"""OutcomeMarket on-chain integration (web3.py).

Wraps the OutcomeMarket Solidity contract for bot-side trading.  Provides
quote/buy/sell/resolve/redeem functions that the Telegram handlers can call
via asyncio.to_thread().

Resolution model:
  - Owner resolves via ownerResolve() (fallback)
  - Oracle resolves via oracleResolve() (primary)
  - Owner disputes via disputeResolution() (2h window after oracle)
  - Anyone cancels via cancelExpired() (24h after close, no resolution)
"""

import json
import os
from pathlib import Path

from web3 import Web3

from . import config

_CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"
_OUTCOME_ABI: list | None = None
_OUTCOME_ADDRESS: str | None = None


def _load_abi() -> list:
    global _OUTCOME_ABI
    if _OUTCOME_ABI is not None:
        return _OUTCOME_ABI
    # Try compiled artifact first (forge build output)
    artifact = _CONTRACTS_DIR.parent / "out" / "OutcomeMarket.sol" / "OutcomeMarket.json"
    if artifact.exists():
        data = json.loads(artifact.read_text())
        _OUTCOME_ABI = data["abi"]
    else:
        raise FileNotFoundError(
            f"OutcomeMarket ABI not found at {artifact}. "
            "Run 'forge build' first."
        )
    return _OUTCOME_ABI


def get_address() -> str | None:
    """Return the deployed OutcomeMarket address from env, or None."""
    global _OUTCOME_ADDRESS
    if _OUTCOME_ADDRESS is not None:
        return _OUTCOME_ADDRESS
    _OUTCOME_ADDRESS = os.environ.get("OUTCOME_MARKET_ADDRESS")
    return _OUTCOME_ADDRESS


def _contract(w3: Web3):
    addr = get_address()
    if not addr:
        return None
    return w3.eth.contract(
        address=Web3.to_checksum_address(addr),
        abi=_load_abi(),
    )


def quote_buy(w3: Web3, market_id: int, outcome_idx: int, shares_micro: int) -> int:
    """Quote cost in micro-USDC to buy `shares_micro` of outcome."""
    c = _contract(w3)
    if c is None:
        raise RuntimeError("OUTCOME_MARKET_ADDRESS not set")
    return c.functions.quoteBuy(market_id, outcome_idx, shares_micro).call()


def quote_sell(w3: Web3, market_id: int, outcome_idx: int, shares_micro: int) -> int:
    """Quote proceeds in micro-USDC from selling `shares_micro` of outcome."""
    c = _contract(w3)
    if c is None:
        raise RuntimeError("OUTCOME_MARKET_ADDRESS not set")
    return c.functions.quoteSell(market_id, outcome_idx, shares_micro).call()


def price_of(w3: Web3, market_id: int, outcome_idx: int) -> int:
    """Current price as 18-decimal fraction (0.5e18 = 50%)."""
    c = _contract(w3)
    if c is None:
        raise RuntimeError("OUTCOME_MARKET_ADDRESS not set")
    return c.functions.priceOf(market_id, outcome_idx).call()


def oracle_resolve(w3: Web3, market_id: int, winning_outcome: int) -> str:
    """Oracle resolves the market. Returns tx hash."""
    c = _contract(w3)
    if c is None:
        raise RuntimeError("OUTCOME_MARKET_ADDRESS not set")
    tx = c.functions.oracleResolve(market_id, winning_outcome).build_transaction({
        "from": config.ORACLE_ADDRESS,
        "nonce": w3.eth.get_transaction_count(config.ORACLE_ADDRESS),
        "maxPriorityFeePerGas": w3.to_wei("0.01", "gwei"),
        "maxFeePerGas": w3.eth.get_block("latest")["baseFeePerGas"] * 2,
        "chainId": w3.eth.chain_id,
    })
    acct = w3.eth.account.from_key(config.ORACLE_PRIVATE_KEY)
    signed = acct.sign_transaction(tx)
    return "0x" + w3.eth.send_raw_transaction(signed.raw_transaction).hex()


def owner_resolve(w3: Web3, market_id: int, winning_outcome: int) -> str:
    """Owner resolves the market directly (fallback). Returns tx hash."""
    c = _contract(w3)
    if c is None:
        raise RuntimeError("OUTCOME_MARKET_ADDRESS not set")
    tx = c.functions.ownerResolve(market_id, winning_outcome).build_transaction({
        "from": config.HOT_WALLET,
        "nonce": w3.eth.get_transaction_count(config.HOT_WALLET),
        "maxPriorityFeePerGas": w3.to_wei("0.01", "gwei"),
        "maxFeePerGas": w3.eth.get_block("latest")["baseFeePerGas"] * 2,
        "chainId": w3.eth.chain_id,
    })
    acct = w3.eth.account.from_key(config.HOT_WALLET_KEY)
    signed = acct.sign_transaction(tx)
    return "0x" + w3.eth.send_raw_transaction(signed.raw_transaction).hex()


def dispute_resolution(w3: Web3, market_id: int) -> str:
    """Owner disputes oracle resolution within 2h window. Returns tx hash."""
    c = _contract(w3)
    if c is None:
        raise RuntimeError("OUTCOME_MARKET_ADDRESS not set")
    tx = c.functions.disputeResolution(market_id).build_transaction({
        "from": config.HOT_WALLET,
        "nonce": w3.eth.get_transaction_count(config.HOT_WALLET),
        "maxPriorityFeePerGas": w3.to_wei("0.01", "gwei"),
        "maxFeePerGas": w3.eth.get_block("latest")["baseFeePerGas"] * 2,
        "chainId": w3.eth.chain_id,
    })
    acct = w3.eth.account.from_key(config.HOT_WALLET_KEY)
    signed = acct.sign_transaction(tx)
    return "0x" + w3.eth.send_raw_transaction(signed.raw_transaction).hex()


def cancel_expired(w3: Web3, market_id: int) -> str:
    """Cancel expired market (>24h past close). Returns tx hash."""
    c = _contract(w3)
    if c is None:
        raise RuntimeError("OUTCOME_MARKET_ADDRESS not set")
    tx = c.functions.cancelExpired(market_id).build_transaction({
        "from": config.HOT_WALLET,
        "nonce": w3.eth.get_transaction_count(config.HOT_WALLET),
        "maxPriorityFeePerGas": w3.to_wei("0.01", "gwei"),
        "maxFeePerGas": w3.eth.get_block("latest")["baseFeePerGas"] * 2,
        "chainId": w3.eth.chain_id,
    })
    acct = w3.eth.account.from_key(config.HOT_WALLET_KEY)
    signed = acct.sign_transaction(tx)
    return "0x" + w3.eth.send_raw_transaction(signed.raw_transaction).hex()
