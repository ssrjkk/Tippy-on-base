"""Base network layer: USDC balance, deposit scanning, withdrawals.

This module is the bot's single chain facade. It keeps the battle-tested
monolithic helpers used by handlers/web/tests (which patch `base.w3` /
`base.usdc` directly), and re-exports the layered `bot.chain.*` toolkit
(`base.core`, `base.prices`, basename/price/DEX readers, ...) so callers and
tests can use the richer primitives. See bot/chain/__init__.py.
"""

import asyncio
import logging
import time
from decimal import ROUND_CEILING, Decimal

from eth_account.messages import encode_defunct
from eth_typing import ChecksumAddress
from web3 import Web3

from . import config
from .chain import (  # noqa: F401  (re-exported below as facade surface)
    basenames,
    core,
    dex,
    network,
    prices,
    tokens,
    transactions,
    transfers,
)
from .chain.transfers import _send_lock  # shared hot-wallet send lock
from .ledger import ledger

log = logging.getLogger("tipbot.base")

# ---------------------------------------------------------------------------
# RPC provider with automatic failover
# ---------------------------------------------------------------------------
# Primary RPC from config
_PRIMARY_RPC = config.BASE_RPC_URL

# Optional fallback RPCs: comma-separated URLs in BASE_RPC_FALLBACK_URLS.
_RPC_FALLBACKS: list[str] = [
    u.strip() for u in (getattr(config, "BASE_RPC_FALLBACK_URLS", "") or "").split(",") if u.strip()
]

_ALL_RPC_URLS = [_PRIMARY_RPC, *_RPC_FALLBACKS]


def _make_w3(url: str) -> Web3:
    """Create a Web3 instance with timeout."""
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": config.RPC_TIMEOUT_SECONDS}))


# Build provider list for failover
_w3_providers = [_make_w3(u) for u in _ALL_RPC_URLS]
w3 = _w3_providers[0]

# Primary contract handles (bound to primary provider)
HOT_WALLET = Web3.to_checksum_address(w3.eth.account.from_key(config.HOT_WALLET_KEY).address)
USDC = Web3.to_checksum_address(config.USDC_ADDRESS)
usdc = w3.eth.contract(address=USDC, abi=config.ERC20_ABI)


def _rpc_call(fn, *args, **kwargs):
    """Try `fn` on primary RPC, then fall back to alternatives.

    Returns the result on success, raises the last exception if all fail.
    """
    last_err = None
    for provider in _w3_providers:
        try:
            contract = provider.eth.contract(address=USDC, abi=config.ERC20_ABI)
            return fn(contract, *args, **kwargs)
        except Exception as e:
            last_err = e
            log.debug("RPC %s failed: %s", provider.provider.endpoint_url, e)
            continue
    raise RuntimeError(f"all RPC providers failed: {last_err}")


# NOTE: hot-wallet sends (withdrawals here, gas drips in chain.transfers,
# owner ops in outcome.py) all share transfers._send_lock so two paths can
# never read the same pending nonce concurrently — one lock per module was
# not enough: different locks protected the SAME EOA nonce sequence.


class BroadcastUncertainError(Exception):
    """send_raw_transaction could not confirm whether the tx was broadcast.

    The tx hash was pre-computed from the signed payload BEFORE broadcast, so
    even on a thrown error we know the potential hash. Callers must NEVER
    auto-refund on this error: the tx may have landed in the mempool and later
    confirmed, in which case a refund would double-pay the user. Record the
    hash and let the pending-withdraw watcher settle from the real receipt.
    """

    def __init__(self, tx_hash: str):
        super().__init__(f"broadcast result unknown for tx {tx_hash}")
        self.tx_hash = tx_hash


# Address "0x0000...0000" = mint events; "0x0000...0001" = burn (weird EdgeCase)
EDGE_1 = Web3.to_checksum_address("0x0000000000000000000000000000000000000001")

_transfer_topic = w3.keccak(text="Transfer(address,address,uint256)").hex()


def hot_wallet() -> ChecksumAddress:
    return HOT_WALLET


def _hot_balance_sync() -> float:
    try:
        micro = usdc.functions.balanceOf(HOT_WALLET).call()
    except Exception:
        # Fallback: try all providers
        micro = _rpc_call(lambda c: c.functions.balanceOf(HOT_WALLET).call())
    return micro / 10**config.USDC_DECIMALS


async def hot_balance() -> float:
    """Async: hot-wallet USDC balance (off the event loop)."""
    return await asyncio.to_thread(_hot_balance_sync)


def _tx_info_sync(tx_hash: str) -> dict | None:
    """Fetch a transaction and decode a USDC transfer out of its input data.

    Returns {'hash', 'from', 'to', 'status', 'value_micro', 'usdc_to'} —
    value/usdc_to are None when the tx is not a plain USDC transfer, status
    is None while the tx is not mined yet.
    """
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception:
        log.warning("get_transaction(%s) failed", tx_hash, exc_info=True)
        return None
    receipt = None
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        pass  # not mined yet
    out = {
        "hash": tx_hash,
        "from": str(tx["from"]),
        "to": str(tx["to"]) if tx["to"] else None,
        "status": bool(receipt.get("status")) if receipt is not None else None,
        "value_micro": None,
        "usdc_to": None,
    }
    # decode transfer(address,uint256) selector 0xa9059cbb
    raw = tx.get("input") or b""
    data = bytes(raw).hex() if isinstance(raw, (bytes, bytearray)) else str(raw)
    data = data.lower().removeprefix("0x")
    if data.startswith("a9059cbb") and len(data) >= 8 + 64 * 2 and out["to"] and out["to"].lower() == USDC.lower():
        to_addr = "0x" + data[8 + 24 : 8 + 64][-40:]
        value = int(data[8 + 64 : 8 + 128], 16)
        out["usdc_to"] = Web3.to_checksum_address(to_addr)
        out["value_micro"] = value
    return out


async def tx_info(tx_hash: str) -> dict | None:
    """Async: decode a transaction (off the event loop)."""
    return await asyncio.to_thread(_tx_info_sync, tx_hash)


def _vault_balance_sync() -> float | None:
    """On-chain USDC held by the TipBotVault treasury, or None if not deployed.

    This is the on-chain proof-of-reserves: anyone can re-verify it directly
    on Base (USDC.balanceOf(vault) == totalReserves()).
    """
    if not config.VAULT_ADDRESS:
        return None
    try:
        micro = usdc.functions.balanceOf(Web3.to_checksum_address(config.VAULT_ADDRESS)).call()
    except Exception:
        micro = _rpc_call(lambda c: c.functions.balanceOf(Web3.to_checksum_address(config.VAULT_ADDRESS)).call())
    return micro / 10**config.USDC_DECIMALS


async def vault_balance() -> float | None:
    """Async: on-chain vault balance (off the event loop)."""
    return await asyncio.to_thread(_vault_balance_sync)


def _scan_deposits(from_block: int, to_block: int) -> list[dict]:
    """Return incoming USDC transfers to the hot wallet.

    Tries the primary RPC first; on failure, falls back to BASE_RPC_FALLBACK_URLS
    in order.  This prevents a single provider outage from silently dropping
    deposits.
    """
    filter_params = {
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": USDC,
        "topics": [_transfer_topic, None, f"0x{'0'*24}{HOT_WALLET[2:].lower()}"],
    }
    last_err = None
    providers = [w3] + [
        Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": config.RPC_TIMEOUT_SECONDS}))
        for url in _RPC_FALLBACKS
    ]
    for provider in providers:
        try:
            logs = provider.eth.get_logs(filter_params)
            break
        except Exception as e:
            last_err = e
            continue
    else:
        raise RuntimeError(f"all RPC providers failed for get_logs: {last_err}")
    out = []
    for log in logs:
        try:
            event = usdc.events.Transfer().process_log(log)
        except Exception:
            continue
        args = event["args"]
        if args["from"].lower() == EDGE_1.lower():
            continue  # skip mints
        out.append(
            {
                "sender": args["from"],
                "amount_micro": args["value"],
                "tx_hash": "0x" + log["transactionHash"].hex(),
            }
        )
    return out


def _recover_signer_sync(message: str, signature: str) -> str:
    """Recover the address that signed `message` (ETH personal_sign)."""
    return w3.eth.account.recover_message(
        encode_defunct(text=message), signature=signature
    )


async def recover_signer(message: str, signature: str) -> str:
    """Async: recover signer (off the event loop)."""
    return await asyncio.to_thread(_recover_signer_sync, message, signature)


def withdraw_fee(amount_micro: int) -> int:
    """Withdrawal fee (config, default 1%), at least 1 micro-unit."""
    fee = (Decimal(amount_micro) * config.WITHDRAW_FEE_PCT).to_integral_value(rounding=ROUND_CEILING)
    return max(int(fee), 1)


def _poll_deposits_sync() -> list[dict]:
    """One sweep: record new deposits; auto-credit ones from linked wallets.

    Returns a list of newly credited deposits so the caller can notify users:
    [{"tg_id", "amount_micro", "tx_hash"}, ...].

    - Cold start (no checkpoint): backfills DEPOSIT_SCAN_LOOKBACK_BLOCKS so
      deposits that arrived during downtime are not missed.
    - Steady state: the newest DEPOSIT_CONFIRM_BLOCKS are re-scanned on every
      sweep, so a chain reorg can't make us skip a deposit.
    - record_pending / claim_for_sender are idempotent (tx_hash PK, claimed=0),
      so overlapping scans never double-credit.
    """
    credited: list[dict] = []
    current = w3.eth.block_number
    last = ledger.last_block()
    if last == 0:
        last = current - config.DEPOSIT_SCAN_LOOKBACK_BLOCKS
    if current <= last:
        return credited
    start = max(1, min(last + 1, current - config.DEPOSIT_CONFIRM_BLOCKS))
    # Public RPCs reject wide eth_getLogs ranges with 413, so walk the gap in
    # bounded chunks, checkpointing after each one. Idempotency of
    # record_pending/claim_for_sender makes re-scans safe.
    max_end = current
    swept = 0
    while start <= max_end and swept < config.DEPOSIT_SCAN_MAX_CHUNKS_PER_SWEEP:
        chunk_end = min(start + config.DEPOSIT_SCAN_CHUNK_BLOCKS - 1, max_end)
        for dep in _scan_deposits(start, chunk_end):
            if ledger.x402_paid(dep["tx_hash"]):
                continue  # already credited via /api/x402 — never double-credit
            ledger.record_pending(dep["tx_hash"], dep["sender"], dep["amount_micro"])
            owner = ledger.tg_id_of_address(dep["sender"])
            if owner:
                for c in ledger.claim_for_sender(owner, dep["sender"]):
                    credited.append(
                        {
                            "tg_id": owner,
                            "amount_micro": c["amount_micro"],
                            "tx_hash": c["tx_hash"],
                        }
                    )
        ledger.set_last_block(chunk_end)
        start = chunk_end + 1
        swept += 1
    return credited


async def poll_deposits() -> list[dict]:
    """Async: run one deposit sweep off the event loop."""
    return await asyncio.to_thread(_poll_deposits_sync)


def _check_pending_withdrawn_sync() -> None:
    """Refund withdrawals that never confirmed (stuck/replaced/reverted/crash).

    - status NULL (legacy rows) with a tx_hash were recorded after a successful
      send -> mark done without a receipt check (never refund a paid tx).
    - 'pending' with tx_hash=NULL = crash between debit-commit and send.
    - 'pending' with a receipt status=0 (reverted) -> refund immediately.
    - 'pending' still not mined after WITHDRAW_STUCK_TIMEOUT_SECONDS -> refund.
    """
    now = int(time.time())
    for row in ledger.pending_withdraws():
        wd_id = int(row["id"])
        amount_micro = int(row["amount"])
        fee_micro = 0
        if row.get("note") and row["note"].startswith("fee="):
            try:
                fee_micro = int(row["note"].split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        if fee_micro == 0:
            fee_micro = withdraw_fee(amount_micro)
        total_micro = amount_micro + fee_micro
        status = row["status"]
        tx_hash = row["tx_hash"]
        if status is None:
            ledger.mark_withdraw_done(wd_id, tx_hash or "")
            continue
        if tx_hash is None:
            if now - int(row["created_at"]) > config.WITHDRAW_STUCK_TIMEOUT_SECONDS:
                ledger.refund_withdraw(wd_id, int(row["tg_id"]), total_micro)
            continue
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            rpc_ok = True
        except Exception:
            log.warning("get_transaction_receipt(%s) failed", tx_hash, exc_info=True)
            receipt = None
            rpc_ok = False
        if receipt is None:
            if not rpc_ok:
                # RPC unreachable: we cannot determine the tx state. Do NOT
                # refund — a successful on-chain tx would otherwise be
                # double-paid (user keeps the funds and gets a refund). Leave
                # the row pending; the next sweep re-checks once RPC recovers.
                continue
            if now - int(row["created_at"]) > config.WITHDRAW_STUCK_TIMEOUT_SECONDS:
                ledger.refund_withdraw(wd_id, int(row["tg_id"]), total_micro)
        elif bool(receipt.get("status")):
            ledger.mark_withdraw_done(wd_id, tx_hash)
        else:
            ledger.refund_withdraw(wd_id, int(row["tg_id"]), total_micro)


async def check_pending_withdraws() -> None:
    """Async: run the pending-withdrawal refund sweep off the event loop."""
    await asyncio.to_thread(_check_pending_withdrawn_sync)


def _send_usdc_sync(to_address: str, amount_micro: int) -> str:
    """Internal sync send USDC from hot wallet. Returns tx hash. Raises on failure.

    Serialized by a process lock so two concurrent withdrawals never pick the
    same nonce (which would silently replace one tx with the other).
    """
    acct = w3.eth.account.from_key(config.HOT_WALLET_KEY)
    with _send_lock:
        nonce = w3.eth.get_transaction_count(HOT_WALLET, "pending")
        base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
        # Priority tip 0.01 gwei (Base's practical floor; 0.001 gwei can be too
        # low and leave a withdrawal stuck until the watcher refunds it). The
        # max fee leaves ~2x headroom over the current base fee so a short fee
        # spike right after building the tx doesn't orphan it.
        priority = w3.to_wei("0.01", "gwei")
        max_fee = base_fee * 2 + priority
        tx = usdc.functions.transfer(
            Web3.to_checksum_address(to_address), amount_micro
        ).build_transaction(
            {
                "from": HOT_WALLET,
                "nonce": nonce,
                "maxPriorityFeePerGas": priority,
                "maxFeePerGas": max_fee,
                "chainId": w3.eth.chain_id,
            }
        )
        signed = acct.sign_transaction(tx)
        # Pre-compute the tx hash before broadcast: it is deterministically
        # derivable from the signed payload. If broadcast then throws (timeout,
        # connection drop) we still KNOW the potential hash — without this, a
        # late-confirming tx would be double-paid by an immediate refund.
        tx_hash = "0x" + Web3.keccak(signed.raw_transaction).hex()
        try:
            raw = w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception:
            raise BroadcastUncertainError(tx_hash) from None
        return "0x" + raw.hex()


async def send_usdc(to_address: str, amount_micro: int) -> str:
    """Async wrapper: send USDC from hot wallet without blocking the event loop.

    Runs the synchronous web3 transaction building + signing + sending in a
    separate thread via asyncio.to_thread, so the bot's event loop stays
    responsive during the ~10s RPC call.
    """
    return await asyncio.to_thread(_send_usdc_sync, to_address, amount_micro)


async def kick_expired_channel_subscriptions(bot) -> int:
    """Kick users whose paid channel access expired. Returns the number kicked.

    A subscription row is dropped ONLY when the user is provably gone (left,
    kicked, chat deleted) or is an admin of the channel. If the bot lost
    admin rights (or the network failed) the row is kept and the kick retries
    next cycle — otherwise a dropped row would leave the user inside the
    channel for free forever, and a re-purchase would silently re-arm access
    they already had.
    """
    from aiogram.exceptions import TelegramBadRequest

    now = time.time()
    kicked = 0
    for sub in ledger.active_channel_subscriptions():
        if int(sub["expires_at"]) > now:
            continue
        chat_id, tg_id = int(sub["chat_id"]), int(sub["tg_id"])
        try:
            member = await bot.get_chat_member(chat_id, tg_id)
            if member.status in ("administrator", "creator"):
                ledger.expire_channel_subscription(chat_id, tg_id)
                continue
        except Exception:
            pass  # probe may fail — the ban itself decides below
        try:
            await bot.ban_chat_member(chat_id, tg_id)
            await bot.unban_chat_member(chat_id, tg_id)
        except TelegramBadRequest as e:
            msg = str(getattr(e, "message", ""))
            if ("not found" in msg or "NOT_PARTICIPANT" in msg
                    or "chat not found" in msg or "CHAT_NOT_FOUND" in msg):
                # the user is no longer in the channel (or it is gone)
                ledger.expire_channel_subscription(chat_id, tg_id)
            # otherwise (bot lost admin rights, ...) — keep the row, retry later
            continue
        except Exception:
            continue  # network hiccup — keep the row, retry later
        kicked += 1
        ledger.expire_channel_subscription(chat_id, tg_id)
        try:
            await bot.send_message(
                tg_id,
                "🔑 Подписка на канал истекла, доступ закрыт.\n"
                "Продлить: /paywall channels",
            )
        except Exception:
            pass
    return kicked


# ---------------------------------------------------------------------------
# bot.chain facade surface
#
# Re-export the layered chain toolkit so callers can reach both the plain
# module objects (`base.core`, `base.prices`, ...) for mocking/reads and the
# ready-made sync primitives (`base.assert_base_chain_sync`, `base.namehash`,
# `base.feed_price_sync`, ...). The monolithic helpers above are kept
# untouched for the handlers and existing tests that patch `base.w3`/`base.usdc`.
# ---------------------------------------------------------------------------

# The sync functions read the *chain.core* provider (`base.core.w3`), so tests
# patch `base.core.w3` / `base.core._contract_read` / `base.prices._feed_read`.
assert_base_chain_sync = network.assert_base_chain_sync
nonce_sync = network.nonce_sync
is_contract_sync = network.is_contract_sync
get_block_sync = network.get_block_sync
eip1559_fees_sync = network.eip1559_fees_sync
eip1559_fees = network.eip1559_fees
clear_network_caches = network.clear_network_caches

wait_for_tx_sync = transactions.wait_for_tx_sync
tx_status = transactions.tx_status
wait_for_tx = transactions.wait_for_tx

_send_token_sync = transfers._send_token_sync
_approve_token_sync = transfers._approve_token_sync
_send_eth_sync = transfers._send_eth_sync
send_usdc_sync = transfers._send_usdc_sync

token_meta_sync = tokens.token_meta_sync
_token_meta_cache = tokens._token_meta_cache
token_balance_sync = tokens.token_balance_sync
token_allowance_sync = tokens.token_allowance_sync
erc20_total_supply_sync = tokens.erc20_total_supply_sync

feed_price_sync = prices.feed_price_sync
l2_sequencer_ok_sync = prices.l2_sequencer_ok_sync
price_cache_clear = prices.price_cache_clear


def get_eth_price_usd_sync() -> float | None:
    """ETH/USD from the Chainlink feed on Base. None if unavailable/stale."""
    return feed_price_sync(config.CHAINLINK_ETH_USD_FEED)


def get_usdc_price_usd_sync() -> float | None:
    """USDC/USD from the Chainlink feed on Base (~1.00; deviation is a red flag)."""
    return feed_price_sync(
        config.CHAINLINK_USDC_USD_FEED,
        max_age_seconds=config.USDC_PRICE_FEED_MAX_AGE_SECONDS,
    )

namehash = basenames.namehash
is_basename = basenames.is_basename
resolve_basename_sync = basenames.resolve_basename_sync
reverse_basename_sync = basenames.reverse_basename_sync
basename_available_sync = basenames.basename_available_sync

aerodrome_quote_sync = dex.aerodrome_quote_sync
usdc_to_eth_quote_sync = dex.usdc_to_eth_quote_sync
usdc_to_eth_quote = dex.usdc_to_eth_quote_sync

hot_wallet_chain = core.hot_wallet
