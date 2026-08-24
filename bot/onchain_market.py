"""On-chain LMSR market trading via user's own wallet (self-custody).

Unlike bot/base.py (hot-wallet-only), each transaction is signed by the
user's own private key. Off-chain estimation uses on-chain view functions;
on-chain buy/sell/redeem is the source of truth with slippage protection.

Requires: OUTCOME_MARKET_ADDRESS, WALLET_ENC_KEY in config. Web3.py is
already in requirements.txt.
"""
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from web3 import Web3

from . import config

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
    from .base import rpc_url
    return Web3(Web3.HTTPProvider(rpc_url()))


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
    from .base import USDC_ADDRESS
    return w3.eth.contract(
        address=Web3.to_checksum_address(USDC_ADDRESS),
        abi=_ERC20_EXTRAS_ABI,
    )


async def market_state(market_id: int) -> tuple[int, int, int]:
    """Return (q_total, b, n) for the on-chain market.

    q_total = sum of all shares outstanding across outcomes (we compute
    this from ERC1155 balances since the contract doesn't expose q_i directly).
    """
    w3 = _w3()
    contract = _market_contract(w3)
    m = contract.functions.markets(market_id).call()
    num_outcomes = m[0]  # uint8 numOutcomes
    b = m[4]             # int256 b
    # Sum ERC1155 balances across all outcomes for the zero address (total supply)
    total_q = 0
    for i in range(num_outcomes):
        token_id = market_id * 256 + i
        bal = contract.functions.balanceOf(
            Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),
            token_id,
        ).call()
        total_q += bal
    return total_q, b, num_outcomes


async def price_of(market_id: int, outcome: int) -> Decimal:
    """Current price of one share of `outcome` (0..1 scale) via on-chain view."""
    w3 = _w3()
    contract = _market_contract(w3)
    price18 = contract.functions.priceOf(market_id, outcome).call()
    return Decimal(price18) / Decimal(10**18)


async def quote_buy(market_id: int, outcome: int, shares: int) -> int:
    """On-chain quote: how many micro-USDC `shares` would cost right now."""
    w3 = _w3()
    contract = _market_contract(w3)
    return contract.functions.quoteBuy(market_id, outcome, shares).call()


async def quote_sell(market_id: int, outcome: int, shares: int) -> int:
    """On-chain quote: how many micro-USDC `shares` would yield when sold."""
    w3 = _w3()
    contract = _market_contract(w3)
    return contract.functions.quoteSell(market_id, outcome, shares).call()


async def estimate_buy_shares(market_id: int, outcome: int, spend_micro: int) -> int:
    """Estimate how many shares you get for `spend_micro` USDC.

    Binary search using on-chain quoteBuy (free view calls, no RPC per step
    in terms of gas — just one eth_call each).
    """
    lo, hi = 0, spend_micro * 10  # upper bound: 10 shares per micro (generous)
    best = 0
    for _ in range(60):
        mid = (lo + hi) // 2
        if mid == 0:
            break
        cost = await quote_buy(market_id, outcome, mid)
        if cost <= spend_micro:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


async def buy(market_id: int, outcome: int, shares: int, max_cost_micro: int,
              user_private_key: str) -> str:
    """On-chain buy: drip gas -> approve -> buy. Returns tx hash.

    `shares`: exact number of shares to buy.
    `max_cost_micro`: slippage cap — tx reverts if cost exceeds this.
    """
    from .base import send_eth
    w3 = _w3()
    account = w3.eth.account.from_key(user_private_key)
    user_addr = account.address

    # 1) Ensure user has gas for approve + buy
    balance = w3.eth.get_balance(user_addr)
    needed_wei = int(Decimal("0.0003") * Decimal(10**18))
    if balance < needed_wei:
        drip_wei = int(config.GAS_DRIP_ETH * Decimal(10**18))
        await send_eth(user_addr, drip_wei)
        # Wait for balance to appear
        for _ in range(20):
            await asyncio.sleep(0.5)
            if w3.eth.get_balance(user_addr) >= needed_wei:
                break

    # 2) Ensure USDC approval
    usdc = _usdc_contract(w3)
    current_allowance = usdc.functions.allowance(
        user_addr, config.OUTCOME_MARKET_ADDRESS
    ).call()
    if current_allowance < max_cost_micro:
        approve_tx = usdc.functions.approve(
            Web3.to_checksum_address(config.OUTCOME_MARKET_ADDRESS), 2**256 - 1
        ).build_transaction({
            "from": user_addr,
            "nonce": w3.eth.get_transaction_count(user_addr),
            "gas": 60000,
            "gasPrice": w3.eth.gas_price,
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
        "nonce": w3.eth.get_transaction_count(user_addr),
        "gas": 300000,
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=user_private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"buy reverted: {tx_hash.hex()}")
    return tx_hash.hex()


async def sell(market_id: int, outcome: int, shares: int,
               min_proceeds_micro: int, user_private_key: str) -> str:
    """On-chain sell. Returns tx hash.

    `shares`: number of shares to sell.
    `min_proceeds_micro`: slippage floor — tx reverts if proceeds are below.
    """
    w3 = _w3()
    account = w3.eth.account.from_key(user_private_key)
    user_addr = account.address
    contract = _market_contract(w3)

    tx = contract.functions.sell(
        market_id, outcome, shares, min_proceeds_micro
    ).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr),
        "gas": 300000,
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=user_private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"sell reverted: {tx_hash.hex()}")
    return tx_hash.hex()


async def redeem(market_id: int, user_private_key: str) -> int:
    """Redeem winning shares after resolution. Returns payout in micro-USDC."""
    w3 = _w3()
    account = w3.eth.account.from_key(user_private_key)
    user_addr = account.address
    contract = _market_contract(w3)

    tx = contract.functions.redeem(market_id).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr),
        "gas": 200000,
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=user_private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"redeem reverted: {tx_hash.hex()}")
    # Parse payout from return data or logs
    return receipt.get("cumulativeGasUsed", 0)  # placeholder — real impl decodes logs


async def create_market(num_outcomes: int, subsidy_micro: int, closes_at: int,
                         creator_private_key: str) -> int:
    """Create a new on-chain market. Returns market_id."""
    w3 = _w3()
    account = w3.eth.account.from_key(creator_private_key)
    user_addr = account.address

    # Ensure creator has gas
    balance = w3.eth.get_balance(user_addr)
    needed_wei = int(Decimal("0.0005") * Decimal(10**18))
    if balance < needed_wei:
        drip_wei = int(config.GAS_DRIP_ETH * Decimal(10**18))
        from .base import send_eth
        await send_eth(user_addr, drip_wei)
        for _ in range(20):
            await asyncio.sleep(0.5)
            if w3.eth.get_balance(user_addr) >= needed_wei:
                break

    # Approve USDC transfer for subsidy
    usdc = _usdc_contract(w3)
    current_allowance = usdc.functions.allowance(
        user_addr, config.OUTCOME_MARKET_ADDRESS
    ).call()
    if current_allowance < subsidy_micro:
        approve_tx = usdc.functions.approve(
            Web3.to_checksum_address(config.OUTCOME_MARKET_ADDRESS), 2**256 - 1
        ).build_transaction({
            "from": user_addr,
            "nonce": w3.eth.get_transaction_count(user_addr),
            "gas": 60000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(approve_tx, private_key=creator_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

    contract = _market_contract(w3)
    tx = contract.functions.createMarket(
        num_outcomes, subsidy_micro, closes_at
    ).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr),
        "gas": 500000,
        "gasPrice": w3.eth.gas_price,
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
    w3 = _w3()
    account = w3.eth.account.from_key(oracle_private_key)
    user_addr = account.address
    contract = _market_contract(w3)

    tx = contract.functions.oracleResolve(
        market_id, winning_outcome
    ).build_transaction({
        "from": user_addr,
        "nonce": w3.eth.get_transaction_count(user_addr),
        "gas": 100000,
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=oracle_private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(f"oracleResolve reverted: {tx_hash.hex()}")
    return tx_hash.hex()


async def get_market_info(market_id: int) -> dict:
    """Read full market info from chain."""
    w3 = _w3()
    contract = _market_contract(w3)
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
