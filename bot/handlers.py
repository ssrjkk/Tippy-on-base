"""Telegram handlers: tips, deposits, wallet linking, leaderboards, bets, donations."""

import json
import re
import time
from datetime import datetime
from decimal import Decimal

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from eth_utils import is_address, to_checksum_address

from . import base, config, wallets
from . import qr as qrlib
from .ledger import ledger

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
    "🤖 <b>Base TipBot</b> — экономика сообщества в USDC на <b>Base</b>.\n"
    "🟦 Сеть Base · монета USDC · все переводы в блокчейне\n\n"
    "💸 <b>Чаевые</b>\n"
    "• /tip 5 @nick — кинуть 5 USDC\n"
    "• /tip 5 (ответом на сообщение) — кинуть автору\n"
    "• 🔥/❤️/⚡/👏/🎉 на сообщение — реакция-чаевые (в группах)\n"
    "• /rain 10 — разбросать 10 USDC случайным участникам группы 🌧️\n\n"
    "🎲 <b>Ставки</b>\n"
    "• /bet create &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt; [24h] — создать\n"
    "• /bets — открытые ставки (кнопки)\n"
    "• /bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt; — поставить\n"
    "• /mybets — твои позиции\n"
    "• /resolve &lt;id&gt; &lt;номер&gt; — закрыть (создатель; на карточке рынка есть кнопка 🏁)\n"
    "• /cancel &lt;id&gt; — отменить / вернуть деньги после истечения\n\n"
    "💰 <b>Кошелёк</b>\n"
    "• /donate — твоя страница донатов с QR\n"
    "• /deposit — QR + адрес для пополнения\n"
    "• /link &lt;адрес&gt; — привязать кошелёк (авто-зачисление)\n"
    "• /withdraw &lt;адрес&gt; &lt;сумма&gt; — вывод (комиссия 1%, мин. 1 USDC)\n\n"
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


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Баланс", callback_data="bal"),
                InlineKeyboardButton(text="💳 Пополнить", callback_data="dep"),
            ],
            [
                InlineKeyboardButton(text="🎲 Ставки", callback_data="bets"),
                InlineKeyboardButton(text="💛 Донаты", callback_data="donate"),
            ],
            [
                InlineKeyboardButton(text="🏆 Топ", callback_data="top"),
                InlineKeyboardButton(text="🧾 История", callback_data="hist"),
            ],
            [
                InlineKeyboardButton(text="📊 Статы", callback_data="stats"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
            ],
        ]
    )


@router.message(Command("start", "help"))
async def cmd_start(message: types.Message, command: CommandObject) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    args = command.args
    if command.command == "start" and args:
        m = DONATE_LINK_RE.match(args)
        if m:
            await _donate_landing(message, int(m.group(1)))
            return
        m = BET_LINK_RE.match(args)
        if m:
            await _send_market_deep_link(message, int(m.group(1)))
            return
        m = PAYWALL_LINK_RE.match(args)
        if m:
            await _send_paywall_deep_link(message, int(m.group(1)))
            return
    if command.command == "start":
        name = message.from_user.username or f"id{message.from_user.id}"
        bal = ledger.balance(message.from_user.id)
        welcome = (
            f"👋 Привет, <b>@{name}</b>!\n\n"
            f"🟦 <b>Base TipBot</b> — USDC-экономика прямо в Telegram:\n"
            f"💸 чаевые и реакции · 🎁 донат-страницы · 🎯 рынки предсказаний\n\n"
            f"💰 Баланс: <b>{bal:.6f}".rstrip("0").rstrip(".") + " USDC</b>\n\n"
            "Что попробовать?\n"
            "• <b>/deposit</b> — пополнить (QR)\n"
            "• <b>/bets</b> — поставить на рынок\n"
            "• <b>/tip 1 @ник</b> — кинуть чаевые\n"
            "• <b>/help</b> — все команды\n\n"
            "🏗️ Работает на <b>Base</b> — дешёвой L2 от Coinbase · base.org\n"
            "🧑‍💻 Автор: @b2wmain · @ssrjkk · x.com/ludych1 · github.com/ssrjkk"
        )
        await message.answer(welcome, reply_markup=_menu_kb())
        return
    await message.answer(HELP, reply_markup=_menu_kb())


@router.message(Command("menu"))
async def cmd_menu(message: types.Message) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    bal = ledger.balance(message.from_user.id)
    await message.answer(
        f"💰 Баланс: <b>{bal:.6f}".rstrip("0").rstrip(".") + " USDC</b>",
        reply_markup=_menu_kb(),
    )


async def _donate_landing(message: types.Message, target_id: int) -> None:
    creator = ledger.username_of(target_id) or f"id{target_id}"
    addr = base.hot_wallet()
    caption = (
        f"💛 Поддержать <b>@{creator}</b>\n\n"
        f"Отправь USDC (<b>сеть Base</b>) на адрес:\n"
        f"<code>{addr}</code>\n\n"
        f"Зачислится на баланс отправителя автоматически после привязки "
        f"кошелька /link. Передать чаевые @{creator}: /tip 5 @{creator}. Спасибо! 🫡"
    )
    qr = _qr_bytes(addr)
    if qr:
        await message.answer_photo(
            BufferedInputFile(qr, filename="qr.png"),
            caption=caption,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💛 Хочу такую же страницу", callback_data="donate")]
                ]
            ),
        )
    else:
        await message.answer(caption)
    ledger.ensure_user(message.from_user.id, message.from_user.username)


async def _send_market_deep_link(message: types.Message, bet_id: int) -> None:
    """?start=bet_<id> from a shared market page: show the market + bet buttons.

    Turns the web dashboard into an onboarding funnel — anyone who opens a
    shared market link lands here and can place a bet in two taps.
    """
    view = ledger.market_view(bet_id)
    if not view:
        await message.answer(
            "🎲 Рынок не найден или ещё не открыт.\nОткрытые рынки: /bets"
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{o['index'] + 1}) {o['label'][:22]} — {o['probability']}%",
                    callback_data=f"betq:{bet_id}:{o['index']}",
                )
            ]
            for o in view["options"]
        ]
        + [[InlineKeyboardButton(text="🎲 Все рынки", callback_data="bets")]]
    )
    await message.answer(
        "🎯 Ты пришёл по ссылке на рынок!\n\n" + _market_detail_text(view, message.from_user.id),
        reply_markup=kb,
    )


async def _send_paywall_deep_link(message: types.Message, item_id: int) -> None:
    """?start=paywall_<id> from a shared Farcaster Frame / page: show the paid
    post with a one-tap buy button — the Frame funnel lands here."""
    item = ledger.paywall_item(item_id)
    if item is None:
        await message.answer("🔐 Пост не найден. Все посты: /paywall list")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔓 Купить за {_fmt(int(item['price_micro']))} USDC",
                    callback_data=f"paywall_buy:{item_id}",
                )
            ],
            [InlineKeyboardButton(text="🔐 Все посты", callback_data="paywall_list")],
        ]
    )
    await message.answer(
        f"🔐 Ты пришёл по ссылке на платный пост!\n\n"
        f"<b>{item['title']}</b>\n"
        f"Цена: <b>{_fmt(int(item['price_micro']))} USDC</b>",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("paywall_buy:"))
async def cb_paywall_buy(cb: types.CallbackQuery) -> None:
    """One-tap purchase from a shared deep link (Farcaster Frame funnel)."""
    item_id = int(cb.data.split(":", 1)[1])
    item = ledger.paywall_item(item_id)
    if item is None:
        await _edit_menu(cb, "❌ Пост не найден.")
        return
    res = ledger.buy_paywall(cb.from_user.id, item_id)
    if res == "ok":
        await _edit_menu(cb, f"✅ Куплено за {_fmt(int(item['price_micro']))} USDC.\n\n{item['content']}")
    elif res == "dup":
        await _edit_menu(cb, f"🔓 Уже куплено. Контент:\n\n{item['content']}")
    elif res == "self":
        await _edit_menu(cb, "❌ Это твой пост — покупать его не нужно.")
    else:
        await _edit_menu(
            cb,
            f"❌ Недостаточно средств: нужно {_fmt(int(item['price_micro']))} USDC.\nПополни: /deposit",
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


@router.callback_query(F.data.in_({"bal", "dep", "top", "hist", "bets", "donate", "stats", "settings", "betcreate"}))
async def on_menu(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    if cb.data == "bal":
        await _edit_menu(cb, await _balance_text(user.id))
    elif cb.data == "dep":
        await _edit_menu(cb, await _deposit_text(user.id))
    elif cb.data == "top":
        await _edit_menu(cb, await _top_text())
    elif cb.data == "hist":
        await _edit_menu(cb, await _history_text(user.id))
    elif cb.data == "bets":
        text, kb = await _bets_text(user.id)
        await _edit_menu(cb, text, kb)
    elif cb.data == "donate":
        await _edit_menu(cb, await _donate_text(cb.message.bot, user.id))
    elif cb.data == "stats":
        await _edit_menu(cb, await _stats_text(user.id))
    elif cb.data == "settings":
        text, kb = await _settings_kb_text(user.id)
        await _edit_menu(cb, text, kb)
    elif cb.data == "betcreate":
        await _edit_menu(
            cb,
            "🎲 <b>Создание рынка</b>\n\n"
            "/bet create &lt;вопрос&gt; | &lt;вариант1&gt; | &lt;вариант2&gt; [24h]\n\n"
            "До 4 вариантов, опционально дедлайн (например <i>24h</i> или <i>7d</i>). "
            "Создатель получает 2% от выигрыша победителя.",
        )
    elif cb.data == "paywall_list":
        rows = ledger.paywall_items_list()
        if not rows:
            text = "🔐 Платных постов пока нет. Создай первый: /paywall create 5 Заголовок"
            await _edit_menu(cb, text)
        else:
            lines = [
                f"#{r['id']} — {r['title']} — <b>{_fmt(int(r['price_micro']))} USDC</b>"
                f"{' ✅' if ledger.paywall_purchased(int(r['id']), cb.from_user.id) else ''}"
                for r in rows
            ]
            await _edit_menu(cb, "🔐 <b>Платные посты</b>\n\n" + "\n".join(lines))
    await cb.answer()


async def _settings_kb_text(tg_id: int) -> tuple[str, InlineKeyboardMarkup]:
    s = ledger.get_settings(tg_id)
    react = "✅ вкл" if s["reaction_tips"] else "⛔ выкл"
    notif = "✅ вкл" if s["notify_deposits"] else "⛔ выкл"
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"⚡ <b>Реакции-чаевые</b> — {'включены' if s['reaction_tips'] else 'выключены'}.\n"
        "Когда ставишь реакцию на сообщение — автору начисляются USDC.\n\n"
        f"🔔 <b>Уведомления о депозитах</b> — {'включены' if s['notify_deposits'] else 'выключены'}.\n"
        "Сообщение при зачислении депозита."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⚡ Реакции-чаевые: {react}",
                    callback_data="set:react",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔔 Депозиты: {notif}",
                    callback_data="set:notif",
                )
            ],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="menu")],
        ]
    )
    return text, kb


@router.message(Command("settings"))
async def cmd_settings(message: types.Message) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    text, kb = await _settings_kb_text(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("set:"))
async def cb_settings(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    _, key = cb.data.split(":")
    if key == "react":
        cur = ledger.get_settings(user.id)["reaction_tips"]
        ledger.set_setting(user.id, "reaction_tips", not cur)
    elif key == "notif":
        cur = ledger.get_settings(user.id)["notify_deposits"]
        ledger.set_setting(user.id, "notify_deposits", not cur)
    else:
        await cb.answer()
        return
    text, kb = await _settings_kb_text(user.id)
    await _edit_menu(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data == "menu")
async def cb_menu(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    bal = ledger.balance(user.id)
    await _edit_menu(
        cb,
        f"💰 Баланс: <b>{bal:.6f}".rstrip("0").rstrip(".") + " USDC</b>",
        _menu_kb(),
    )
    await cb.answer()


@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message) -> None:
    if config.ADMIN_TG_ID is None or message.from_user.id != config.ADMIN_TG_ID:
        await message.answer("❌ Только для владельца бота.")
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /broadcast &lt;текст&gt;")
        return
    sent = 0
    for row in ledger.all_users():
        try:
            await message.bot.send_message(row["tg_id"], parts[1])
            sent += 1
        except Exception:
            pass
    await message.answer(f"📣 Разослано: {sent} пользователям.")


@router.message(Command("rain"))
async def cmd_rain(message: types.Message) -> None:
    chat = message.chat
    if chat.type not in ("group", "supergroup"):
        await message.answer("🌧️ /rain работает только в группах: разбросай USDC активным участникам!")
        return
    parts = message.text.strip().split()
    if len(parts) not in (2, 3) or not AMOUNT_RE.match(parts[1]):
        await message.answer("Формат: /rain 10  (или /rain 10 15 — на 15 человек)")
        return
    amount = Decimal(parts[1])
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if amount > config.RAIN_MAX_USDC:
        await message.answer(f"Максимум за один дождь: <b>{config.RAIN_MAX_USDC:.0f} USDC</b>.")
        return
    count = int(parts[2]) if len(parts) == 3 else 5
    if count < 1:
        await message.answer("Количество участников должно быть больше нуля.")
        return
    if count > config.RAIN_MAX_RECIPIENTS:
        await message.answer(f"Максимум участников: <b>{config.RAIN_MAX_RECIPIENTS}</b>.")
        return
    wait = _throttle(message.from_user.id, "rain")
    if wait:
        await message.answer(wait)
        return
    amount_micro = _to_micro(amount)
    ok, text, chosen = ledger.rain(chat.id, message.from_user.id, amount_micro, count)
    if not ok:
        await message.answer(text)
        return
    names = []
    for tid in chosen[:8]:
        uname = ledger.username_of(tid) or f"id{tid}"
        names.append(f"@{uname}")
    tail = f" и ещё {len(chosen) - 8}" if len(chosen) > 8 else ""
    await message.answer(f"{text}\n🎁 Получили: {', '.join(names)}{tail}\n\n🌧️ Дождь закончился!")


async def _balance_text(tg_id: int) -> str:
    ledger.ensure_user(tg_id, None)
    bal = ledger.balance(tg_id)
    addr = ledger.linked_address(tg_id)
    link_line = f"\n🔗 Кошелёк: <code>{_esc(addr)}</code>" if addr else "\n🔗 Кошелёк не привязан — /link"
    pos = ledger.user_positions(tg_id)
    bets_line = ""
    if pos:
        stake = sum(p["stake_micro"] for p in pos)
        potential = sum(p["potential_micro"] for p in pos)
        bets_line = (
            f"\n🎲 В игре: <b>{len(pos)}</b> позиция(и) на <b>{_fmt(stake)} USDC</b>\n"
            f"🏆 Потенциальный выигрыш: <b>{_fmt(potential)} USDC</b>\n"
            f"📌 Твои ставки: /mybets"
        )
    fees = ledger.creator_fees(tg_id)
    fees_line = f"\n🧾 Заработано на рынках: <b>{_fmt(fees)} USDC</b>" if fees else ""
    return (
        f"💰 Баланс: <b>{bal:.6f}".rstrip("0").rstrip(".")
        + f" USDC</b>{link_line}{bets_line}{fees_line}"
    )


async def _deposit_text(tg_id: int) -> str:
    addr = base.hot_wallet()
    linked = ledger.linked_address(tg_id)
    if linked:
        return (
            f"💳 Отправь USDC на адрес бота\n"
            f"🟦 <b>Сеть Base</b> · монета USDC (ERC-20)\n\n"
            f"<code>{addr}</code>\n\n"
            f"С твоего привязанного кошелька <code>{_esc(linked)}</code> — зачислится автоматически ✅\n"
            f"🏗️ Операция в блокчейне, видна всем: basescan.org\n\n"
            f"⚠️ <b>Дисклеймер:</b> средства хранит бот (кастодиальный кошелёк). "
            f"Свой ключ и сид-фразу можно забрать в любой момент: /wallet export"
        )
    return (
        f"💳 Отправь USDC на адрес бота\n"
        f"🟦 <b>Сеть Base</b> · монета USDC (ERC-20)\n\n"
        f"<code>{addr}</code>\n\n"
        f"После отправки пришли /claim <i>&lt;tx_hash&gt;</i>.\n"
        f"<b>Удобнее:</b> привяжи кошелёк — /link, и депозиты будут зачисляться сами.\n"
        f"🏗️ Операция в блокчейне, видна всем: basescan.org\n\n"
        f"⚠️ <b>Дисклеймер:</b> средства хранит бот (кастодиальный кошелёк). "
        f"Свой ключ и сид-фразу можно забрать в любой момент: /wallet export"
    )


async def _donate_text(bot, tg_id: int) -> str:
    uname = await _get_bot_username(bot)
    link = f"https://t.me/{uname}?start=donate_{tg_id}"
    return (
        f"💛 <b>Твоя страница донатов</b>\n\n"
        f"Скинь эту ссылку куда угодно — по ней откроется твой адрес для USDC:\n"
        f"<code>{link}</code>\n\n"
        f"По ссылке сразу видно, кому и куда платить — без посредников."
    )


@router.message(Command("balance"))
async def cmd_balance(message: types.Message) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _balance_text(message.from_user.id))


@router.message(Command("deposit"))
async def cmd_deposit(message: types.Message) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    text = await _deposit_text(message.from_user.id)
    qr = _qr_bytes(str(base.hot_wallet()))
    if qr:
        await message.answer_photo(
            BufferedInputFile(qr, filename="qr.png"),
            caption=text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Сканируй и отправь USDC",
                            url="https://basescan.org/address/"
                            + str(base.hot_wallet()),
                        )
                    ]
                ]
            ),
        )
    else:
        await message.answer(text)


@router.message(Command("donate"))
async def cmd_donate(message: types.Message) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _donate_text(message.bot, message.from_user.id))


@router.message(Command("claim"))
async def cmd_claim(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 2 or not TX_HASH_RE.match(parts[1]):
        await message.answer("Формат: /claim <i>&lt;0x…tx_hash&gt;</i>")
        return
    ok, amount_micro, sender, reason = ledger.claim(message.from_user.id, parts[1].lower())
    if not ok:
        if reason == "not_owner":
            await message.answer(
                f"❌ Этот депозит отправлен с кошелька <code>{_esc(sender)}</code>.\n"
                f"Зачислить его может только владелец кошелька. Привяжи его: /link <i>&lt;адрес&gt;</i>\n"
                f"(привязка автоматически зачтёт все твои депозиты)"
            )
            return
        await message.answer(
            "❌ Не нашёл такой незачтенной транзакции (или уже зачтена).\n"
            "Проверь сеть <b>Base</b> и что USDC отправлен на адрес бота."
        )
        return
    await message.answer(
        f"✅ Зачтено <b>{_fmt(amount_micro)} USDC</b> от <code>{_esc(sender)}</code>"
    )


@router.message(Command("link"))
async def cmd_link(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 2 or not USDC_ADDR_RE.match(parts[1]) or not is_address(parts[1]):
        await message.answer("Формат: /link <i>&lt;0x…адрес&gt;</i>")
        return
    address = to_checksum_address(parts[1])
    nonce = ledger.new_link_nonce(message.from_user.id, address)
    sign_text = f"Base TipBot: link {message.from_user.id}:{nonce}"
    await message.answer(
        "🔗 <b>Привязка кошелька</b>\n\n"
        "Подпиши сообщение в своём кошельке (WalletConnect / MetaMask / любой)\n\n"
        "🖊 Сообщение:\n"
        f"<code>{sign_text}</code>\n\n"
        "Потом пришли сюда /confirm <i>&lt;0x…подпись&gt;</i>"
    )


@router.message(Command("confirm"))
async def cmd_confirm(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 2 or not SIG_RE.match(parts[1]):
        await message.answer("Формат: /confirm <i>&lt;0x…подпись&gt;</i>")
        return
    row = ledger.get_link_nonce(message.from_user.id)
    if not row:
        await message.answer("❌ Сначала начни привязку: /link <i>&lt;адрес&gt;</i>")
        return
    address, nonce = row["address"], row["nonce"]
    if int(time.time()) - row["created_at"] > config.LINK_NONCE_TTL_SECONDS:
        await message.answer(
            f"⏳ Код привязки устарел (действует {config.LINK_NONCE_TTL_SECONDS // 60} мин). "
            f"Начни заново: /link <i>&lt;адрес&gt;</i>"
        )
        return
    sign_text = f"Base TipBot: link {message.from_user.id}:{nonce}"
    try:
        recovered = base.recover_signer(sign_text, parts[1])
    except Exception:
        await message.answer("❌ Не удалось разобрать подпись.")
        return
    if recovered.lower() != address.lower():
        await message.answer(
            f"❌ Подпись не совпадает: подписавший <code>{_esc(recovered)}</code>, "
            f"ожидали <code>{_esc(address)}</code>"
        )
        return
    ledger.confirm_link(message.from_user.id, address, nonce)
    claimed = ledger.claim_for_sender(message.from_user.id, address)
    extra = f"\nСразу зачислено: {len(claimed)} депозит(ов)" if claimed else ""
    await message.answer(
        f"✅ Кошелёк <code>{_esc(address)}</code> привязан.\n"
        f"Теперь депозиты с него зачисляются автоматически.{extra}"
    )


@router.message(Command("wallet"))
async def cmd_wallet(message: types.Message) -> None:
    """Personal wallet: /wallet (address) or /wallet export (key + seed)."""
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.strip().split()
    if len(parts) > 1 and parts[1].lower() == "export":
        row = ledger.get_wallet(message.from_user.id)
        if not row:
            row = _ensure_wallet(message.from_user.id)
        key = wallets.decrypt(row["key_enc"])
        seed = wallets.decrypt(row["seed_enc"])
        await message.answer(
            f"🔑 <b>Твой кошелёк</b>\n\n"
            f"Адрес: <code>{row['address']}</code>\n"
            f"Приватный ключ: <code>{key}</code>\n"
            f"Сид-фраза: <code>{seed}</code>\n\n"
            f"⚠️ <b>Не показывай это никому.</b> Кто знает ключ — тот владеет средствами. "
            f"Экспортнув ключ, ты можешь забрать баланс на любой кошелёк (/withdraw)."
        )
        return
    row = ledger.get_wallet(message.from_user.id)
    if not row:
        row = _ensure_wallet(message.from_user.id)
    await message.answer(
        f"👛 <b>Твой кошелёк</b>\n\n"
        f"Адрес: <code>{row['address']}</code>\n"
        f"🟦 Сеть Base · монета USDC\n\n"
        f"Ключ и сид-фраза доступны: /wallet export\n"
        f"Привязать свой кошелёк сид-фразой: /import &lt;фраза&gt;"
    )


@router.message(Command("import"))
async def cmd_import(message: types.Message) -> None:
    """Attach an existing wallet by BIP-39 seed phrase (self-custody import)."""
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Формат: /import <i>&lt;12 или 24 слова&gt;</i>")
        return
    seed = " ".join(parts[1:])
    if not wallets.is_valid_seed(seed):
        await message.answer("❌ Сид-фраза должна содержать 12 или 24 слова.")
        return
    try:
        address, key = wallets.wallet_from_seed(seed)
    except Exception:
        await message.answer("❌ Не удалось восстановить кошелёк из этой сид-фразы.")
        return
    own = ledger.wallet_address(message.from_user.id)
    if own and own.lower() != address.lower():
        await message.answer(
            f"⚠️ У тебя уже есть кошелёк <code>{_esc(own)}</code>. "
            f"Сначала выведи с него средства (/withdraw), затем импортируй новый."
        )
        return
    if not own and ledger.wallet_address_exists(address):
        await message.answer(
            f"❌ Кошелёк <code>{_esc(address)}</code> уже привязан к другому пользователю."
        )
        return
    ledger.save_wallet(message.from_user.id, address, wallets.encrypt(key), wallets.encrypt(seed))
    await message.answer(
        f"✅ Кошелёк <code>{_esc(address)}</code> импортирован.\n"
        f"Ключ и сид хранятся зашифрованными, выгрузить: /wallet export"
    )


@router.message(Command("export"))
async def cmd_export(message: types.Message) -> None:
    """Owner only: hot-wallet private key (operational access)."""
    if message.from_user.id != config.ADMIN_TG_ID:
        await message.answer("❌ Только владелец бота.")
        return
    await message.answer(
        f"🟦 <b>Hot wallet бота</b>\n\n"
        f"Адрес: <code>{base.hot_wallet()}</code>\n"
        f"Приватный ключ: <code>{config.HOT_WALLET_KEY}</code>\n\n"
        f"⚠️ Это ключ, который держит балансы пользователей. Никому не передавай."
    )


def _ensure_wallet(tg_id: int) -> dict:
    """Create a personal wallet for the user if missing; returns the row."""
    row = ledger.get_wallet(tg_id)
    if row:
        return row
    address, key, seed = wallets.new_wallet()
    ledger.save_wallet(tg_id, address, wallets.encrypt(key), wallets.encrypt(seed))
    return ledger.get_wallet(tg_id)


@router.message(Command("tip"))
async def cmd_tip(message: types.Message) -> None:
    parts = message.text.strip().split()

    if len(parts) >= 2 and AMOUNT_RE.match(parts[1]):
        amount = Decimal(parts[1])
        rest = parts[2:]
    elif len(parts) == 1:
        amount = Decimal("1")
        rest = []
    else:
        await message.answer("Формат: /tip 5 @nick  (или /tip 5 ответом на сообщение)")
        return
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if amount > config.MAX_TIP_USDC:
        await message.answer(f"Максимум чаевых за раз: <b>{config.MAX_TIP_USDC:.0f} USDC</b>.")
        return
    amount_micro = _to_micro(amount)

    if message.reply_to_message and message.reply_to_message.from_user:
        to_id = message.reply_to_message.from_user.id
        to_name = message.reply_to_message.from_user.username
    elif rest:
        target = rest[0]
        if not target.startswith("@"):
            await message.answer("Укажи получателя: /tip 5 @username")
            return
        username = target[1:]
        to_id = ledger.find_by_username(username)
        if to_id is None:
            to_id = await _resolve_in_chat(message, username)
        if to_id is None:
            await message.answer(
                f"Не нашёл @{username}. Пусть он напишет боту в ЛС (/start), "
                f"тогда мы его запомним."
            )
            return
        to_name = username
    else:
        await message.answer("Кому кидаем? /tip 5 @nick — или ответь на сообщение и напиши /tip 5")
        return

    if to_id == message.from_user.id:
        await message.answer("Себе кидать нельзя 😅")
        return

    wait = _throttle(message.from_user.id, "tip")
    if wait:
        await message.answer(wait)
        return

    if not ledger.transfer(message.from_user.id, to_id, amount_micro):
        await message.answer("❌ Недостаточно баланса. Пополни: /deposit")
        return

    sender_name = message.from_user.username or f"id{message.from_user.id}"
    mention = f"<a href='tg://user?id={to_id}'>@{to_name or to_id}</a>"
    bal = ledger.balance(message.from_user.id)
    await message.answer(
        f"💸 <b>{sender_name}</b> → {mention}\n"
        f"<b>{_fmt(amount_micro)} USDC</b>\n"
        f"Остаток: {bal:.4f}".rstrip("0").rstrip(".") + " USDC"
    )
    await _notify_tip_received(message, to_id, amount_micro, sender_name)


async def _notify_tip_received(message: types.Message, to_id: int, amount_micro: int, sender: str) -> None:
    if to_id == message.from_user.id:
        return
    try:
        await message.bot.send_message(
            to_id,
            f"💸 <b>Тебе кинули {_fmt(amount_micro)} USDC</b>\n"
            f"От: @{sender}\n\nБаланс: /balance",
        )
    except Exception:
        pass


async def _resolve_in_chat(message: types.Message, username: str) -> int | None:
    try:
        async for member in message.chat.get_members(limit=200):
            user = member.user
            if user.username and user.username.lower() == username.lower():
                ledger.ensure_user(user.id, user.username)
                return user.id
    except Exception:
        return None
    return None


@router.message(Command("withdraw"))
async def cmd_withdraw(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 3 or not USDC_ADDR_RE.match(parts[1]) or not AMOUNT_RE.match(parts[2]):
        await message.answer("Формат: /withdraw <i>&lt;адрес&gt; &lt;сумма&gt;</i>")
        return
    to_address = parts[1]
    if not is_address(to_address):
        await message.answer("❌ Непохоже на валидный адрес (0x + 40 hex).")
        return
    amount = Decimal(parts[2])
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if amount < config.MIN_WITHDRAW_USDC:
        await message.answer(
            f"Минимум для вывода: <b>{config.MIN_WITHDRAW_USDC:.2f} USDC</b>."
        )
        return
    if ledger.withdrawals_today(message.from_user.id) >= config.MAX_WITHDRAWS_PER_DAY:
        await message.answer(
            f"⏳ Лимит <b>{config.MAX_WITHDRAWS_PER_DAY} выводов в сутки</b>. "
            f"Попробуй завтра."
        )
        return
    wait = _throttle(message.from_user.id, "withdraw")
    if wait:
        await message.answer(wait)
        return
    amount_micro = _to_micro(amount)
    fee_micro = base.withdraw_fee(amount_micro)
    total_micro = amount_micro + fee_micro
    bal = ledger.balance(message.from_user.id)
    if bal < Decimal(total_micro) / Decimal(10**config.USDC_DECIMALS):
        await message.answer(
            f"❌ Недостаточно баланса. Нужно <b>{_fmt(total_micro)} USDC</b> "
            f"(сумма + комиссия {_fmt(fee_micro)}).\nТвой баланс: <b>{bal:.6f}".rstrip("0").rstrip(".") + " USDC</b>"
        )
        return

    # Atomically debit and reserve the withdrawal as 'pending' BEFORE touching
    # the chain, so a crash between the debit and the send is detected and
    # refunded by the withdraw watcher (tx_hash stays NULL -> refund after timeout).
    wd_id = ledger.reserve_withdraw(
        message.from_user.id, to_address, amount_micro, fee_micro
    )
    if wd_id is None:
        await message.answer("❌ Недостаточно баланса.")
        return

    try:
        tx_hash = base.send_usdc(to_address, amount_micro)
        ledger.mark_withdraw_done(wd_id, tx_hash)
        ledger.record_withdraw_fee(
            message.from_user.id, to_address, fee_micro, tx_hash
        )
        await message.answer(
            f"✅ Отправлено <b>{_fmt(amount_micro)} USDC</b> "
            f"(комиссия {_fmt(fee_micro)})\n"
            f"Tx: <code>https://basescan.org/tx/{tx_hash}</code>"
        )
    except Exception as e:
        # Full refund (incl. fee) on failure — never charge for a failed send.
        ledger.refund_withdraw(wd_id, message.from_user.id, total_micro)
        await message.answer(f"❌ Ошибка отправки: {e}")


# ---------- bets / prediction markets ----------


@router.message(Command("bet"))
async def cmd_bet(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Формат:\n• /bet create &lt;вопрос&gt; | &lt;вариант1&gt; | &lt;вариант2&gt;\n• /bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;")
        return
    if parts[1] == "create":
        await _bet_create(message, parts)
        return
    await _bet_place(message, parts)


async def _bet_create(message: types.Message, parts: list[str]) -> None:
    body = " ".join(parts[2:])
    segs = [s.strip() for s in body.split("|") if s.strip()]

    deadline = None
    if len(segs) >= 3:
        last = segs[-1]
        if DEADLINE_RE.match(last.lower()):
            deadline = _parse_deadline(last)
            segs = segs[:-1]
        else:
            parts2 = last.rsplit(None, 1)
            if len(parts2) == 2 and DEADLINE_RE.match(parts2[1].lower()):
                deadline = _parse_deadline(parts2[1])
                segs = [*segs[:-1], parts2[0].strip()]

    if len(segs) < 3:
        await message.answer(
            "Формат: /bet create <i>&lt;вопрос&gt; | &lt;вариант1&gt; | &lt;вариант2&gt;</i>\n"
            "До 4 вариантов, опционально дедлайн: 24h / 7d. Пример:\n"
            "/bet create Кто победит? | Трамп | Харрис 24h"
        )
        return
    if len(segs) > 5:
        await message.answer("Максимум 4 варианта.")
        return
    question = segs[0]
    options = segs[1:]
    if len(question) > 200:
        await message.answer("Слишком длинный вопрос (макс 200 символов).")
        return
    for o in options:
        if len(o) > config.MAX_OPTION_LEN:
            await message.answer(
                f"Вариант длиннее {config.MAX_OPTION_LEN} символов: <i>{o[:40]}…</i>"
            )
            return
    bet_id = ledger.create_bet(message.from_user.id, question, options, close_at=deadline)
    dl = f"\n⏰ Приём ставок до: {datetime.fromtimestamp(deadline).strftime('%d.%m %H:%M')}" if deadline else "\n⌛ Закрытие: /resolve (только ты)"
    await message.answer(
        f"🎲 Ставка #{bet_id} создана!\n\n"
        f"<b>{question}</b>\n"
        + "\n".join(f"{i + 1}) {o}" for i, o in enumerate(options))
        + dl
        + f"\n\nСтавят: /bet {bet_id} &lt;номер&gt; &lt;сумма&gt; или кнопки: /bets"
    )


def _parse_deadline(s: str) -> int | None:
    m = DEADLINE_RE.match(s.lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    secs = n * (3600 if unit == "h" else 86400)
    return int(time.time()) + secs


async def _bet_place(message: types.Message, parts: list[str]) -> None:
    if len(parts) != 4 or not BET_ID_RE.match(parts[1]) or not AMOUNT_RE.match(parts[3]):
        await message.answer("Формат: /bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;")
        return
    try:
        bet_id = int(parts[1])
        option_idx = int(parts[2]) - 1
    except ValueError:
        await message.answer("Формат: /bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;")
        return
    amount = Decimal(parts[3])
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if amount > config.MAX_BET_USDC:
        await message.answer(f"Максимум за одну ставку: <b>{config.MAX_BET_USDC:.0f} USDC</b>.")
        return
    amount_micro = _to_micro(amount)

    wait = _throttle(message.from_user.id, "bet")
    if wait:
        await message.answer(wait)
        return

    bet = ledger.get_bet(bet_id)
    if not bet:
        await message.answer("Ставка не найдена.")
        return
    options = json.loads(bet["options"])
    if option_idx < 0 or option_idx >= len(options):
        await message.answer("Неверный номер варианта.")
        return

    result = ledger.place_bet(bet_id, message.from_user.id, option_idx, amount_micro)
    if result == "closed":
        await message.answer("Эта ставка уже закрыта.")
        return
    if result == "deadline":
        await message.answer("⏰ Время приёма ставок истекло. Жди решения создателя: /bets")
        return
    if result == "badopt":
        await message.answer("Неверный номер варианта.")
        return
    if result == "balance":
        await message.answer("❌ Недостаточно баланса. Пополни: /deposit")
        return

    bal = ledger.balance(message.from_user.id)
    await message.answer(
        f"✅ Ставка принята!\n"
        f"🎲 #{bet_id} — <b>{options[option_idx]}</b> на <b>{_fmt(amount_micro)} USDC</b>\n"
        f"Остаток: {bal:.4f}".rstrip("0").rstrip(".") + " USDC"
    )


def _rel_deadline(ts: int) -> str:
    """'через 2ч 15м' / 'осталось 3д 4ч' — relative deadline for cards."""
    left = ts - int(time.time())
    if left <= 0:
        return "дедлайн прошёл"
    days, rem = divmod(left, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"осталось {days}д {hours}ч"
    if hours:
        return f"осталось {hours}ч {mins}м"
    return f"осталось {mins}м"


async def _bet_card(bet, tg_id: int | None = None) -> str:
    view = ledger.market_view(bet["id"])
    if not view:
        return ""
    return _market_detail_text(view, tg_id)


def _market_detail_text(view: dict, tg_id: int | None = None) -> str:
    lines = [f"🎯 #{view['id']} <b>{view['question']}</b>"]
    if view["status"] == "resolved":
        winner = (
            view["options"][view["winner"]]["label"]
            if view["winner"] is not None and view["winner"] < len(view["options"])
            else "?"
        )
        lines.append(f"✅ <b>Решён:</b> {winner}")
    elif view["status"] == "cancelled":
        lines.append("❌ Отменён — деньги возвращены всем.")
    elif view["expired"]:
        lines.append("🕳️ <b>Рынок истёк</b> — деньги можно вернуть: /cancel <id>")
    elif view["close_at"]:
        lines.append(f"⏰ {_rel_deadline(view['close_at'])} · создатель: @{view['creator']['username'] or ('id' + str(view['creator']['id']))}")
    else:
        lines.append(f"⌛ Закрытие: создателем /resolve · @{view['creator']['username'] or ('id' + str(view['creator']['id']))}")
    my_stake = ledger.user_bet_stake(view["id"], tg_id) if tg_id else {}
    for o in view["options"]:
        mine = f" · <b>твоя ставка: {_fmt(my_stake[o['index']])}</b>" if my_stake.get(o["index"]) else ""
        backers = f"{o['backers']}👤" if o["backers"] else ""
        lines.append(
            f"{o['index'] + 1}) {o['label']} — <b>{_fmt(o['pool'])} USDC</b> "
            f"({o['probability']}%, {backers}){mine}"
        )
    lines.append(f"Пул итого: <b>{_fmt(view['pot'])} USDC</b> · участников: {view['total_backers']}")
    lines.append("\nКомиссия на выигрыш: 2% (создателю рынка)")
    return "\n".join(lines)


async def _bets_text(tg_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Compact market listing: one line per market + a button per market.
    Full cards open by tapping a button (keeps the message short in groups)."""
    bets = ledger.open_bets(8)
    if not bets:
        return (
            "🎲 Открытых ставок нет.\n"
            "Создай первую: /bet create &lt;вопрос&gt; | &lt;вариант1&gt; | &lt;вариант2&gt;",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Создать рынок", callback_data="betcreate")]
                ]
            ),
        )
    lines = ["🎲 <b>Открытые ставки</b>", ""]
    for b in bets:
        view = ledger.market_view(b["id"])
        if not view:
            continue
        if view["expired"]:
            meta = " 🕳️ истёк — вернуть: /cancel " + str(view["id"])
        elif view["close_at"]:
            meta = " ⏰ " + _rel_deadline(view["close_at"])
        else:
            meta = ""
        lines.append(
            f"#{view['id']} {view['question']} — {_fmt(view['pot'])} USDC · {view['total_backers']}👤{meta}"
        )
    lines.append("\nНажми на рынок — карточка со ставками.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎯 #{b['id']}: {b['question'][:38]}",
                    callback_data=f"market:{b['id']}",
                )
            ]
            for b in bets
        ]
        + [[InlineKeyboardButton(text="➕ Создать рынок", callback_data="betcreate")]]
    )
    return "\n".join(lines), kb


@router.message(Command("bets"))
async def cmd_bets(message: types.Message) -> None:
    text, kb = await _bets_text(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("market:"))
async def cb_market(cb: types.CallbackQuery) -> None:
    bet_id = int(cb.data.split(":", 1)[1])
    view = ledger.market_view(bet_id)
    if not view:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"{o['index'] + 1}) {o['label'][:22]} — {o['probability']}%",
                callback_data=f"betq:{bet_id}:{o['index']}",
            )
        ]
        for o in view["options"]
    ]
    user = cb.from_user
    if user and view["status"] == "open" and user.id == view["creator"]["id"]:
        rows.append(
            [InlineKeyboardButton(text="🏁 Закрыть рынок", callback_data=f"res:{bet_id}")]
        )
    rows.append([InlineKeyboardButton(text="🎲 Все рынки", callback_data="bets")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await _edit_menu(cb, _market_detail_text(view, user.id if user else None), kb)
    await cb.answer()


@router.callback_query(F.data.startswith("res:"))
async def cb_res(cb: types.CallbackQuery) -> None:
    """Creator resolves a market inline: pick the winning option, done."""
    user = cb.from_user
    if not user:
        return
    parts = cb.data.split(":")
    bet_id = int(parts[1])
    view = ledger.market_view(bet_id)
    if not view:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    if view["status"] != "open":
        await cb.answer("Рынок уже закрыт", show_alert=True)
        return
    if user.id != view["creator"]["id"]:
        await cb.answer("Закрыть может только создатель рынка", show_alert=True)
        return
    if len(parts) == 2:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🏆 {o['label'][:30]}",
                        callback_data=f"res:{bet_id}:{o['index']}",
                    )
                ]
                for o in view["options"]
            ]
            + [[InlineKeyboardButton(text="◀️ Назад", callback_data=f"market:{bet_id}")]]
        )
        await _edit_menu(
            cb,
            f"🏁 <b>Закрыть рынок #{bet_id}</b> — «{view['question']}»\n\n"
            f"Кто победил?\nПобедители делят пул ({_fmt(view['pot'])} USDC), "
            f"создатель получает 2% комиссии.",
            kb,
        )
        await cb.answer()
        return
    idx = int(parts[2])
    if idx >= len(view["options"]):
        await cb.answer("Нет такого варианта", show_alert=True)
        return
    ok, msg = ledger.resolve_bet(bet_id, idx, user.id)
    if not ok:
        await cb.answer(msg, show_alert=True)
        return
    await _notify_bet_result(cb.message, bet_id)
    new_view = ledger.market_view(bet_id)
    text = (
        f"✅ <b>Ставка #{bet_id} закрыта!</b>\n{msg}\n\n"
        + _market_detail_text(new_view, user.id)
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Все рынки", callback_data="bets")]
        ]
    )
    await _edit_menu(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.startswith("betq:"))
async def cb_bet_amount(cb: types.CallbackQuery) -> None:
    _, bet_id, opt = cb.data.split(":")
    view = ledger.market_view(int(bet_id))
    if not view:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    label = view["options"][int(opt)]["label"]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{a} USDC",
                    callback_data=f"bets:{bet_id}:{opt}:{a}",
                )
                for a in QUICK_AMOUNTS
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"market:{bet_id}")],
        ]
    )
    await _edit_menu(cb, f"🎯 #{bet_id}: {view['question']}\n\n<b>{label}</b> — сколько ставим?", kb)
    await cb.answer()


@router.callback_query(F.data.startswith("bets:"))
async def cb_bet_place(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    _, bet_id, opt, amt = cb.data.split(":")
    bet_id, opt = int(bet_id), int(opt)
    try:
        amount_micro = _to_micro(Decimal(amt))
    except Exception:
        await cb.answer("Неверная сумма", show_alert=True)
        return

    wait = _throttle(user.id, "bet")
    if wait:
        await cb.answer(wait, show_alert=True)
        return

    bet = ledger.get_bet(bet_id)
    if not bet:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    result = ledger.place_bet(bet_id, user.id, opt, amount_micro)
    if result == "ok":
        options = json.loads(bet["options"])
        bal = ledger.balance(user.id)
        text = (
            f"✅ Ставка принята!\n"
            f"🎯 #{bet_id} — <b>{options[opt]}</b> на <b>{_fmt(amount_micro)} USDC</b>\n"
            f"Остаток: {bal:.4f}".rstrip("0").rstrip(".") + " USDC"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🎯 Рынок", callback_data=f"market:{bet_id}"),
                    InlineKeyboardButton(text="🎲 Все рынки", callback_data="bets"),
                ]
            ]
        )
        await _edit_menu(cb, text, kb)
        await cb.answer()
    elif result == "deadline":
        await cb.answer("⏰ Приём ставок закрыт", show_alert=True)
    elif result == "closed":
        await cb.answer("Рынок уже закрыт", show_alert=True)
    elif result == "balance":
        await cb.answer("❌ Недостаточно баланса. /deposit", show_alert=True)
    else:
        await cb.answer("Что-то пошло не так", show_alert=True)


@router.message(Command("resolve"))
async def cmd_resolve(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 3 or not BET_ID_RE.match(parts[1]):
        await message.answer("Формат: /resolve &lt;id&gt; &lt;номер&gt;")
        return
    bet_id = int(parts[1])
    try:
        winning_idx = int(parts[2]) - 1
    except ValueError:
        await message.answer("Формат: /resolve &lt;id&gt; &lt;номер&gt;")
        return
    ok, msg = ledger.resolve_bet(bet_id, winning_idx, message.from_user.id)
    if not ok:
        await message.answer(f"❌ {msg}")
        return
    await message.answer(f"✅ <b>Ставка #{bet_id} закрыта!</b>\n{msg}\n\nВыплаты разосланы победителям.")
    await _notify_bet_result(message, bet_id)


async def _notify_bet_result(message: types.Message, bet_id: int) -> None:
    bet = ledger.get_bet(bet_id)
    if not bet:
        return
    payouts = ledger.payouts_for(bet_id)
    winner_label = ""
    if bet["winner"] is not None:
        options = json.loads(bet["options"])
        if bet["winner"] < len(options):
            winner_label = options[bet["winner"]]
    by_user: dict[int, list[dict]] = {}
    for p in payouts:
        by_user.setdefault(p["tg_id"], []).append(p)
    for tg_id, rows in by_user.items():
        won = [r for r in rows if r["win"]]
        if won:
            total = sum(r["net_micro"] for r in won)
            line = (
                f"🏆 <b>Ты выиграл {_fmt(total)} USDC!</b>\n"
                f"🎲 #{bet_id} — «{bet['question']}»\n"
                f"Победил: <b>{winner_label}</b>\nБаланс: /balance"
            )
        else:
            labels = "», «".join(r["option"] for r in rows)
            line = (
                f"🎲 Ставка #{bet_id} — «{bet['question']}» закрыта.\n"
                f"Победил: <b>{winner_label}</b>\n"
                f"Твои ставки на «{labels}» не сыграли. Попробуй ещё: /bets"
            )
        try:
            await message.bot.send_message(tg_id, line)
        except Exception:
            pass


async def _notify_bet_cancelled(message: types.Message, bet_id: int) -> None:
    bet = ledger.get_bet(bet_id)
    if not bet:
        return
    positions = ledger._bet_positions(bet_id)
    seen = set()
    for p in positions:
        tg_id = int(p["tg_id"])
        if tg_id in seen:
            continue
        seen.add(tg_id)
        try:
            await message.bot.send_message(
                tg_id,
                f"↩️ Ставка #{bet_id} — «{bet['question']}» отменена.\n"
                f"Деньги возвращены: /history",
            )
        except Exception:
            pass


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 2 or not BET_ID_RE.match(parts[1]):
        await message.answer("Формат: /cancel &lt;id&gt;")
        return
    ok, msg = ledger.cancel_bet(int(parts[1]), message.from_user.id)
    if not ok:
        await message.answer(f"❌ {msg}")
        return
    await message.answer(f"✅ {msg}")
    await _notify_bet_cancelled(message, int(parts[1]))


@router.message(Command("mybets"))
async def cmd_mybets(message: types.Message) -> None:
    positions = ledger.user_positions(message.from_user.id)
    if not positions:
        await message.answer("🎲 У тебя нет открытых позиций. Ставят: /bets")
        return
    lines = ["📌 <b>Твои открытые позиции</b>\n"]
    for p in positions:
        lines.append(
            f"🎯 #{p['bet_id']} <b>{p['question']}</b>\n"
            f"   • {p['option']} — поставлено <b>{_fmt(p['stake_micro'])} USDC</b>\n"
            f"   • потенциальный выигрыш: <b>{_fmt(p['potential_micro'])} USDC</b>"
        )
    await message.answer("\n\n".join(lines))


# ---------- stats / leaderboards ----------


@router.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _stats_text(message.from_user.id))


async def _stats_text(tg_id: int) -> str:
    ledger.ensure_user(tg_id, None)
    sent, received, won, lost = ledger.user_stats(tg_id)
    creator_fees = ledger.creator_fees(tg_id)
    bal = ledger.balance(tg_id)
    fees_line = f"\n🧾 Заработано на рынках: <b>{_fmt(creator_fees)} USDC</b>" if creator_fees else ""
    return (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"💸 Отправил чаевых: <b>{_fmt(sent)} USDC</b>\n"
        f"💛 Получил чаевых: <b>{_fmt(received)} USDC</b>\n"
        f"🏆 Выиграл ставками: <b>{_fmt(won)} USDC</b>\n"
        f"🎲 Поставил в рынках: <b>{_fmt(lost)} USDC</b>\n"
        f"💰 Баланс: <b>{bal:.6f}".rstrip("0").rstrip(".") + " USDC</b>"
        + fees_line
    )


async def _top_text() -> str:
    rows = ledger.top_tippers(10)
    if not rows:
        return "🏆 Пока никто не кидал чаевых. Будь первым!"
    lines = []
    for i, row in enumerate(rows, 1):
        uname = ledger.username_of(row["tg_id"]) or f"id{row['tg_id']}"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
        lines.append(f"{medal} <b>@{uname}</b> — {_fmt(row['total'])} USDC")
    return "🏆 <b>Топ чаевых (все время)</b>\n\n" + "\n".join(lines)


async def _history_text(tg_id: int, limit: int = 15) -> str:
    rows = ledger.history(tg_id, limit)
    if not rows:
        return "🧾 Пока нет операций. Пополни: /deposit"
    lines = []
    for r in rows:
        emoji = KIND_EMOJI.get(r["kind"], "•")
        amt = _fmt(r["amount"])
        if r["kind"] == "tip":
            cid = int(r["counterparty"]) if r["counterparty"].isdigit() else None
            cname = ledger.username_of(cid) if cid else None
            who = f"@{cname}" if cname else (r["counterparty"] or "?")
            lines.append(f"{emoji} {amt} → {who}")
        elif r["kind"] == "deposit":
            lines.append(f"{emoji} +{amt} <code>{_esc(r['counterparty'])}</code>")
        elif r["kind"] == "withdraw":
            lines.append(f"{emoji} −{amt} → <code>{_esc(r['counterparty'])}</code>")
        elif r["kind"] == "bet":
            lines.append(f"{emoji} −{amt} #{r['counterparty']} ({r['note']})")
        elif r["kind"] == "bet_win":
            lines.append(f"{emoji} +{amt} #{r['counterparty']}")
        elif r["kind"] == "bet_cancel":
            lines.append(f"{emoji} +{amt} #{r['counterparty']} (отмена)")
        elif r["kind"] == "fee":
            lines.append(f"{emoji} −{amt} (комиссия вывода)")
        elif r["kind"] == "x402":
            sender = r["counterparty"] or "?"
            short = f"{sender[:10]}…{sender[-4:]}" if sender.startswith("0x") else sender
            lines.append(f"{emoji} +{amt} от агента <code>{_esc(short)}</code>")
        elif r["kind"] == "paywall":
            lines.append(f"{emoji} −{amt} #{r['counterparty']} (платный контент)")
        elif r["kind"] == "paywall_earn":
            lines.append(f"{emoji} +{amt} #{r['counterparty']} (продажа)")
        elif r["kind"] == "channel_pay":
            lines.append(f"{emoji} −{amt} #{r['counterparty']} (подписка на канал)")
        elif r["kind"] == "channel_earn":
            lines.append(f"{emoji} +{amt} #{r['counterparty']} (продажа доступа)")
        else:
            lines.append(f"{emoji} {amt} ({r['kind']})")
    return "🧾 <b>Последние операции</b>\n\n" + "\n".join(lines)


@router.message(Command("top"))
async def cmd_top(message: types.Message) -> None:
    await message.answer(await _top_text())


@router.message(Command("history"))
async def cmd_history(message: types.Message) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.strip().split()
    limit = 15
    if len(parts) == 2 and parts[1].isdigit():
        limit = min(max(int(parts[1]), 1), 50)
    await message.answer(await _history_text(message.from_user.id, limit))


# ---------- paywall (paid content) ----------

PAYWALL_DRAFT_TTL = 300
_paywall_draft: dict[int, tuple[int, str, float]] = {}

PAYWALL_HELP = (
    "🔐 <b>Платный контент</b>\n"
    "• /paywall create 5 Мой отчёт — создать пост за 5 USDC\n"
    "  (после этого пришли контент одним сообщением)\n"
    "• /paywall list — все платные посты\n"
    "• /paywall buy &lt;id&gt; — купить и открыть контент\n"
    "• /paywall cancel — отменить создание\n\n"
    "📡 <b>Платные каналы</b>\n"
    "• /paywall channel 5 — в канале: доступ за 5 USDC / 30 дней\n"
    "• /paywall channel off — выключить продажу доступа\n"
    "• /paywall subscribe @канал — купить/продлить доступ\n"
    "• /paywall channels — платные каналы и мои подписки\n\n"
    "Продавец получает USDC на баланс сразу после покупки.\n"
    "Покупка идёт с баланса (/deposit). AI-агенты платят через API:\n"
    "POST /api/x402/paywall?item=&lt;id&gt;&amount=&lt;usdc&gt; (x402-протокол)."
)


@router.message(Command("paywall"))
async def cmd_paywall(message: types.Message, command: CommandObject) -> None:
    ledger.ensure_user(message.from_user.id, message.from_user.username)
    args = (command.args or "").strip()
    if not args:
        await message.answer(PAYWALL_HELP)
        return
    parts = args.split(maxsplit=1)
    sub = parts[0]
    uid = message.from_user.id
    if sub == "create" and len(parts) == 2:
        m = re.match(r"^(\d{1,9}(?:\.\d{1,6})?)\s+(.+)$", parts[1])
        if not m:
            await message.answer("Формат: /paywall create &lt;цена&gt; &lt;заголовок&gt;")
            return
        amount = Decimal(m.group(1))
        if amount <= 0 or amount > config.MAX_TIP_USDC:
            await message.answer(f"Цена должна быть 0 &lt; цена ≤ {config.MAX_TIP_USDC} USDC")
            return
        title = m.group(2).strip()
        if len(title) > config.PAYWALL_MAX_TITLE_LEN:
            await message.answer(
                f"⚠️ Заголовок слишком длинный: максимум {config.PAYWALL_MAX_TITLE_LEN} символов."
            )
            return
        _paywall_draft[uid] = (_to_micro(amount), title, time.time())
        await message.answer(
            f"💰 {_fmt(_to_micro(amount))} USDC · «{title}»\n\n"
            "Теперь пришли <b>контент</b> одним сообщением (текст).\n"
            "/paywall cancel — отмена."
        )
        return
    if sub == "cancel":
        if _paywall_draft.pop(uid, None):
            await message.answer("❌ Создание отменено.")
        else:
            await message.answer("Нет активного создания.")
        return
    if sub == "list":
        rows = ledger.paywall_items_list()
        if not rows:
            await message.answer("🔐 Платных постов пока нет. Создай первый: /paywall create 5 Заголовок")
            return
        lines = [
            f"#{r['id']} — {r['title']} — <b>{_fmt(int(r['price_micro']))} USDC</b>"
            f"{' ✅' if ledger.paywall_purchased(int(r['id']), uid) else ''}"
            for r in rows
        ]
        await message.answer("🔐 <b>Платные посты</b>\n\n" + "\n".join(lines) + "\n\nКупить: /paywall buy &lt;id&gt;")
        return
    if sub == "buy":
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Формат: /paywall buy &lt;id&gt;")
            return
        item = ledger.paywall_item(int(parts[1]))
        if item is None:
            await message.answer("❌ Пост не найден.")
            return
        res = ledger.buy_paywall(uid, int(parts[1]))
        if res == "ok":
            await message.answer(f"✅ Куплено за {_fmt(int(item['price_micro']))} USDC.\n\n{item['content']}")
        elif res == "dup":
            await message.answer(f"🔓 Уже куплено. Контент:\n\n{item['content']}")
        elif res == "self":
            await message.answer("❌ Это твой пост — покупать его не нужно.")
        elif res == "insufficient":
            await message.answer(
                f"❌ Недостаточно средств: нужно {_fmt(int(item['price_micro']))} USDC.\nПополни: /deposit"
            )
        return
    if sub == "channel":
        await _paywall_channel_cmd(message, parts[1:] if len(parts) == 2 else [])
        return
    if sub == "subscribe":
        await _paywall_subscribe_cmd(message, parts[1] if len(parts) == 2 else "")
        return
    if sub == "channels":
        await _paywall_channels_cmd(message)
        return
    await message.answer(PAYWALL_HELP)


async def _paywall_channel_cmd(message: types.Message, args: list[str]) -> None:
    """/paywall channel <price> | off — must run inside the channel (bot admin)."""
    uid = message.from_user.id
    chat = message.chat
    if chat.type != "channel":
        await message.answer("⚠️ Выполняй команду <b>в самом канале</b>, где бот — админ.")
        return
    try:
        bot_member = await message.bot.get_chat_member(chat.id, message.bot.id)
        if bot_member.status not in ("administrator", "creator"):
            await message.answer("⚠️ Сначала сделай бота <b>админом канала</b>.")
            return
        user_member = await message.bot.get_chat_member(chat.id, uid)
        if user_member.status not in ("administrator", "creator"):
            await message.answer("⚠️ Только админ канала может включить продажу доступа.")
            return
    except Exception:
        await message.answer("⚠️ Не удалось проверить права. Попробуй ещё раз.")
        return
    if not args or args[0] == "off":
        ledger.disable_paywall_channel(chat.id)
        await message.answer("📡 Продажа доступа к каналу <b>выключена</b>. Подписки продолжают действовать.")
        return
    m = re.match(r"^(\d{1,9}(?:\.\d{1,6})?)$", args[0])
    if not m:
        await message.answer("Формат: /paywall channel &lt;цена USDC за 30 дней&gt; или /paywall channel off")
        return
    amount = Decimal(m.group(1))
    if amount <= 0 or amount > config.MAX_TIP_USDC:
        await message.answer(f"Цена должна быть 0 &lt; цена ≤ {config.MAX_TIP_USDC} USDC")
        return
    if not ledger.set_paywall_channel(chat.id, uid, _to_micro(amount)):
        await message.answer(
            f"⚠️ Лимит: не больше {config.PAYWALL_MAX_CHANNELS_PER_USER} платных каналов на юзера."
        )
        return
    await message.answer(
        f"📡 Канал продаётся: <b>{_fmt(_to_micro(amount))} USDC / 30 дней</b>.\n"
        "Подписчики: /paywall subscribe @" + (getattr(chat, "username", None) or str(chat.id))
    )


async def _paywall_subscribe_cmd(message: types.Message, target: str) -> None:
    """/paywall subscribe <@channel|id> — buy or extend channel access."""
    uid = message.from_user.id
    target = target.strip().lstrip("@")
    if not target:
        await message.answer("Формат: /paywall subscribe @канал")
        return
    if target.lstrip("-").isdigit():
        chat_id = int(target)
    else:
        try:
            chat = await message.bot.get_chat(target)
            chat_id = chat.id
        except Exception:
            await message.answer("❌ Канал не найден.")
            return
    ch = ledger.paywall_channel(chat_id)
    if ch is None:
        await message.answer("❌ Этот канал не продаётся.")
        return
    try:
        member = await message.bot.get_chat_member(chat_id, uid)
        if member.status in ("administrator", "creator"):
            await message.answer("⚠️ Ты админ канала — подписка не нужна.")
            return
        inside = member.status in ("member", "restricted")
    except Exception:
        inside = False
    res = ledger.subscribe_channel(chat_id, uid)
    if res == "self":
        await message.answer("❌ Это твой канал — подписка не нужна.")
        return
    if res == "insufficient":
        await message.answer(
            f"❌ Недостаточно средств: нужно {_fmt(int(ch['price_micro']))} USDC.\nПополни: /deposit"
        )
        return
    if res != "ok":
        await message.answer("❌ Не получилось оформить подписку.")
        return
    expires = ledger.channel_subscription(chat_id, uid)["expires_at"]
    until = time.strftime("%d.%m.%Y %H:%M", time.localtime(int(expires)))
    if inside:
        await message.answer(f"🔑 Доступ продлён до <b>{until}</b>. Остаёшься в канале.")
    else:
        try:
            invite = await message.bot.create_chat_invite_link(
                chat_id, member_limit=1, expire_date=int(time.time()) + 3600
            )
            await message.answer(
                f"🔑 Доступ к каналу до <b>{until}</b>.\n"
                f"Жми ссылку (действует 1 час): {invite.invite_link}"
            )
        except Exception:
            await message.answer(
                f"🔑 Оплачено. Доступ до <b>{until}</b>. "
                "Бот сам откроет доступ (проверь, что бот — админ канала)."
            )
    try:
        title = (await message.bot.get_chat(chat_id)).title
    except Exception:
        title = str(chat_id)
    try:
        await message.bot.send_message(
            int(ch["owner_tg"]),
            f"💰 +{_fmt(int(ch['price_micro']))} USDC — подписка на канал «{title}».",
        )
    except Exception:
        pass


async def _paywall_channels_cmd(message: types.Message) -> None:
    """/paywall channels — paid channels and my subscriptions."""
    uid = message.from_user.id
    rows = ledger.paywall_channels_list()
    if not rows:
        await message.answer("📡 Платных каналов пока нет. Админ: /paywall channel 5 (в канале).")
        return
    lines = []
    for ch in rows:
        title = str(ch["chat_id"])
        try:
            title = (await message.bot.get_chat(int(ch["chat_id"]))).title
        except Exception:
            pass
        sub = ledger.channel_subscription(int(ch["chat_id"]), uid)
        state = f" — <b>{_fmt(int(ch['price_micro']))} USDC/30д</b>"
        if sub and int(sub["expires_at"]) > time.time():
            until = time.strftime("%d.%m", time.localtime(int(sub["expires_at"])))
            state += f" — 🔑 до {until}"
        lines.append(f"• {title}{state}")
    await message.answer(
        "📡 <b>Платные каналы</b>\n\n" + "\n".join(lines) + "\n\nКупить: /paywall subscribe @канал"
    )


# ---------- reaction tips ----------


@router.message()
async def _index_message(message: types.Message) -> None:
    """Index message -> author so reactions can tip. Privacy mode must be off."""
    user = message.from_user
    if not user or user.is_bot:
        return
    draft = _paywall_draft.pop(user.id, None)
    if draft:
        price_micro, title, ts = draft
        if time.time() - ts > PAYWALL_DRAFT_TTL:
            await message.answer("⏰ Время ожидания контента истекло — начни заново: /paywall create")
            return
        if (message.text or "").startswith("/"):
            await message.answer("❌ Отменено (пришла команда). Создание: /paywall create")
            return
        content = (message.text or message.caption or "").strip()
        if not content:
            _paywall_draft[user.id] = draft
            await message.answer("Пришли контент текстом или подписью к фото.")
            return
        if len(content) > config.PAYWALL_MAX_CONTENT_LEN:
            await message.answer(
                f"⚠️ Контент слишком длинный: максимум {config.PAYWALL_MAX_CONTENT_LEN} символов.\n"
                "Пришли заново (укороти или разбей на части)."
            )
            return
        item_id = ledger.create_paywall(user.id, title, price_micro, content)
        if item_id is None:
            await message.answer(
                f"⚠️ Лимит: не больше {config.PAYWALL_MAX_ITEMS_PER_USER} платных постов на юзера."
            )
            return
        await message.answer(
            f"✅ Пост #{item_id} «{title}» создан за {_fmt(price_micro)} USDC.\n"
            f"Посмотреть: /paywall list · купить: /paywall buy {item_id}"
        )
        return
    try:
        ledger.record_message(message.chat.id, message.message_id, user.id)
    except Exception:
        pass


@router.message_reaction()
async def on_reaction(update: types.MessageReactionUpdated) -> None:
    """Emoji reaction = instant USDC tip to the message author (once per user)."""
    reactor = update.user
    if not reactor or reactor.is_bot:
        return
    if not ledger.get_settings(reactor.id)["reaction_tips"]:
        return  # user disabled reaction tips in /settings
    amounts = [
        config.REACTION_TIPS[r.emoji]
        for r in update.new_reaction
        if isinstance(r, types.ReactionTypeEmoji) and r.emoji in config.REACTION_TIPS
    ]
    if not amounts:
        return
    amount_micro = _to_micro(max(amounts))
    if _throttle(reactor.id, "react"):
        return  # silent: reaction spam is throttled quietly
    ok, reason, author_id = ledger.tip_by_reaction(
        update.chat.id, update.message_id, reactor.id, amount_micro
    )
    if not ok:
        if reason == "balance":
            try:
                await update.bot.send_message(
                    reactor.id,
                    "❌ Реакция — это чаевые автору. Пополни баланс: /deposit",
                )
            except Exception:
                pass
        return
    try:
        await update.bot.send_message(
            author_id,
            f"⚡ +{_fmt(amount_micro)} USDC — реакция от "
            f"{'@' + reactor.username if reactor.username else reactor.id}!\n"
            f"Баланс: /balance",
        )
    except Exception:
        pass
    try:
        await update.bot.send_message(
            reactor.id,
            f"⚡ Отправлено {_fmt(amount_micro)} USDC автору сообщения.",
        )
    except Exception:
        pass
