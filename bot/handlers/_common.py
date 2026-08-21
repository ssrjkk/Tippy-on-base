"""Shared state and helpers for the bot.handlers package.

All mutating state that tests patch (ledger, _now, _qr_bytes, _money_cmd_last)
lives here so a single monkeypatch on bot.handlers._common works everywhere.
"""

import re
import time
from decimal import Decimal

from aiogram import Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .. import base, config, i18n, wallets
from .. import qr as qrlib
from ..ledger import ledger

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

# Quick-amount buttons for inline betting.
QUICK_AMOUNTS = ("5", "10", "25", "50")

HELP = (
    "🤖 <b>Tippy</b> — экономика сообщества в USDC на <b>Base</b>.\n"
    "🟦 Сеть Base · монета USDC · все переводы в блокчейне\n\n"
    "💸 <b>Чаевые</b>\n"
    "• /tip 5 @nick — кинуть 5 USDC\n"
    "• /tip 5 (ответом на сообщение) — кинуть автору\n"
    "• 🔥/❤️/⚡/👏/🎉 на сообщение — реакция-чаевые (в группах)\n"
    "• /rain 10 — разбросать 10 USDC случайным участникам группы 🌧️\n\n"
    "📈 <b>Рынки предсказаний (AMM)</b>\n"
    "• /market create &lt;банк&gt; &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt; [24h] — создать рынок с живыми котировками\n"
    "• /markets — открытые рынки (кнопки)\n"
    "• /trade &lt;id&gt; &lt;номер&gt; &lt;сумма&gt; — купить доли по живой цене\n"
    "• /sell &lt;id&gt; &lt;номер&gt; [50%] — продать доли в любой момент\n"
    "• /positions — твои позиции и PnL\n\n"
    "🎲 <b>Ставки (пулы)</b>\n"
    "• /bet create &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt; [24h] — создать\n"
    "• /bets — открытые ставки (кнопки)\n"
    "• /bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt; — поставить\n"
    "• /mybets — твои позиции\n"
    "• /resolve &lt;id&gt; &lt;номер&gt; — закрыть (создатель)\n"
    "• /cancel &lt;id&gt; — отменить / вернуть деньги после истечения\n\n"
    "🧠 <b>ИИ-ассистент</b>\n"
    "• /ask &lt;вопрос&gt; — спросить ИИ о чём угодно (можно ответом на сообщение)\n\n"
    "💰 <b>Кошелёк</b>\n"
    "• /donate — твоя страница донатов с QR\n"
    "• /deposit — QR + адрес для пополнения\n"
    "• /link &lt;адрес&gt; — привязать кошелёк (авто-зачисление)\n"
    "• /withdraw &lt;адрес&gt; &lt;сумма&gt; — вывод (комиссия 1%, мин. 1 USDC)\n"
    "• /tx &lt;hash&gt; — проверить транзакцию в Base\n\n"
    "📊 <b>Ещё</b>\n"
    "• /menu — меню · /balance · /stats · /top · /history\n"
    "• /settings — уведомления и реакции ⚙️\n\n"
    "🔐 <b>Платный контент</b>\n"
    "• /paywall create 5 Заголовок — создать платный пост\n"
    "• /paywall list · /paywall buy &lt;id&gt; — купить и открыть\n"
    "• /paywall subscribe @канал — доступ к платному каналу\n\n"
    "🟦 <b>На Base</b> · 🪙 USDC (ERC-20) · 🔍 все транзакции в блокчейне\n"
    "🏗️ <b>Base</b> — безопасная, дешёвая, развивающаяся L2 от Coinbase: base.org\n"
    "👛 Свой кошелёк: /wallet · выгрузить ключ и сид: /wallet export · импорт по сид-фразе: /import"
)

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


def _throttle(tg_id: int, action: str) -> str | None:
    """Anti-spam: return a wait message if the user is over the cooldown.

    Bounded in-memory map (cleared when it grows too large); the cooldown is
    per user per action so normal multi-action use is not penalised.
    """
    cooldown = config.MONEY_CMD_COOLDOWN_SECONDS
    if cooldown <= 0:
        return None
    key = (tg_id, action)
    now = _now()
    last = _money_cmd_last.get(key, 0.0)
    if now - last < cooldown:
        return f"⏳ Слишком часто. Подожди {max(1, int(cooldown - (now - last)) + 1)} сек."
    if len(_money_cmd_last) > 100_000:
        _money_cmd_last.clear()
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
        ]
    )


def _qr_bytes(data: str) -> bytes | None:
    try:
        return qrlib.qr_bytes(data)
    except Exception:
        return None


async def _edit_menu(cb: types.CallbackQuery, text: str, reply_markup=None) -> None:
    """Edit a menu message, falling back to caption for media messages."""
    try:
        await cb.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await cb.message.edit_caption(caption=text, reply_markup=reply_markup)
        except Exception:
            pass
