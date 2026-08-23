"""LLM decision layer — converts news into structured market/bet decisions.

All LLM output is JSON-validated. No free-text parsing into actions.
External news content is wrapped in delimiters and marked untrusted.
"""

import json
import os
import urllib.request
from dataclasses import dataclass

from . import config


@dataclass
class MarketDecision:
    question: str
    options: list[str]
    hours: float
    bet_outcome: int
    bet_amount_usdc: float
    confidence: float  # 0.0-1.0
    reasoning: str  # for audit trail only, not executed


SYSTEM_PROMPT = """You are an autonomous trading agent for prediction markets on Base.
You analyze news and decide whether to create a market and place a bet.

RULES:
1. Only create markets for clear, binary or multi-outcome events.
2. Never bet more than $10 per trade.
3. Max 4 outcomes per market, 64 chars each.
4. Close the market within 1-7 days.
5. Be conservative: only bet when you have high confidence.

OUTPUT: return ONLY valid JSON matching this schema:
{
  "create_market": true/false,
  "question": "string (if create_market=true)",
  "options": ["string", ...] (2-4 items, if create_market=true),
  "hours": number (1-168, if create_market=true),
  "bet_outcome": 0,
  "bet_amount_usdc": number (1-10),
  "confidence": 0.0-1.0,
  "reasoning": "short explanation"
}

Do NOT include markdown, backticks, or any text outside the JSON object.
"""


def _call_llm(news_items: list[str], balance: float) -> dict:
    """Call LLM via Groq/OpenAI-compatible API. Returns parsed JSON."""
    user_msg = (
        f"Agent balance: ${balance:.2f} USDC\n"
        f"Daily spend cap: ${config.DAILY_SPEND_CAP_USDC}\n"
        f"Per-tx cap: ${config.PER_TX_CAP_USDC}\n\n"
        "Analyze these news items and decide:\n\n"
        + "\n".join(news_items)
    )

    api_key = os.environ.get("AI_API_KEY", "")
    if not api_key:
        return {
            "create_market": False,
            "reasoning": "No AI_API_KEY set, skipping decision",
        }

    payload = json.dumps({
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
    }).encode()

    # Groq API (also works with OpenAI-compatible endpoints)
    api_url = os.environ.get(
        "AI_API_URL", "https://api.groq.com/openai/v1/chat/completions"
    )
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        content = result["choices"][0]["message"]["content"]
        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        return json.loads(content.strip())
    except Exception as e:
        return {"create_market": False, "reasoning": f"LLM error: {e}"}


def decide(news_items: list[str], balance: float) -> MarketDecision | None:
    """Convert news into a structured MarketDecision via LLM.

    Returns None if LLM says no market should be created.
    """
    raw = _call_llm(news_items, balance)

    if not raw.get("create_market"):
        return None

    # Validate required fields
    question = raw.get("question", "")
    options = raw.get("options", [])
    if not question or len(options) < 2 or len(options) > 4:
        return None

    # Enforce caps
    bet_amount = float(raw.get("bet_amount_usdc", 1.0))
    bet_amount = min(bet_amount, config.PER_TX_CAP_USDC)
    bet_amount = max(bet_amount, 0.0)

    hours = float(raw.get("hours", 24))
    hours = max(1.0, min(hours, 168))

    return MarketDecision(
        question=question,
        options=[str(o)[:64] for o in options[:4]],
        hours=hours,
        bet_outcome=int(raw.get("bet_outcome", 0)) % len(options),
        bet_amount_usdc=bet_amount,
        confidence=float(raw.get("confidence", 0.5)),
        reasoning=str(raw.get("reasoning", ""))[:500],
    )
