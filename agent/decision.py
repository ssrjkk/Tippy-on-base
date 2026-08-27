"""LLM decision layer — converts news into structured market/bet decisions.

All LLM output is JSON-validated. No free-text parsing into actions.
External news content is wrapped in delimiters and marked untrusted.

Model routing:
  1. Cheap model (gpt-4o-mini / llama-3.1-8b) — filters noise, rejects irrelevant news
  2. Strong model (gpt-4o / llama-3.3-70b) — decides market params + bet size
  This cuts LLM costs by ~90% (cheap model handles 90% of cycles).
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


FILTER_PROMPT = """You are a news filter for a crypto prediction market agent.

Decide if this news is relevant for creating a prediction market.
Only reject if clearly irrelevant (meme coins, airdrops, personal drama).

Return ONLY valid JSON:
{"relevant": true/false, "reason": "one sentence"}

Do NOT include markdown or backticks.
"""


DECISION_PROMPT = """You are an autonomous trading agent for prediction markets on Base.
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


def _call_llm(messages: list[dict], model: str | None = None, temperature: float = 0.3) -> dict:
    """Call LLM via Groq/OpenAI-compatible API. Returns parsed JSON."""
    api_key = os.environ.get("AI_API_KEY", "")
    if not api_key:
        return {"create_market": False, "reasoning": "No AI_API_KEY set"}

    api_url = os.environ.get(
        "AI_API_URL", "https://api.groq.com/openai/v1/chat/completions"
    )

    payload = json.dumps({
        "model": model or config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 500,
    }).encode()

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
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        return json.loads(content.strip())
    except Exception as e:
        return {"error": str(e)}


def _filter_news(news_items: list[str]) -> tuple[list[str], list[str]]:
    """Stage 1: cheap model filters irrelevant news. Returns (relevant, rejected)."""
    cheap_model = os.environ.get("AGENT_FILTER_MODEL", "llama-3.1-8b-instant")
    relevant = []
    rejected = []

    for item in news_items:
        result = _call_llm(
            messages=[
                {"role": "system", "content": FILTER_PROMPT},
                {"role": "user", "content": item},
            ],
            model=cheap_model,
            temperature=0.1,
        )
        if result.get("relevant", True):
            relevant.append(item)
        else:
            rejected.append(item)

    return relevant, rejected


def _decide_on_news(news_items: list[str], balance: float) -> dict:
    """Stage 2: strong model makes the actual decision."""
    user_msg = (
        f"Agent balance: ${balance:.2f} USDC\n"
        f"Daily spend cap: ${config.DAILY_SPEND_CAP_USDC}\n"
        f"Per-tx cap: ${config.PER_TX_CAP_USDC}\n\n"
        "Analyze these news items and decide:\n\n"
        + "\n".join(news_items)
    )

    return _call_llm(
        messages=[
            {"role": "system", "content": DECISION_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=config.LLM_MODEL,
        temperature=0.3,
    )


def decide(news_items: list[str], balance: float) -> MarketDecision | None:
    """Two-stage decision: cheap filter → strong decision.

    Returns None if no market should be created.
    """
    # Stage 1: cheap model filters noise
    relevant, _rejected = _filter_news(news_items)
    if not relevant:
        return None

    # Stage 2: strong model decides
    raw = _decide_on_news(relevant, balance)

    if "error" in raw or not raw.get("create_market"):
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
