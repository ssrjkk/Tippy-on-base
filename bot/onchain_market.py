"""On-chain LMSR market trading via user's own wallet (self-custody).

Unlike bot/base.py (hot-wallet-only), each transaction is signed by the
user's own private key. Off-chain estimation uses on-chain view functions;
on-chain buy/sell/redeem is the source of truth with slippage protection.

Requires: OUTCOME_MARKET_ADDRESS, WALLET_ENC_KEY in config. Web3.py is
already in requirements.txt.
"""
import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path

from web3 import Web3

from . import config

# Shared hot-wallet send lock (gas drips go through chain.transfers).
from .chain.transfers import _send_lock  # noqa: F401

# Gas-drip anti-griefing: one drip per user wallet per cooldown window no
# matter how many buy/create attempts they trigger, PLUS a global per-UTC-day
# budget — an attacker with 100 wallets must not be able to farm the hot
# wallet's ETH even at one drip each.
_DRIP_COOLDOWN_SECONDS = 3600
_last_drip: dict[str, float] = {}

# Short-lived read caches: priceOf / markets() are state-changing only when a
# trade lands, so a dashboard or mini-app refresh a few seconds later can reuse
# the snapshot. Bounds cache growth instead of letting an unauthenticated
# /api/onchain/* fan-out multiply RPC calls per request forever.
_READ_CACHE_TTL_SECONDS = 3.0
_READ_CACHE_MAX = 256
_market_info_cache: dict[int, tuple[float, dict]] = {}
_prices_cache: dict[tuple[int, int], tuple[float, list[Decimal]]] = {}


def _cache_read(cache: dict, key: tuple) -> object | None:
    hit = cache.get(key)
    if hit is None:
        return None
    ts, value = hit
    if time.time() - ts <= _READ_CACHE_TTL_SECONDS:
        return value
    cache.pop(key, None)
    return None


def _cache_write(cache: dict, key: tuple, value) -> None:
    if len(cache) >= _READ_CACHE_MAX:
        cache.clear()  # crude bound; entries repopulate on demand
    cache[key] = (time.time(), value)


def _check_chain() -> None:
    """Refuse to build/sign anything if the RPC is not the expected chain."""
    from .chain.network import assert_base_chain_sync

    assert_base_chain_sync()


def _eip1559_fee_fields(w3: Web3) -> dict:
    """EIP-1559 (type-2) fee fields for user-signed txs.

    On Base legacy `gasPrice` txs can get stuck in the mempool when the base
    fee spiked after the read — type-2 txs with a 2x cap don't. Block-data
    misses fall back to the legacy RPC gas price as `maxFeePerGas`.
    """
    from .chain.network import eip1559_fees_sync

    fees = eip1559_fees_sync(priority_gwei=0.01)
    if fees["base_fee_gwei"] > 0:
        return {
            "maxPriorityFeePerGas": Web3.to_wei(fees["priority_gwei"], "gwei"),
            "maxFeePerGas": Web3.to_wei(fees["max_fee_gwei"], "gwei"),
        }
    gp = w3.eth.gas_price
    return {
        "maxPriorityFeePerGas": Web3.to_wei(1, "gwei"),
        "maxFeePerGas": gp,
    }


async def _ensure_gas(w3: Web3, user_addr: str, needed_wei: int) -> None:
    """Drip gas from the hot wallet if `user_addr` cannot pay for a tx.

    Two limits: a per-wallet cooldown (in-memory, short-lived) and a global
    per-UTC-day budget persisted in the DB — the budget survives bot
    restarts, so a restart cannot be used to re-arm a drained budget.
    """
    if await asyncio.to_thread(
        lambda: w3.eth.get_balance(Web3.to_checksum_address(user_addr))
    ) >= needed_wei:
        return
    from bot.ledger import async_ledger as ledger

    daily_max = int(getattr(config, "GAS_DRIP_DAILY_MAX", 50))
    now = time.monotonic()
    last = _last_drip.get(user_addr.lower(), 0.0)
    if now - last < _DRIP_COOLDOWN_SECONDS:
        raise RuntimeError("gas top-up cooldown — try again in an hour")
    drip_wei = int(config.GAS_DRIP_ETH * Decimal(10**18))
    if drip_wei <= 0:
        raise RuntimeError("on-chain gas top-up disabled (GAS_DRIP_ETH=0)")
    from .chain.transfers import send_eth

    # Book FIRST (atomically), drip second: an over-booking on a failed send
    # wastes a drip slot, but an under-book after a successful send would
    # let concurrent requests exceed the budget. Book-then-send also closes
    # the send-then-count race between processes.
    if not await ledger.try_book_gas_drip(daily_max):
        raise RuntimeError("daily gas top-up budget exhausted — try tomorrow")
    await send_eth(user_addr, drip_wei)
    _last_drip[user_addr.lower()] = now

# Minimal ERC20 ABI — only the functions we need (approve/allowance/balanceOf).
_ERC20_EXTRAS_ABI = json.loads("""[
    {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

_ABI_PATH = Path(__file__).parent / "abi" / "outcome_market_abi.json"
_OUTCOME_MARKET_ABI: list | None = None


def _load_abi() -> list:
    global _OUTCOME_MARKET_ABI
    if _OUTCOME_MARKET_ABI is None:
        if not _ABI_PATH.exists():
            raise FileNotFoundError(f"OutcomeMarket ABI not found at {_ABI_PATH}")
        _OUTCOME_MARKET_ABI = json.loads(_ABI_PATH.read_text())
    return _OUTCOME_MARKET_ABI


def _w3() -> Web3:
    from .chain import core
    # Use the project-standard provider factory so the HTTP request carries a
    # timeout (RPC_TIMEOUT_SECONDS). Without it, a hung RPC blocks the single
    # event-loop thread forever and freezes every concurrent money-path watcher
    # (deposit scanner, pending-withdrawal refund, batch flush, CREATE2/x402
    # sweeps). Same timed provider the rest of the codebase uses.
    return core._make_w3(core.rpc_url())


def _market_contract(w3: Web3 | None = None):
    if not config.OUTCOME_MARKET_ADDRESS:
        raise RuntimeError("OUTCOME_MARKET_ADDRESS not configured")
    if w3 is None:
        w3 = _w3()
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.OUTCOME_MARKET_ADDRESS),
        abi=_load_abi(),
    )


def _usdc_contract(w3: Web3):
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.USDC_ADDRESS),
        abi=_ERC20_EXTRAS_ABI,
    )


async def market_state(market_id: int) -> tuple[int, int, int]:
    """Return (q_total, b, n) for the on-chain market.

    q_total = sum of outstanding shares across outcomes, read from
    ERC1155Supply.totalSupply per token id.
    """
    w3 = _w3()
    contract = _market_contract(w3)

    def _read() -> tuple[int, int, int]:
        m = contract.functions.markets(market_id).call()
        num_outcomes = m[0]  # uint8 numOutcomes
        b = m[4]             # int256 b
        # ERC1155Supply tracks total supply in a dedicated mapping — minting
        # credits traders, never the zero address, so balanceOf(0x0) is NOT the
        # supply (that mistake silently made this return q=[0,...] forever).
        supplies = [contract.functions.totalSupply(market_id * 256 + i).call()
                    for i in range(num_outcomes)]
        return sum(supplies), b, num_outcomes

    total_q, b, num_outcomes = await asyncio.to_thread(_read)
    return total_q, b, num_outcomes


async def creator_holds_outcome(market_id: int, outcome: int) -> bool:
    """True if the market's creator still holds shares of `outcome` on-chain.

    Mirrors the off-chain resolve guard (ledger.resolve_market): a creator
    declaring an outcome they themselves hold could mint a payout, so the
    relayer must refuse to sign such a resolution.
    """
    w3 = _w3()
    contract = _market_contract(w3)

    def _read() -> bool:
        m = contract.functions.markets(market_id).call()
        creator = m[5]  # Market.creator
        if not creator or creator == "0x" + "0" * 40:
            return False
        try:
            held = contract.functions.balanceOf(
                Web3.to_checksum_address(creator), market_id * 256 + outcome
            ).call()
            return int(held) > 0
        except Exception:
            return False

    return await asyncio.to_thread(_read)


async def price_of(market_id: int, outcome: int) -> Decimal:
    """Current price of one share of `outcome` (0..1 scale) via on-chain view."""
    w3 = _w3()
    contract = _market_contract(w3)

    def _read() -> int:
        return contract.functions.priceOf(market_id, outcome).call()

    price18 = await asyncio.to_thread(_read)
    return Decimal(price18) / Decimal(10**18)


async def market_prices(market_id: int, num_outcomes: int) -> list[Decimal]:
    """Live LMSR prices for every outcome (0..1 scale), one RPC batch.

    Cached for a few seconds; market data only changes when a trade lands,
    so this keeps dashboard refreshes off a per-request RPC fan-out.
    """
    key = (market_id, num_outcomes)
    cached = _cache_read(_prices_cache, key)
    if cached is not None:
        return cached
    w3 = _w3()

    def _call():
        c = _market_contract(w3)
        return [c.functions.priceOf(market_id, i).call() for i in range(num_outcomes)]

    raw = await asyncio.to_thread(_call)
    prices = [Decimal(p) / Decimal(10**18) for p in raw]
    _cache_write(_prices_cache, key, prices)
    return prices


async def quote_buy(market_id: int, outcome: int, shares: int) -> int:
    """On-chain quote: how many micro-USDC `shares` would cost right now."""
    w3 = _w3()
    contract = _market_contract(w3)

    def _read() -> int:
        return contract.functions.quoteBuy(market_id, outcome, shares).call()

    return await asyncio.to_thread(_read)


async def quote_sell(market_id: int, outcome: int, shares: int) -> int:
    """On-chain quote: how many micro-USDC `shares` would yield when sold."""
    w3 = _w3()
    contract = _market_contract(w3)

    def _read() -> int:
        return contract.functions.quoteSell(market_id, outcome, shares).call()

    return await asyncio.to_thread(_read)


async def buy(market_id: int, outcome: int, shares: int, max_cost_micro: int,
              user_private_key: str) -> str:
    """On-chain buy: drip gas -> approve -> buy. Returns tx hash.

    `shares`: exact number of shares to buy.
    `max_cost_micro`: slippage cap — tx reverts if cost exceeds this.
    """
    _check_chain()
    needed_wei = int(Decimal("0.0003") * Decimal(10**18))
    w3 = _w3()
    account = w3.eth.account.from_key(user_private_key)
    user_addr = account.address

    # 1) Ensure user has gas for approve + buy (rate-limited drip)
    await _ensure_gas(w3, user_addr, needed_wei)

    # 2) + 3) Full send path off the event loop so a slow/hung RPC never
    # freezes the single event-loop thread (all concurrent watchers depend on
    # it). Mirrors smart_wallet's *_sync + asyncio.to_thread pattern.
    return await asyncio.to_thread(
        _buy_sync, market_id, outcome, shares, max_cost_micro,
        user_private_key, user_addr, w3,
    )


def _buy_sync(market_id: int, outcome: int, shares: int, max_cost_micro: int,
              user_private_key: str, user_addr: str, w3: Web3) -> str:
    """Blocking half of :func:`buy` (runs in a worker thread)."""
    # 2) Ensure USDC approval
    usdc = _usdc_contract(w3)
    current_allowance = usdc.functions.allowance(
        user_addr, config.OUTCOME_MARKET_ADDRESS
    ).call()
    if current_allowance < max_cost_micro:
        approve_tx = usdc.functions.approve(
            Web3.to_checksum_address(config.OUTCOME_MARKET_ADDRESS), max_cost_micro
        ).build_transaction({
            "from": user_addr,
            "nonce": w3.eth.get_transaction_count(user_addr, "pending"),
            "gas": 60000,
            **_eip1559_fee_fields(w3),
        })
        signed = w3.eth.account.sign_transaction(approve_tx, private_key=user_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

    # 3) Execute buy
    contract = _market_contract(w3)
    tx = contract.functions.buy(
        market_id, outcome, shares, max_cost_micro
    ).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr, "pending"),
        "gas": 300000,
        **_eip1559_fee_fields(w3),
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=user_private_key)
    with _send_lock:
        raw_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash = raw_hash.hex() if isinstance(raw_hash, bytes) else raw_hash
    receipt = w3.eth.wait_for_transaction_receipt(raw_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"buy reverted: {tx_hash.hex()}")
    return tx_hash.hex()


async def sell(market_id: int, outcome: int, shares: int,
               min_proceeds_micro: int, user_private_key: str) -> str:
    """On-chain sell. Returns tx hash.

    `shares`: number of shares to sell.
    `min_proceeds_micro`: slippage floor — tx reverts if proceeds are below.
    """
    _check_chain()
    w3 = _w3()
    account = w3.eth.account.from_key(user_private_key)
    user_addr = account.address
    return await asyncio.to_thread(
        _sell_sync, market_id, outcome, shares, min_proceeds_micro,
        user_private_key, user_addr, w3,
    )


def _sell_sync(market_id: int, outcome: int, shares: int, min_proceeds_micro: int,
               user_private_key: str, user_addr: str, w3: Web3) -> str:
    """Blocking half of :func:`sell` (runs in a worker thread)."""
    contract = _market_contract(w3)
    tx = contract.functions.sell(
        market_id, outcome, shares, min_proceeds_micro
    ).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr, "pending"),
        "gas": 300000,
        **_eip1559_fee_fields(w3),
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=user_private_key)
    with _send_lock:
        raw_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash = raw_hash.hex() if isinstance(raw_hash, bytes) else raw_hash
    receipt = w3.eth.wait_for_transaction_receipt(raw_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"sell reverted: {tx_hash.hex()}")
    return tx_hash.hex()


async def redeem(market_id: int, user_private_key: str) -> int:
    """Redeem winning shares after resolution. Returns payout in micro-USDC."""
    _check_chain()
    w3 = _w3()
    account = w3.eth.account.from_key(user_private_key)
    user_addr = account.address
    return await asyncio.to_thread(
        _redeem_sync, market_id, user_private_key, user_addr, w3,
    )


def _redeem_sync(market_id: int, user_private_key: str, user_addr: str, w3: Web3) -> int:
    """Blocking half of :func:`redeem` (runs in a worker thread)."""
    contract = _market_contract(w3)
    tx = contract.functions.redeem(market_id).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr, "pending"),
        "gas": 200000,
        **_eip1559_fee_fields(w3),
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=user_private_key)
    with _send_lock:
        raw_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash = raw_hash.hex() if isinstance(raw_hash, bytes) else raw_hash
    receipt = w3.eth.wait_for_transaction_receipt(raw_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"redeem reverted: {tx_hash}")
    payout = 0
    from . import base
    for log in receipt.get("logs", []):
        if log.get("address", "").lower() == config.USDC_ADDRESS.lower():
            try:
                ev = base.usdc.events.Transfer().process_log(log)
                if ev["args"]["to"].lower() == user_addr.lower():
                    payout += int(ev["args"]["value"])
            except Exception:
                pass
    return payout


async def redeem_many(market_ids: list[int], user_private_key: str) -> int:
    """Batch redeem winnings from multiple resolved markets. Returns total payout in micro-USDC."""
    _check_chain()
    w3 = _w3()
    account = w3.eth.account.from_key(user_private_key)
    user_addr = account.address
    return await asyncio.to_thread(
        _redeem_many_sync, market_ids, user_private_key, user_addr, w3,
    )


def _redeem_many_sync(market_ids: list[int], user_private_key: str,
                      user_addr: str, w3: Web3) -> int:
    """Blocking half of :func:`redeem_many` (runs in a worker thread)."""
    contract = _market_contract(w3)
    tx = contract.functions.redeemMany(market_ids).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr, "pending"),
        "gas": 200000 * len(market_ids),
        **_eip1559_fee_fields(w3),
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=user_private_key)
    with _send_lock:
        raw_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash = raw_hash.hex() if isinstance(raw_hash, bytes) else raw_hash
    receipt = w3.eth.wait_for_transaction_receipt(raw_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"redeemMany reverted: {tx_hash}")
    payout = 0
    from . import base
    for log in receipt.get("logs", []):
        if log.get("address", "").lower() == config.USDC_ADDRESS.lower():
            try:
                ev = base.usdc.events.Transfer().process_log(log)
                if ev["args"]["to"].lower() == user_addr.lower():
                    payout += int(ev["args"]["value"])
            except Exception:
                pass
    return payout


async def create_market(num_outcomes: int, subsidy_micro: int, closes_at: int,
                         creator_private_key: str) -> int:
    """Create a new on-chain market. Returns market_id."""
    _check_chain()
    w3 = _w3()
    account = w3.eth.account.from_key(creator_private_key)
    user_addr = account.address

    # Ensure creator has gas (rate-limited drip)
    needed_wei = int(Decimal("0.0005") * Decimal(10**18))
    await _ensure_gas(w3, user_addr, needed_wei)

    return await asyncio.to_thread(
        _create_market_sync, num_outcomes, subsidy_micro, closes_at,
        creator_private_key, user_addr, w3,
    )


def _create_market_sync(num_outcomes: int, subsidy_micro: int, closes_at: int,
                        creator_private_key: str, user_addr: str, w3: Web3) -> int:
    """Blocking half of :func:`create_market` (runs in a worker thread)."""
    # Approve USDC transfer for subsidy
    usdc = _usdc_contract(w3)
    current_allowance = usdc.functions.allowance(
        user_addr, config.OUTCOME_MARKET_ADDRESS
    ).call()
    if current_allowance < subsidy_micro:
        approve_tx = usdc.functions.approve(
            Web3.to_checksum_address(config.OUTCOME_MARKET_ADDRESS), subsidy_micro
        ).build_transaction({
            "from": user_addr,
            "nonce": w3.eth.get_transaction_count(user_addr, "pending"),
            "gas": 60000,
            **_eip1559_fee_fields(w3),
        })
        signed = w3.eth.account.sign_transaction(approve_tx, private_key=creator_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

    contract = _market_contract(w3)
    tx = contract.functions.createMarket(
        num_outcomes, subsidy_micro, closes_at
    ).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr, "pending"),
        "gas": 500000,
        **_eip1559_fee_fields(w3),
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=creator_private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"createMarket reverted: {tx_hash.hex()}")
    # Parse market_id from MarketCreated event
    market_id = contract.events.MarketCreated().process_receipt(receipt)["args"]["marketId"]
    return market_id


async def oracle_resolve(market_id: int, winning_outcome: int,
                          oracle_private_key: str) -> str:
    """Oracle resolves the market. Returns tx hash."""
    _check_chain()
    w3 = _w3()
    account = w3.eth.account.from_key(oracle_private_key)
    user_addr = account.address
    return await asyncio.to_thread(
        _oracle_resolve_sync, market_id, winning_outcome,
        oracle_private_key, user_addr, w3,
    )


def _oracle_resolve_sync(market_id: int, winning_outcome: int,
                         oracle_private_key: str, user_addr: str, w3: Web3) -> str:
    """Blocking half of :func:`oracle_resolve` (runs in a worker thread)."""
    contract = _market_contract(w3)
    tx = contract.functions.oracleResolve(
        market_id, winning_outcome
    ).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr, "pending"),
        "gas": 100000,
        **_eip1559_fee_fields(w3),
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=oracle_private_key)
    with _send_lock:
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"oracleResolve reverted: {tx_hash.hex()}")
    return tx_hash.hex()


def _resolve_like(contract, w3: Web3, fn_name: str, args: tuple,
                  private_key: str, gas: int) -> str:
    """Shared sign-and-broadcast for ownerResolve/cancelExpired (hot wallet)."""
    account = w3.eth.account.from_key(private_key)
    # Nonce read must happen under the shared hot-wallet lock too: these are
    # sent FROM the bot owner/hot wallet, whose nonce the withdraw/batch/x402
    # paths consume — a nonce read outside the lock could collide and replace
    # one of those payouts.
    with _send_lock:
        tx = getattr(contract.functions, fn_name)(*args).build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "gas": gas,
            **_eip1559_fee_fields(w3),
        })
        signed = account.sign_transaction(tx)
        raw_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(raw_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"{fn_name} reverted: {raw_hash.hex()}")
    return raw_hash.hex()


async def owner_resolve(market_id: int, winning_outcome: int,
                        private_key: str) -> str:
    """Owner resolves directly (fallback when no oracle key is configured)."""
    _check_chain()
    w3 = _w3()
    contract = _market_contract(w3)
    return _resolve_like(contract, w3, "ownerResolve",
                         (market_id, winning_outcome), private_key, 100000)


async def cancel_expired(market_id: int, private_key: str) -> str:
    """Cancel an expired market (>24h past close, no resolution) — anyone may
    call it; refunds unlock at par via claimCancelled."""
    _check_chain()
    w3 = _w3()
    contract = _market_contract(w3)
    return _resolve_like(contract, w3, "cancelExpired",
                         (market_id,), private_key, 200000)


async def market_views(limit: int = 12) -> list[dict]:
    """Dashboard/Mini-App friendly snapshots of registered on-chain markets:
    registry labels + live on-chain prices and resolution state. Markets that
    fail to read (RPC hiccup) are skipped, never break the whole list."""
    from .ledger import async_ledger as ledger

    if not config.OUTCOME_MARKET_ADDRESS:
        return []
    rows = await ledger.list_onchain_markets(limit)

    async def _view(m) -> dict | None:
        options = json.loads(m['options'])
        try:
            prices, info = await asyncio.gather(
                market_prices(int(m['id']), len(options)),
                get_market_info(int(m['id'])),
            )
        except Exception:
            return None  # one stale RPC must not break the whole list
        return {
            'id': int(m['id']),
            'question': m['question'],
            'close_at': m['close_at'],
            'resolved': info.get('resolved'),
            'cancelled': info.get('cancelled'),
            'winner': info.get('winning_outcome'),
            'market_address': config.OUTCOME_MARKET_ADDRESS,
            'options': [
                {'index': i, 'label': o, 'price_pct': float(round(prices[i] * 100, 2))}
                for i, o in enumerate(options)
            ],
        }

    views = await asyncio.gather(*(_view(m) for m in rows))
    return [v for v in views if v is not None]


async def get_market_info(market_id: int) -> dict:
    """Read full market info from chain (off the event loop).

    Cached a few seconds like :func:`market_prices` — resolution/escrow only
    move on-chain, a dashboard poll doesn't need a fresh call per request.
    """
    cached = _cache_read(_market_info_cache, (market_id,))
    if cached is not None:
        return cached
    w3 = _w3()
    contract = _market_contract(w3)

    def _call():
        m = contract.functions.markets(market_id).call()
        return {
            "market_id": market_id,
            "num_outcomes": m[0],
            "resolved": m[1],
            "winning_outcome": m[2],
            "closes_at": m[3],
            "b": m[4],
            "creator": m[5],
            "escrow_micro": m[6],
            "resolved_at": m[7],
            "disputed": m[8],
            "cancelled": m[9],
        }

    info = await asyncio.to_thread(_call)
    _cache_write(_market_info_cache, (market_id,), info)
    return info
