"""Telegram channel paywall lifecycle: kick members whose access expired.

Kept separate from the chain layer — this is pure Telegram + ledger logic.
"""

import time

from .ledger import ledger


async def kick_expired_channel_subscriptions(bot) -> int:
    """Kick users whose paid channel access expired. Returns the number kicked.

    A subscription row is dropped ONLY when the user is provably gone (left,
    kicked, chat deleted) or is an admin of the channel. If the bot lost
    admin rights (or the network failed) the row is kept and the kick retries
    next cycle — otherwise a dropped row would leave the user inside the
    channel for free forever, and a re-purchase would silently re-arm access
    they already had.
    """
    from aiogram.exceptions import TelegramBadRequest

    now = time.time()
    kicked = 0
    for sub in ledger.active_channel_subscriptions():
        if int(sub["expires_at"]) > now:
            continue
        chat_id, tg_id = int(sub["chat_id"]), int(sub["tg_id"])
        try:
            member = await bot.get_chat_member(chat_id, tg_id)
            if member.status in ("administrator", "creator"):
                ledger.expire_channel_subscription(chat_id, tg_id)
                continue
        except Exception:
            pass  # probe may fail — the ban itself decides below
        try:
            await bot.ban_chat_member(chat_id, tg_id)
            await bot.unban_chat_member(chat_id, tg_id)
        except TelegramBadRequest as e:
            msg = str(getattr(e, "message", ""))
            if ("not found" in msg or "NOT_PARTICIPANT" in msg
                    or "chat not found" in msg or "CHAT_NOT_FOUND" in msg):
                # the user is no longer in the channel (or it is gone)
                ledger.expire_channel_subscription(chat_id, tg_id)
            # otherwise (bot lost admin rights, ...) — keep the row, retry later
            continue
        except Exception:
            continue  # network hiccup — keep the row, retry later
        kicked += 1
        ledger.expire_channel_subscription(chat_id, tg_id)
        try:
            await bot.send_message(
                tg_id,
                "🔑 Подписка на канал истекла, доступ закрыт.\n"
                "Продлить: /paywall channels",
            )
        except Exception:
            pass
    return kicked
