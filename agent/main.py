"""Tippy Agent main loop — perceive → decide → act → attest.

Usage:
    python -m agent.main                    # single cycle (for demo)
    python -m agent.main --loop             # continuous loop
    python -m agent.main --status           # show current caps/status
"""

import argparse
import asyncio
import json
import logging
import os
import time

from . import caps, config
from .decision import decide
from .eas import AttestationData, attest_action
from .news import fetch_news
from .signals import sell_signal
from .tools import create_market, get_balance, place_bet

log = logging.getLogger("agent")

_AUDIT_FILE = os.path.join(config.STATE_DIR, "agent_audit.jsonl")


async def single_cycle() -> bool:
    """Run one perceive → decide → act → attest cycle. Returns True if action taken."""
    log.info("=== Agent cycle ===")

    # 1. Check circuit breaker
    status = caps.get_status()
    if status["cooldown_active"]:
        log.info("Circuit breaker active, skipping cycle")
        return False

    # 2. Perceive — fetch news
    news = await asyncio.to_thread(fetch_news, max_items=3)
    if not news:
        log.info("No new relevant news found")
        return False
    log.info("Found %d news item(s)", len(news))
    for n in news:
        log.info("  [%.1f] %s", n.relevance, n.title[:80])

    # 3. Decide — LLM analysis
    balance = await get_balance()
    news_prompts = [n.to_prompt() for n in news]
    decision = await asyncio.to_thread(decide, news_prompts, balance)
    if decision is None:
        log.info("LLM decided: no market to create")
        return False
    log.info("Decision: %s", decision.question)
    log.info("  Options: %s", decision.options)
    log.info("  Bet: $%.2f on outcome %s", decision.bet_amount_usdc, decision.bet_outcome)
    log.info("  Confidence: %.0f%%", decision.confidence * 100)
    log.info("  Reasoning: %s", decision.reasoning[:120])

    # 4. Act — create market
    market_result = await create_market(
        question=decision.question,
        options=decision.options,
        hours=decision.hours,
        subsidy_usdc=10.0,
    )
    if "error" in market_result:
        log.error("ERROR creating market: %s", market_result["error"])
        return False

    market_id = market_result["market_id"]
    log.info("Market created: #%s", market_id)

    # 5. Attest — EAS on-chain attestation for market creation
    await _attest_action("create_market", market_id, 10_000_000, decision.confidence, decision.reasoning)

    # 6. Act — place bet
    if decision.bet_amount_usdc > 0:
        bet_result = await place_bet(
            market_id=market_id,
            outcome_idx=decision.bet_outcome,
            amount_usdc=decision.bet_amount_usdc,
        )
        if "error" in bet_result:
            log.error("ERROR placing bet: %s", bet_result["error"])
        else:
            log.info("Bet placed! New balance: $%.2f", bet_result.get("new_balance_usdc", 0))
            await _attest_action(
                "place_bet",
                market_id,
                int(decision.bet_amount_usdc * 1_000_000),
                decision.confidence,
                decision.reasoning,
            )

    # 7. Sell signal — create paywall post with analysis
    signal_result = await sell_signal(
        market_id=market_id,
        analysis=decision.reasoning,
        price_usdc=1.0,
    )
    if "error" not in signal_result:
        log.info("Signal sold: paywall item #%s", signal_result["item_id"])
        await _attest_action("sell_signal", market_id, 1_000_000, decision.confidence, decision.reasoning)
    else:
        log.info("Signal creation failed: %s", signal_result["error"])

    # 8. Log local audit trail
    _log_audit(market_id, decision)

    log.info("Cycle complete. Market #%s live.", market_id)
    return True


async def _attest_action(action_type: str, market_id: int, amount_micro: int, confidence: float, reasoning: str) -> None:
    """Submit EAS attestation (local fallback if no key).

    Runs off the event loop: EAS proof, gas push and receipt wait are all
    blocking RPC work — in a loop that also sleeps any network stall here
    would freeze the whole cycle."""
    data = AttestationData(
        action_type=action_type,
        market_id=market_id,
        amount_micro=amount_micro,
        confidence=int(confidence * 100),
        reasoning=reasoning[:200],
    )
    tx_hash = await asyncio.to_thread(attest_action, data)
    if tx_hash:
        log.info("EAS attestation: %s", tx_hash)
    # Local audit trail always written by eas.py


def _log_audit(market_id: int, decision) -> None:
    """Log full cycle to local audit trail."""
    entry = {
        "ts": time.time(),
        "market_id": market_id,
        "question": decision.question,
        "options": decision.options,
        "bet_outcome": decision.bet_outcome,
        "bet_amount_usdc": decision.bet_amount_usdc,
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
    }
    try:
        os.makedirs(os.path.dirname(_AUDIT_FILE), exist_ok=True)
        with open(_AUDIT_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        log.warning("audit trail write failed (read-only filesystem?)")


async def run_loop(stop: asyncio.Event | None = None) -> None:
    """Continuous agent loop with configurable interval.

    When `stop` is given, the loop wakes up on it instead of a fixed sleep so
    a graceful container shutdown (SIGTERM → stop.set) exits promptly instead
    of waiting out the whole interval.
    """
    log.info("Agent loop starting (interval=%ss)", config.NEWS_CHECK_INTERVAL)
    log.info("  Daily cap: $%s", config.DAILY_SPEND_CAP_USDC)
    log.info("  Per-tx cap: $%s", config.PER_TX_CAP_USDC)
    log.info("  Max actions/hour: %s", config.MAX_ACTIONS_PER_HOUR)
    log.info("  Model: %s", config.LLM_MODEL)

    cycle = 0
    while True:
        cycle += 1
        log.info("--- Agent cycle %d ---", cycle)
        try:
            await single_cycle()
        except Exception as e:
            caps.record_error()
            log.error("UNHANDLED ERROR in agent cycle: %s", e, exc_info=True)

        if stop is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.NEWS_CHECK_INTERVAL)
                log.info("Agent loop stopped (stop event)")
                return
            except asyncio.TimeoutError:
                continue
        await asyncio.sleep(config.NEWS_CHECK_INTERVAL)


def main() -> None:
    cfg_errors = config.validate()
    if cfg_errors:
        for e in cfg_errors:
            log.error("[CONFIG ERROR] %s", e)
        raise SystemExit(1)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loop", action="store_true", help="Run continuous loop")
    ap.add_argument("--status", action="store_true", help="Show agent status")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.status:
        s = caps.get_status()
        print(json.dumps(s, indent=2))
        return

    if args.loop:
        asyncio.run(run_loop())
    else:
        asyncio.run(single_cycle())


if __name__ == "__main__":
    main()
