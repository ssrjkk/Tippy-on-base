"""AI assistant backend: any OpenAI-compatible chat-completions endpoint.

Works with OpenAI, OpenRouter, Together, local vLLM/llama.cpp servers —
anything speaking POST {base}/chat/completions. Uses only the stdlib
(urllib) so no new dependency lands in requirements.txt.

Disabled state: AI_API_KEY unset -> ai_enabled() is False and /ask explains
how to enable it instead of erroring mid-chat.
"""

import json
import urllib.error
import urllib.request

from . import config

SYSTEM_PROMPT = (
    "You are Tippy, the assistant of a Telegram bot that runs a community "
    "economy in USDC on Base (an Ethereum L2 by Coinbase). You help users "
    "with the bot's features: instant USDC tips (/tip), prediction markets "
    "with live AMM odds (/market create, /markets, /trade, /sell), "
    "parimutuel polls (/bet), deposits and withdrawals on Base, paywalled "
    "content (/paywall), per-user wallets (/wallet), and the x402 HTTP "
    "payment API for agents. Answer in the user's language (default "
    "Russian). Be concise and practical; when a command helps, show it. "
    "You give information, not financial advice."
)


def ai_enabled() -> bool:
    return bool(config.AI_API_KEY)


def ask(question: str) -> str:
    """One-shot question -> answer text. Raises RuntimeError with a short,
    user-presentable message on any failure (network, HTTP, bad payload)."""
    if not config.AI_API_KEY:
        raise RuntimeError("AI is not configured")
    url = f"{config.AI_API_URL}/chat/completions"
    payload = {
        "model": config.AI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question[: config.AI_MAX_QUESTION_LEN]},
        ],
        "max_tokens": 1024,
        "temperature": 0.4,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.AI_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.AI_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
            detail = str(body.get("error", {}).get("message", ""))[:200]
        except Exception:
            pass
        raise RuntimeError(f"AI HTTP {e.code}: {detail or 'request failed'}") from e
    except Exception as e:
        raise RuntimeError(f"AI unavailable: {e}") from e
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("AI returned an unexpected response") from e
    return (text or "").strip()
