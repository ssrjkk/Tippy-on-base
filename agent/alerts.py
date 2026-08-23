"""Telegram alerts for agent events — circuit breaker, errors, significant trades.

Requires: AIGRAM_BOT_TOKEN and ALERT_CHAT_ID env vars.
"""

import asyncio
import os
import time

from aiogram import Bot
from aiogram.enums import ParseMode


_bot: Bot | None = None


def _get_bot() -> Bot | None:
    global _bot
    token = os.environ.get("AIGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        return None
    if _bot is None:
        _bot = Bot(token=token, default=BotProperties(parse_mode=ParseMode.HTML))
    return _bot


def _get_chat_id() -> int | None:
    cid = os.environ.get("ALERT_CHAT_ID")
    return int(cid) if cid else None


async def send_alert(text: str, parse_mode: str = "HTML") -> bool:
    """Send alert to configured chat. Returns True on success."""
    bot = _get_bot()
    chat_id = _get_chat_id()
    if not bot or not chat_id:
        return False
    try:
        await bot.send_message(chat_id, text, parse_mode=parse_mode)
        return True
    except Exception:
        return False


async def alert_circuit_breaker(cooldown_secs: int, consecutive_errors: int) -> None:
    """Alert when circuit breaker activates."""
    await send_alert(
        f"🚨 <b>Agent Circuit Breaker Active</b>\n"
        f"Cooldown: {cooldown_secs}s\n"
        f"Consecutive errors: {consecutive_errors}\n"
        f"Time: {time.strftime('%H:%M:%S')}"
    )


async def alert_error(error_msg: str, context: str = "") -> None:
    """Alert on agent error."""
    ctx = f"\nContext: {context}" if context else ""
    await send_alert(
        f"⚠️ <b>Agent Error</b>\n"
        f"{error_msg[:200]}{ctx}\n"
        f"Time: {time.strftime('%H:%M:%S')}"
    )


async def alert_market_created(market_id: int, question: str, options: list[str]) -> None:
    """Alert when agent creates a new market."""
    opts = "\n".join(f"  • {o}" for o in options)
    await send_alert(
        f"📊 <b>New Market Created</b> #{market_id}\n"
        f"Q: {question}\n"
        f"Options:\n{opts}"
    )


async def alert_bet_placed(market_id: int, outcome: int, amount: float) -> None:
    """Alert when agent places a bet."""
    await send_alert(
        f"🎰 <b>Bet Placed</b>\n"
        f"Market #{market_id}, outcome {outcome}\n"
        f"Amount: ${amount:.2f}"
    )


async def alert_signal_sold(item_id: int, price: float) -> None:
    """Alert when agent sells a signal."""
    await send_alert(
        f"💰 <b>Signal Sold</b>\n"
        f"Paywall item #{item_id}\n"
        f"Price: ${price:.2f}"
    )


async def alert_daily_summary(spent: float, markets_created: int, bets_placed: int) -> None:
    """Daily summary alert."""
    await send_alert(
        f"📈 <b>Agent Daily Summary</b>\n"
        f"Spent: ${spent:.2f}\n"
        f"Markets created: {markets_created}\n"
        f"Bets placed: {bets_placed}\n"
        f"Date: {time.strftime('%Y-%m-%d')}"
    )
