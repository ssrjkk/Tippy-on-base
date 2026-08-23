"""Agent tools — direct function calls into Tippy's internal ledger.

These wrap ledger.* methods for the agent loop. Each tool:
  1. Checks spend caps (caps.check_action)
  2. Calls ledger
  3. Records action (caps.record_action) or error (caps.record_error)

For the demo, the agent uses ledger directly (same-process). Production
would call the HTTP API with an agent-specific auth token.
"""

import asyncio
import time
from bot.ledger import ledger
from . import config, caps


async def create_market(
    question: str,
    options: list[str],
    hours: float = 24.0,
    subsidy_usdc: float = 10.0,
) -> dict:
    """Create a prediction market with LMSR subsidy.

    Returns: {"market_id": int, "options": list[str], "subsidy_usdc": float}
    """
    err = caps.check_action(subsidy_usdc)
    if err:
        return {"error": err}

    close_at = int(time.time() + min(hours, 30 * 24) * 3600)
    tg_id = config.AGENT_TG_ID

    try:
        market_id = await ledger.create_market(
            tg_id, question, options, close_at=close_at
        )
        if market_id is None:
            caps.record_error()
            return {"error": "create_market returned None (schema issue?)"}
        caps.record_action(subsidy_usdc)
        return {
            "market_id": market_id,
            "options": options,
            "subsidy_usdc": subsidy_usdc,
        }
    except Exception as e:
        caps.record_error()
        return {"error": str(e)}


async def place_bet(
    market_id: int,
    outcome_idx: int,
    amount_usdc: float,
) -> dict:
    """Place a bet on a prediction market.

    Returns: {"status": str, "new_balance_usdc": float}
    """
    err = caps.check_action(amount_usdc)
    if err:
        return {"error": err}

    micro = int(round(amount_usdc * 1_000_000))
    tg_id = config.AGENT_TG_ID

    try:
        status, info = await ledger.buy_shares(market_id, tg_id, outcome_idx, micro)
        if status != "ok":
            caps.record_error()
            return {"error": f"buy_shares failed: {status}"}
        caps.record_action(amount_usdc)
        bal = float(await ledger.balance(tg_id))
        return {"status": "ok", "info": info, "new_balance_usdc": bal}
    except Exception as e:
        caps.record_error()
        return {"error": str(e)}


async def get_market(market_id: int) -> dict | None:
    """Read-only: fetch market view."""
    try:
        return await ledger.amm_market_view(market_id)
    except Exception:
        return None


async def list_open_markets(limit: int = 10) -> list[dict]:
    """Read-only: list open markets."""
    try:
        markets = await ledger.open_markets(limit)
        out = []
        for m in markets:
            view = await ledger.amm_market_view(int(m["id"]))
            if view:
                out.append(view)
        return out
    except Exception:
        return []


async def get_balance() -> float:
    """Read-only: agent's USDC balance."""
    try:
        return float(await ledger.balance(config.AGENT_TG_ID))
    except Exception:
        return 0.0
