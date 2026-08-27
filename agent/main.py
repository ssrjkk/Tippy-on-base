"""Tippy Agent main loop — perceive → decide → act → attest.

Usage:
    python -m agent.main                    # single cycle (for demo)
    python -m agent.main --loop             # continuous loop
    python -m agent.main --status           # show current caps/status
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from . import caps, config
from .decision import decide
from .eas import AttestationData, attest_action
from .news import fetch_news
from .signals import sell_signal
from .tools import create_market, get_balance, place_bet


async def single_cycle() -> bool:
    """Run one perceive → decide → act → attest cycle. Returns True if action taken."""
    print(f"[{time.strftime('%H:%M:%S')}] === Agent cycle ===")

    # 1. Check circuit breaker
    status = caps.get_status()
    if status["cooldown_active"]:
        print("  Circuit breaker active, skipping cycle")
        return False

    # 2. Perceive — fetch news
    news = fetch_news(max_items=3)
    if not news:
        print("  No new relevant news found")
        return False
    print(f"  Found {len(news)} news items:")
    for n in news:
        print(f"    [{n.relevance:.1f}] {n.title[:80]}")

    # 3. Decide — LLM analysis
    balance = await get_balance()
    news_prompts = [n.to_prompt() for n in news]
    decision = decide(news_prompts, balance)
    if decision is None:
        print("  LLM decided: no market to create")
        return False
    print(f"  Decision: {decision.question}")
    print(f"    Options: {decision.options}")
    print(f"    Bet: ${decision.bet_amount_usdc:.2f} on outcome {decision.bet_outcome}")
    print(f"    Confidence: {decision.confidence:.0%}")
    print(f"    Reasoning: {decision.reasoning[:120]}")

    # 4. Act — create market
    market_result = await create_market(
        question=decision.question,
        options=decision.options,
        hours=decision.hours,
        subsidy_usdc=10.0,
    )
    if "error" in market_result:
        print(f"  ERROR creating market: {market_result['error']}")
        return False

    market_id = market_result["market_id"]
    print(f"  Market created: #{market_id}")

    # 5. Attest — EAS on-chain attestation for market creation
    _attest_action("create_market", market_id, 10_000_000, decision.confidence, decision.reasoning)

    # 6. Act — place bet
    if decision.bet_amount_usdc > 0:
        bet_result = await place_bet(
            market_id=market_id,
            outcome_idx=decision.bet_outcome,
            amount_usdc=decision.bet_amount_usdc,
        )
        if "error" in bet_result:
            print(f"  ERROR placing bet: {bet_result['error']}")
        else:
            print(f"  Bet placed! New balance: ${bet_result.get('new_balance_usdc', 0):.2f}")
            _attest_action(
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
        print(f"  Signal sold: paywall item #{signal_result['item_id']}")
        _attest_action("sell_signal", market_id, 1_000_000, decision.confidence, decision.reasoning)
    else:
        print(f"  Signal creation failed: {signal_result['error']}")

    # 8. Log local audit trail
    _log_audit(market_id, decision)

    print(f"  Cycle complete. Market #{market_id} live.")
    return True


def _attest_action(action_type: str, market_id: int, amount_micro: int, confidence: float, reasoning: str) -> None:
    """Submit EAS attestation (local fallback if no key)."""
    data = AttestationData(
        action_type=action_type,
        market_id=market_id,
        amount_micro=amount_micro,
        confidence=int(confidence * 100),
        reasoning=reasoning[:200],
    )
    tx_hash = attest_action(data)
    if tx_hash:
        print(f"    EAS attestation: {tx_hash}")
    # Local audit trail always written by eas.py


def _log_audit(market_id: int, decision) -> None:
    """Log full cycle to local audit trail."""
    log_file = Path("agent_audit.jsonl")
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
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


async def run_loop() -> None:
    """Continuous agent loop with configurable interval."""
    print(f"Agent loop starting (interval={config.NEWS_CHECK_INTERVAL}s)")
    print(f"  Daily cap: ${config.DAILY_SPEND_CAP_USDC}")
    print(f"  Per-tx cap: ${config.PER_TX_CAP_USDC}")
    print(f"  Max actions/hour: {config.MAX_ACTIONS_PER_HOUR}")
    print(f"  Model: {config.LLM_MODEL}")

    cycle = 0
    while True:
        cycle += 1
        print(f"\n--- Cycle {cycle} ---")
        try:
            await single_cycle()
        except Exception as e:
            caps.record_error()
            print(f"  UNHANDLED ERROR: {e}")
        await asyncio.sleep(config.NEWS_CHECK_INTERVAL)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loop", action="store_true", help="Run continuous loop")
    ap.add_argument("--status", action="store_true", help="Show agent status")
    args = ap.parse_args()

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
