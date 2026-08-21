"""Prediction markets v2: Polymarket-style LMSR AMM handlers.

Live odds that move with demand, buy/sell any time before resolution,
creator-funded liquidity (subsidy -> b = S/ln(n)), guaranteed solvency by
the LMSR funding theorem. Money conservation is exact (see bot/ledger.py).
"""

import json
import time
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..ledger import lmsr_prices
from . import _common as common
from .bets import _parse_deadline, _rel_deadline

__all__ = [
    "_market_card",
    "_market_create",
    "_markets_text",
    "_notify_market_result",
    "cb_market_card",
    "cb_market_list",
    "cb_mk_buy",
    "cb_mk_cancel",
    "cb_mk_do",
    "cb_mk_resolve",
    "cb_mk_sell",
    "cb_mk_selldo",
    "cmd_market",
    "cmd_markets",
    "cmd_positions",
    "cmd_sell",
    "cmd_trade",
]

_SELL_PCTS = ("25", "50", "100")


def _pct(p: Decimal) -> str:
    return f"{int((p * 100).to_integral_value(rounding=ROUND_HALF_UP))}%"


def _bar(p: Decimal, width: int = 10) -> str:
    filled = int((p * width).to_integral_value(rounding="ROUND_HALF_UP"))
    return "▰" * filled + "▱" * (width - filled)


@common.router.message(Command("market"))
async def cmd_market(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer(
            "Формат:\n"
            "• /market create &lt;банк&gt; &lt;вопрос&gt; | &lt;вариант1&gt; | &lt;вариант2&gt; [24h|7d]\n"
            "• /trade &lt;id&gt; &lt;номер&gt; &lt;сумма&gt; — купить доли\n"
            "• /sell &lt;id&gt; &lt;номер&gt; — продать доли обратно\n"
            "• /markets — список рынков"
        )
        return
    if parts[1] == "create":
        await _market_create(message, parts)
        return
    await message.answer("Формат: /market create &lt;банк&gt; &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt; [24h]")


async def _market_create(message: types.Message, parts: list[str]) -> None:
    body = " ".join(parts[2:])
    segs = [s.strip() for s in body.split("|") if s.strip()]
    deadline = None
    if len(segs) >= 3:
        last = segs[-1]
        if common.DEADLINE_RE.match(last.lower()):
            deadline = _parse_deadline(last)
            segs = segs[:-1]
        else:
            parts2 = last.rsplit(None, 1)
            if len(parts2) == 2 and common.DEADLINE_RE.match(parts2[1].lower()):
                deadline = _parse_deadline(parts2[1])
                segs = [*segs[:-1], parts2[0].strip()]

    if not segs:
        await message.answer(
            "Формат: /market create &lt;банк&gt; &lt;вопрос&gt; | &lt;вариант1&gt; | &lt;вариант2&gt; [24h]\n"
            f"Банк — твоя ликвидность для AMM (мин {common.config.MARKET_MIN_SUBSIDY_USDC:.0f} USDC). Пример:\n"
            "/market create 50 Кто победит выборы? | Алиса | Боб 7d"
        )
        return
    # First segment starts with the subsidy: "50 Кто победит? | Алиса | Боб"
    head = segs[0].split(None, 1)
    if len(head) != 2 or not common.AMOUNT_RE.match(head[0]):
        await message.answer(
            "Формат: /market create &lt;банк&gt; &lt;вопрос&gt; | &lt;вариант1&gt; | &lt;вариант2&gt; [24h]\n"
            f"Банк — твоя ликвидность для AMM (мин {common.config.MARKET_MIN_SUBSIDY_USDC:.0f} USDC). Пример:\n"
            "/market create 50 Кто победит выборы? | Алиса | Боб 7d"
        )
        return
    try:
        subsidy = common._to_micro(Decimal(head[0]))
    except Exception:
        await message.answer("Неверная сумма банка.")
        return
    if subsidy < common._to_micro(common.config.MARKET_MIN_SUBSIDY_USDC):
        await message.answer(
            f"Минимальный банк: <b>{common.config.MARKET_MIN_SUBSIDY_USDC:.0f} USDC</b> "
            "(это ликвидность AMM — она вернётся тебе с прибылью, если трейдеры ошибутся)."
        )
        return
    if subsidy > common._to_micro(common.config.MARKET_MAX_SUBSIDY_USDC):
        await message.answer(
            f"Максимальный банк: <b>{common.config.MARKET_MAX_SUBSIDY_USDC:.0f} USDC</b>."
        )
        return
    segs = [head[1], *segs[1:]]
    if len(segs) < 3:
        await message.answer("Нужно минимум 2 варианта: | вариант1 | вариант2")
        return
    if len(segs) > 4:
        await message.answer("Максимум 4 варианта.")
        return
    question = segs[0]
    options = segs[1:]
    if len(question) > 200:
        await message.answer("Слишком длинный вопрос (макс 200 символов).")
        return
    for o in options:
        if len(o) > common.config.MAX_OPTION_LEN:
            await message.answer(
                f"Вариант длиннее {common.config.MAX_OPTION_LEN} символов: <i>{o[:40]}…</i>"
            )
            return

    wait = common._throttle(message.from_user.id, "market")
    if wait:
        await message.answer(wait)
        return

    result = common.ledger.create_market(
        message.from_user.id, question, options, subsidy, close_at=deadline
    )
    if result == "balance":
        await message.answer("❌ Недостаточно баланса на банк. Пополни: /deposit")
        return
    dl = (
        f"\n⏰ Дедлайн: {datetime.fromtimestamp(deadline).strftime('%d.%m %H:%M')}"
        if deadline
        else "\n⌛ Закрытие: /resolve-кнопка на карточке (только ты)"
    )
    await message.answer(
        f"📈 Рынок #{result} создан!\n\n"
        f"<b>{question}</b>\n"
        + "\n".join(f"{i + 1}) {o}" for i, o in enumerate(options))
        + f"\n🏦 Ликвидность: {common._fmt(subsidy)} USDC (твоё)"
        + dl
        + "\n\nТорговля: /trade "
        + str(result)
        + " &lt;номер&gt; &lt;сумма&gt; или кнопки: /markets\n"
        "Цены движутся с спросом — как на Polymarket."
    )


def _market_card(m: dict, tg_id: int | None = None) -> str:
    """Text card with live LMSR odds and the user's position."""
    mid = int(m["id"])
    options = json.loads(m["options"])
    quantities = common.ledger.market_quantities(mid)
    prices = lmsr_prices(quantities, int(m["b_micro"]))
    lines = [f"📈 #{mid} <b>{m['question']}</b>"]
    if m["status"] == "resolved":
        w = int(m["winner"]) if m["winner"] is not None else -1
        label = options[w] if 0 <= w < len(options) else "?"
        lines.append(f"✅ <b>Решён:</b> {label}")
    elif m["status"] == "cancelled":
        lines.append("❌ Отменён — деньги возвращены.")
    elif m["close_at"] and int(time.time()) > int(m["close_at"]):
        lines.append("⏰ Дедлайн прошёл — ждём решения создателя.")
    elif m["close_at"]:
        lines.append(f"⏰ {_rel_deadline(int(m['close_at']))}")
    pos = common.ledger.user_market_position(mid, tg_id) if tg_id else {}
    for i, o in enumerate(options):
        p = prices[i]
        mine = ""
        if i in pos and pos[i]["shares"] > 0:
            value_micro = int(pos[i]["shares"] * p)
            mine = (
                f"\n     └ твои доли: {common._fmt(pos[i]['shares'])} "
                f"(≈{common._fmt(value_micro)} USDC)"
            )
        lines.append(f"{i + 1}) {o} — <b>{_pct(p)}</b> {_bar(p)}{mine}")
    escrow = int(m["escrow_micro"])
    lines.append(f"🏦 Пул ликвидности: {common._fmt(escrow)} USDC")
    if m["status"] == "open":
        lines.append("\n💰 Победные доли платят 1 USDC за долю при резолюции. Продать можно в любой момент.")
    return "\n".join(lines)


async def _markets_text(tg_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    markets = common.ledger.open_markets(8)
    if not markets:
        return (
            "📈 Открытых рынков нет.\n"
            "Создай первый: /market create &lt;банк&gt; &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt;",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Создать рынок", callback_data="mkcreate")]
                ]
            ),
        )
    lines = ["📈 <b>Рынки предсказаний</b> — живые котировки", ""]
    kb_rows = []
    for m in markets:
        mid = int(m["id"])
        prices = common.ledger.market_prices(mid) or []
        top = max(range(len(prices)), key=lambda i: prices[i]) if prices else 0
        leader = (
            json.loads(m["options"])[top]
            if prices
            else "?"
        )
        lines.append(
            f"#{mid} {m['question']} — фаворит: <b>{leader[:30]} {_pct(prices[top])}</b>"
            if prices
            else f"#{mid} {m['question']}"
        )
        kb_rows.append(
            [InlineKeyboardButton(text=f"📈 #{mid}: {m['question'][:36]}", callback_data=f"mk:{mid}")]
        )
    lines.append("\nНажми на рынок — карточка с котировками и кнопками.")
    kb_rows.append([InlineKeyboardButton(text="➕ Создать рынок", callback_data="mkcreate")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows)


@common.router.message(Command("markets"))
async def cmd_markets(message: types.Message) -> None:
    text, kb = await _markets_text(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@common.router.callback_query(F.data == "markets_amm")
@common.router.callback_query(F.data == "mkcreate")
async def cb_market_list(cb: types.CallbackQuery) -> None:
    text, kb = await _markets_text(cb.from_user.id if cb.from_user else None)
    await common._edit_menu(cb, text, kb)
    await cb.answer()

@common.router.callback_query(F.data.startswith("mk:"))
async def cb_market_card(cb: types.CallbackQuery) -> None:
    mid = int(cb.data.split(":", 1)[1])
    m = common.ledger.get_market(mid)
    if not m:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    user = cb.from_user
    rows = []
    if m["status"] == "open":
        rows.append(
            [
                InlineKeyboardButton(text="🛒 Купить", callback_data=f"mkbuy:{mid}:0"),
                InlineKeyboardButton(text="📉 Продать", callback_data=f"mksell:{mid}:0"),
            ]
        )
        if user and int(m["creator"]) == user.id:
            rows.append(
                [
                    InlineKeyboardButton(text="🏁 Закрыть рынок", callback_data=f"mkres:{mid}"),
                    InlineKeyboardButton(text="✖️ Отменить", callback_data=f"mkcancel:{mid}"),
                ]
            )
    rows.append(
        [
            InlineKeyboardButton(text="📈 Все рынки", callback_data="markets_amm"),
            InlineKeyboardButton(text="🎲 Ставки", callback_data="bets"),
        ]
    )
    await common._edit_menu(cb, _market_card(m, user.id if user else None),
                            InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@common.router.callback_query(F.data.startswith("mkbuy:"))
async def cb_mk_buy(cb: types.CallbackQuery) -> None:
    _, mid, opt = cb.data.split(":")
    m = common.ledger.get_market(int(mid))
    if not m:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    options = json.loads(m["options"])
    idx = int(opt)
    label = options[idx] if idx < len(options) else "?"
    rows = [
        [
            InlineKeyboardButton(text=f"Купить {a} USDC", callback_data=f"mkdo:{mid}:{idx}:{a}")
            for a in common.QUICK_AMOUNTS
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"mk:{mid}")],
    ]
    await common._edit_menu(
        cb,
        f"🛒 #{mid}: <b>{label}</b>\n\nСколько вкладываем? Доли начисляются по живой цене.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@common.router.callback_query(F.data.startswith("mkdo:"))
async def cb_mk_do(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    _, mid, opt, amt = cb.data.split(":")
    try:
        spend = common._to_micro(Decimal(amt))
    except Exception:
        await cb.answer("Неверная сумма", show_alert=True)
        return
    wait = common._throttle(user.id, "market")
    if wait:
        await cb.answer(wait, show_alert=True)
        return
    status, info = common.ledger.buy_shares(int(mid), user.id, int(opt), spend)
    if status != "ok":
        msgs = {
            "closed": "Рынок уже закрыт",
            "deadline": "⏰ Дедлайн прошёл",
            "badopt": "Нет такого варианта",
            "balance": "❌ Недостаточно баланса. /deposit",
            "toosmall": "Слишком мало — увеличь сумму",
        }
        await cb.answer(msgs.get(status, "Не получилось"), show_alert=True)
        return
    bal = common.ledger.balance(user.id)
    text = (
        f"✅ Куплено!\n📈 #{mid} — <b>{info['label']}</b>\n"
        f"Доли: <b>{common._fmt(info['shares'])}</b> по цене {_pct(info['price'])}\n"
        f"Потрачено: <b>{common._fmt(info['cost'])} USDC</b>\n"
        f"Остаток: {format(bal, '.4f').rstrip('0').rstrip('.')} USDC"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📈 Рынок", callback_data=f"mk:{mid}"),
                InlineKeyboardButton(text="📉 Продать", callback_data=f"mksell:{mid}:{opt}"),
            ]
        ]
    )
    await common._edit_menu(cb, text, kb)
    await cb.answer()


@common.router.callback_query(F.data.startswith("mksell:"))
async def cb_mk_sell(cb: types.CallbackQuery) -> None:
    _, mid, opt = cb.data.split(":")
    user = cb.from_user
    if not user:
        return
    pos = common.ledger.user_market_position(int(mid), user.id)
    idx = int(opt)
    held = pos.get(idx, {}).get("shares", 0)
    if held <= 0:
        await cb.answer("У тебя нет долей этого исхода", show_alert=True)
        return
    rows = [
        [
            InlineKeyboardButton(text=f"Продать {p}%", callback_data=f"mkselldo:{mid}:{idx}:{p}")
            for p in _SELL_PCTS
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"mk:{mid}")],
    ]
    await common._edit_menu(
        cb,
        f"📉 #{mid}: продаём доли?\nУ тебя: <b>{common._fmt(held)}</b> долей по живой цене.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@common.router.callback_query(F.data.startswith("mkselldo:"))
async def cb_mk_selldo(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    _, mid, opt, pct = cb.data.split(":")
    wait = common._throttle(user.id, "market")
    if wait:
        await cb.answer(wait, show_alert=True)
        return
    pos = common.ledger.user_market_position(int(mid), user.id)
    idx = int(opt)
    held = pos.get(idx, {}).get("shares", 0)
    if held <= 0:
        await cb.answer("Нет долей", show_alert=True)
        return
    shares = held * int(pct) // 100
    if shares <= 0:
        await cb.answer("Слишком мало", show_alert=True)
        return
    status, info = common.ledger.sell_shares(int(mid), user.id, idx, shares)
    if status != "ok":
        await cb.answer("Не получилось продать", show_alert=True)
        return
    text = (
        f"✅ Продано!\n📉 #{mid} — <b>{info['label']}</b>\n"
        f"Доли: <b>{common._fmt(info['shares'])}</b> по цене {_pct(info['price'])}\n"
        f"Получено: <b>{common._fmt(info['value'])} USDC</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📈 Рынок", callback_data=f"mk:{mid}")]]
    )
    await common._edit_menu(cb, text, kb)
    await cb.answer()


@common.router.callback_query(F.data.startswith("mkres:"))
async def cb_mk_resolve(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    parts = cb.data.split(":")
    mid = int(parts[1])
    m = common.ledger.get_market(mid)
    if not m:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    if m["status"] != "open":
        await cb.answer("Рынок уже закрыт", show_alert=True)
        return
    if int(m["creator"]) != user.id:
        await cb.answer("Закрыть может только создатель рынка", show_alert=True)
        return
    if len(parts) == 2:
        options = json.loads(m["options"])
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"🏆 {o[:30]}", callback_data=f"mkres:{mid}:{i}")]
                for i, o in enumerate(options)
            ]
            + [[InlineKeyboardButton(text="◀️ Назад", callback_data=f"mk:{mid}")]]
        )
        await common._edit_menu(
            cb,
            f"🏁 <b>Закрыть рынок #{mid}</b> — «{m['question']}»\n\n"
            "Кто победил? Победные доли платят 1 USDC за долю, остаток пула — тебе.",
            kb,
        )
        await cb.answer()
        return
    idx = int(parts[2])
    ok, msg, payouts = common.ledger.resolve_market(mid, idx, user.id)
    if not ok:
        await cb.answer(msg, show_alert=True)
        return
    await _notify_market_result(cb.message, mid, payouts)
    new_m = common.ledger.get_market(mid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📈 Все рынки", callback_data="markets_amm")]]
    )
    await common._edit_menu(cb, f"✅ <b>Рынок #{mid} закрыт!</b>\n{msg}\n\n" + _market_card(new_m, user.id), kb)
    await cb.answer()


@common.router.callback_query(F.data.startswith("mkcancel:"))
async def cb_mk_cancel(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    mid = int(cb.data.split(":", 1)[1])
    m = common.ledger.get_market(mid)
    if not m:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    ok, msg = common.ledger.cancel_market(mid, user.id)
    if not ok:
        await cb.answer(msg, show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📈 Все рынки", callback_data="markets_amm")]]
    )
    await common._edit_menu(cb, f"✅ {msg}", kb)
    await cb.answer()


@common.router.message(Command("trade"))
async def cmd_trade(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 4 or not common.BET_ID_RE.match(parts[1]) or not common.AMOUNT_RE.match(parts[3]):
        await message.answer("Формат: /trade &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;")
        return
    try:
        mid, opt = int(parts[1]), int(parts[2]) - 1
        spend = common._to_micro(Decimal(parts[3]))
    except ValueError:
        await message.answer("Формат: /trade &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;")
        return
    if spend <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if spend > common._to_micro(common.config.MARKET_MAX_TRADE_USDC):
        await message.answer(
            f"Максимум за одну сделку: <b>{common.config.MARKET_MAX_TRADE_USDC:.0f} USDC</b>."
        )
        return
    wait = common._throttle(message.from_user.id, "market")
    if wait:
        await message.answer(wait)
        return
    status, info = common.ledger.buy_shares(mid, message.from_user.id, opt, spend)
    if status == "closed":
        await message.answer("Рынок не найден или уже закрыт.")
        return
    if status == "deadline":
        await message.answer("⏰ Дедлайн рынка прошёл.")
        return
    if status == "badopt":
        await message.answer("Неверный номер варианта.")
        return
    if status == "balance":
        await message.answer("❌ Недостаточно баланса. Пополни: /deposit")
        return
    if status == "toosmall":
        await message.answer("Слишком маленькая сумма — доли не начисляются. Увеличь.")
        return
    bal = common.ledger.balance(message.from_user.id)
    await message.answer(
        f"✅ Куплено!\n📈 #{mid} — <b>{info['label']}</b>\n"
        f"Доли: <b>{common._fmt(info['shares'])}</b> по цене {_pct(info['price'])}\n"
        f"Потрачено: <b>{common._fmt(info['cost'])} USDC</b>\n"
        f"Остаток: {format(bal, '.4f').rstrip('0').rstrip('.')} USDC\n\n"
        "Продать в любой момент: /sell "
        f"{mid} {opt + 1}"
    )


@common.router.message(Command("sell"))
async def cmd_sell(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) not in (3, 4) or not common.BET_ID_RE.match(parts[1]):
        await message.answer("Формат: /sell &lt;id&gt; &lt;номер&gt; [процент%]")
        return
    mid, opt = int(parts[1]), int(parts[2]) - 1
    pct = 100
    if len(parts) == 4:
        raw = parts[3].rstrip("%")
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            await message.answer("Процент: 1–100 (например /sell 3 1 50%)")
            return
        pct = int(raw)
    wait = common._throttle(message.from_user.id, "market")
    if wait:
        await message.answer(wait)
        return
    pos = common.ledger.user_market_position(mid, message.from_user.id)
    held = pos.get(opt, {}).get("shares", 0)
    if held <= 0:
        await message.answer("У тебя нет долей этого исхода. Позиции: /positions")
        return
    status, info = common.ledger.sell_shares(mid, message.from_user.id, opt, held * pct // 100)
    if status != "ok":
        await message.answer("Не получилось продать (рынок закрыт?).")
        return
    await message.answer(
        f"✅ Продано!\n📉 #{mid} — <b>{info['label']}</b>\n"
        f"Доли: <b>{common._fmt(info['shares'])}</b> по цене {_pct(info['price'])}\n"
        f"Получено: <b>{common._fmt(info['value'])} USDC</b>"
    )


@common.router.message(Command("positions"))
async def cmd_positions(message: types.Message) -> None:
    positions = common.ledger.user_market_positions(message.from_user.id)
    if not positions:
        await message.answer("📈 У тебя нет открытых позиций. Рынки: /markets")
        return
    lines = ["📌 <b>Твои позиции на рынках</b>\n"]
    total_value = 0
    total_cost = 0
    for p in positions:
        value_micro = int(p["value"])
        total_value += value_micro
        total_cost += max(p["cost"], 0)
        pnl = value_micro - p["cost"]
        sign = "+" if pnl >= 0 else "−"
        lines.append(
            f"📈 #{p['market_id']} <b>{p['question'][:60]}</b>\n"
            f"   • {p['option']} — {common._fmt(p['shares'])} долей @ {_pct(p['price'])}\n"
            f"   • стоимость ≈ <b>{common._fmt(value_micro)} USDC</b> "
            f"(PnL {sign}{common._fmt(abs(pnl))})"
        )
    lines.append(f"\nΣ стоимость: ≈<b>{common._fmt(total_value)} USDC</b>")
    await message.answer("\n\n".join(lines))


async def _notify_market_result(
    message: types.Message, mid: int, payouts: list[dict]
) -> None:
    m = common.ledger.get_market(mid)
    if not m:
        return
    options = json.loads(m["options"])
    winner_label = ""
    if m["winner"] is not None and int(m["winner"]) < len(options):
        winner_label = options[int(m["winner"])]
    for p in payouts:
        if p["win"]:
            line = (
                f"🏆 <b>Ты выиграл {common._fmt(p['net_micro'])} USDC!</b>\n"
                f"📈 #{mid} — «{m['question']}»\n"
                f"Победил: <b>{winner_label}</b>\nБаланс: /balance"
            )
        else:
            line = (
                f"📈 Рынок #{mid} — «{m['question']}» закрыт.\n"
                f"Победил: <b>{winner_label}</b>\n"
                f"Твои доли не сыграли. Новые рынки: /markets"
            )
        try:
            await message.bot.send_message(int(p["tg_id"]), line)
        except Exception:
            pass
