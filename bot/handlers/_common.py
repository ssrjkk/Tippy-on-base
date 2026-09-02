"""Shared state and helpers for the bot.handlers package.

All mutating state that tests patch (ledger, _now, _qr_bytes, _money_cmd_last)
lives here so a single monkeypatch on bot.handlers._common works everywhere.
"""

import re
import time
from decimal import Decimal

from aiogram import Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .. import base, config, i18n, wallets, create2
from .. import qr as qrlib
from ..ledger import async_ledger as ledger

__all__ = [
    "AMOUNT_RE",
    "BET_ID_RE",
    "BET_LINK_RE",
    "DEADLINE_RE",
    "DONATE_LINK_RE",
    "HELP",
    "KIND_EMOJI",
    "PAYWALL_LINK_RE",
    "QUICK_AMOUNTS",
    "SIG_RE",
    "TX_HASH_RE",
    "USDC_ADDR_RE",
    "_bot_username",
    "_edit_menu",
    "_esc",
    "_fmt",
    "_get_bot_username",
    "_menu_kb",
    "_money_cmd_last",
    "_now",
    "_qr_bytes",
    "_throttle",
    "_to_micro",
    "base",
    "config",
    "ledger",
    "qrlib",
    "require_private",
    "router",
    "wallets",
]

router = Router()

TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
USDC_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
AMOUNT_RE = re.compile(r"^\d{1,9}(\.\d{1,6})?$")
SIG_RE = re.compile(r"^0x[a-fA-F0-9]{130}$")
BET_ID_RE = re.compile(r"^\d{1,9}$")
DONATE_LINK_RE = re.compile(r"^donate_(\d{1,20})$")
BET_LINK_RE = re.compile(r"^bet_(\d{1,20})$")
PAYWALL_LINK_RE = re.compile(r"^paywall_(\d{1,20})$")
DEADLINE_RE = re.compile(r"^(\d{1,3})([hd])$")

QUICK_AMOUNTS = ("5", "10", "25", "50")

HELP = i18n.t("ru", "help_full")


async def user_lang(tg_id: int) -> str:
    try:
        s = await ledger.get_settings(tg_id)
        return i18n.norm(s.get("lang"))
    except Exception:
        return "ru"


async def require_private(message: types.Message) -> bool:
    """Refuse sensitive commands outside a private chat to avoid leaking
    secrets (private keys, seed phrases) into group history. Returns True
    when the command may proceed. On refusal it hints the user to DM the
    bot and best-effort deletes the triggering message."""
    if message.chat.type == "private":
        return True
    lang = await user_lang(message.from_user.id)
    await message.answer(i18n.t(lang, "private_chat_only"))
    try:
        await message.delete()
    except Exception:
        pass
    return False


def help_text(lang: str = "ru") -> str:
    return i18n.t(lang, "help_full")


KIND_EMOJI = {
    "deposit": "⬇️",
    "tip": "💸",
    "withdraw": "⬆️",
    "fee": "🧾",
    "bet": "🎲",
    "bet_win": "🏆",
    "bet_cancel": "↩️",
    "x402": "🤖",
    "paywall": "🔐",
    "paywall_earn": "💰",
    "channel_pay": "🔑",
    "channel_earn": "💰",
    "market_create": "🏦",
    "market_buy": "📈",
    "market_sell": "📉",
    "market_win": "🏆",
    "market_cancel": "↩️",
    "market_refund": "↩️",
}

_bot_username: str | None = None


def _fmt(amount_micro: int) -> str:
    d = Decimal(amount_micro) / Decimal(10**config.USDC_DECIMALS)
    s = f"{d:,.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _to_micro(amount: Decimal) -> int:
    return int(amount * Decimal(10**config.USDC_DECIMALS))


def _esc(s: str, n: int = 4) -> str:
    return f"{s[:6]}…{s[-n:]}"


def _now() -> float:
    return time.time()


_money_cmd_last: dict[tuple[int, str], float] = {}


async def _throttle(tg_id: int, action: str) -> str | None:
    cooldown = config.MONEY_CMD_COOLDOWN_SECONDS
    if cooldown <= 0:
        return None
    key = (tg_id, action)
    now = _now()
    last = _money_cmd_last.get(key, 0.0)
    if now - last < cooldown:
        lang = await user_lang(tg_id)
        remaining = max(1, int(cooldown - (now - last)) + 1)
        return i18n.t(lang, "throttle", sec=remaining)
    if len(_money_cmd_last) > 100_000:
        cutoff = now - 3600
        expired = [k for k, v in _money_cmd_last.items() if v < cutoff]
        for k in expired:
            del _money_cmd_last[k]
    _money_cmd_last[key] = now
    return None


async def _get_bot_username(bot) -> str:
    global _bot_username
    if _bot_username is None:
        me = await bot.get_me()
        _bot_username = me.username
    return _bot_username


def _menu_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    def b(key: str, cb: str | None = None) -> InlineKeyboardButton:
        if key == "about":
            return InlineKeyboardButton(text=i18n.t(lang, "btn_about"), callback_data="about")
        return InlineKeyboardButton(text=i18n.t(lang, f"btn_{key}"), callback_data=cb or key)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [b("bal"), b("dep")],
            [b("tip"), b("ask")],
            [b("markets", "markets_amm"), b("bets")],
            [b("donate"), b("top")],
            [b("hist"), b("stats")],
            [b("wallet"), b("paywall")],
            [b("settings")],
            [b("about")],
            [InlineKeyboardButton(text=i18n.t(lang, "btn_mini_app"), callback_data="miniapp")],
        ]
    )


async def _qr_bytes(data: str) -> bytes | None:
    try:
        return await qrlib.qr_bytes(data)
    except Exception:
        return None


async def _edit_menu(cb: types.CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await cb.message.edit_caption(caption=text, reply_markup=reply_markup)
        except Exception:
            pass
