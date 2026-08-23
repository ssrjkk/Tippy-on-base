"""OutcomeMarket on-chain integration (web3.py).

Wraps the OutcomeMarket Solidity contract for bot-side trading.  Provides
quote/buy/sell/resolve/redeem functions that the Telegram handlers can call
via asyncio.to_thread().
"""

import json
import os
import time
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
