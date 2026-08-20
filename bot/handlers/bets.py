"""Bets / prediction-market handlers."""

import json
import time
from datetime import datetime
from decimal import Decimal

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import _common as common

__all__ = [
    "_bet_card",
    "_bet_create",
    "_bet_place",
    "_bets_text",
    "_market_detail_text",
    "_notify_bet_cancelled",
    "_notify_bet_result",
    "_parse_deadline",
    "_rel_deadline",
    "cb_bet_amount",
    "cb_bet_place",
    "cb_market",
    "cb_res",
    "cmd_bet",
    "cmd_bets",
    "cmd_cancel",
    "cmd_mybets",
    "cmd_resolve",
]


@common.router.message(Command("bet"))
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
        if common.DEADLINE_RE.match(last.lower()):
            deadline = _parse_deadline(last)
            segs = segs[:-1]
        else:
            parts2 = last.rsplit(None, 1)
            if len(parts2) == 2 and common.DEADLINE_RE.match(parts2[1].lower()):
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
        if len(o) > common.config.MAX_OPTION_LEN:
            await message.answer(
                f"Вариант длиннее {common.config.MAX_OPTION_LEN} символов: <i>{o[:40]}…</i>"
            )
            return
    bet_id = common.ledger.create_bet(
        message.from_user.id, question, options, close_at=deadline
    )
    dl = f"\n⏰ Приём ставок до: {datetime.fromtimestamp(deadline).strftime('%d.%m %H:%M')}" if deadline else "\n⌛ Закрытие: /resolve (только ты)"
    await message.answer(
        f"🎲 Ставка #{bet_id} создана!\n\n"
        f"<b>{question}</b>\n"
        + "\n".join(f"{i + 1}) {o}" for i, o in enumerate(options))
        + dl
        + f"\n\nСтавят: /bet {bet_id} &lt;номер&gt; &lt;сумма&gt; или кнопки: /bets"
    )


def _parse_deadline(s: str) -> int | None:
    m = common.DEADLINE_RE.match(s.lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    secs = n * (3600 if unit == "h" else 86400)
    return int(time.time()) + secs


async def _bet_place(message: types.Message, parts: list[str]) -> None:
    if len(parts) != 4 or not common.BET_ID_RE.match(parts[1]) or not common.AMOUNT_RE.match(parts[3]):
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
    if amount > common.config.MAX_BET_USDC:
        await message.answer(f"Максимум за одну ставку: <b>{common.config.MAX_BET_USDC:.0f} USDC</b>.")
        return
    amount_micro = common._to_micro(amount)

    wait = common._throttle(message.from_user.id, "bet")
    if wait:
        await message.answer(wait)
        return

    bet = common.ledger.get_bet(bet_id)
    if not bet:
        await message.answer("Ставка не найдена.")
        return
    options = json.loads(bet["options"])
    if option_idx < 0 or option_idx >= len(options):
        await message.answer("Неверный номер варианта.")
        return

    result = common.ledger.place_bet(bet_id, message.from_user.id, option_idx, amount_micro)
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

    bal = common.ledger.balance(message.from_user.id)
    await message.answer(
        f"✅ Ставка принята!\n"
        f"🎲 #{bet_id} — <b>{options[option_idx]}</b> на <b>{common._fmt(amount_micro)} USDC</b>\n"
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
    view = common.ledger.market_view(bet["id"])
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
    my_stake = common.ledger.user_bet_stake(view["id"], tg_id) if tg_id else {}
    for o in view["options"]:
        mine = f" · <b>твоя ставка: {common._fmt(my_stake[o['index']])}</b>" if my_stake.get(o["index"]) else ""
        backers = f"{o['backers']}👤" if o["backers"] else ""
        lines.append(
            f"{o['index'] + 1}) {o['label']} — <b>{common._fmt(o['pool'])} USDC</b> "
            f"({o['probability']}%, {backers}){mine}"
        )
    lines.append(f"Пул итого: <b>{common._fmt(view['pot'])} USDC</b> · участников: {view['total_backers']}")
    lines.append("\nКомиссия на выигрыш: 2% (создателю рынка)")
    return "\n".join(lines)


async def _bets_text(tg_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Compact market listing: one line per market + a button per market.
    Full cards open by tapping a button (keeps the message short in groups)."""
    bets = common.ledger.open_bets(8)
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
        view = common.ledger.market_view(b["id"])
        if not view:
            continue
        if view["expired"]:
            meta = " 🕳️ истёк — вернуть: /cancel " + str(view["id"])
        elif view["close_at"]:
            meta = " ⏰ " + _rel_deadline(view["close_at"])
        else:
            meta = ""
        lines.append(
            f"#{view['id']} {view['question']} — {common._fmt(view['pot'])} USDC · {view['total_backers']}👤{meta}"
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


@common.router.message(Command("bets"))
async def cmd_bets(message: types.Message) -> None:
    text, kb = await _bets_text(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@common.router.callback_query(F.data.startswith("market:"))
async def cb_market(cb: types.CallbackQuery) -> None:
    bet_id = int(cb.data.split(":", 1)[1])
    view = common.ledger.market_view(bet_id)
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
    await common._edit_menu(cb, _market_detail_text(view, user.id if user else None), kb)
    await cb.answer()


@common.router.callback_query(F.data.startswith("res:"))
async def cb_res(cb: types.CallbackQuery) -> None:
    """Creator resolves a market inline: pick the winning option, done."""
    user = cb.from_user
    if not user:
        return
    parts = cb.data.split(":")
    bet_id = int(parts[1])
    view = common.ledger.market_view(bet_id)
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
        await common._edit_menu(
            cb,
            f"🏁 <b>Закрыть рынок #{bet_id}</b> — «{view['question']}»\n\n"
            f"Кто победил?\nПобедители делят пул ({common._fmt(view['pot'])} USDC), "
            f"создатель получает 2% комиссии.",
            kb,
        )
        await cb.answer()
        return
    idx = int(parts[2])
    if idx >= len(view["options"]):
        await cb.answer("Нет такого варианта", show_alert=True)
        return
    ok, msg = common.ledger.resolve_bet(bet_id, idx, user.id)
    if not ok:
        await cb.answer(msg, show_alert=True)
        return
    await _notify_bet_result(cb.message, bet_id)
    new_view = common.ledger.market_view(bet_id)
    text = (
        f"✅ <b>Ставка #{bet_id} закрыта!</b>\n{msg}\n\n"
        + _market_detail_text(new_view, user.id)
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Все рынки", callback_data="bets")]
        ]
    )
    await common._edit_menu(cb, text, kb)
    await cb.answer()


@common.router.callback_query(F.data.startswith("betq:"))
async def cb_bet_amount(cb: types.CallbackQuery) -> None:
    _, bet_id, opt = cb.data.split(":")
    view = common.ledger.market_view(int(bet_id))
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
                for a in common.QUICK_AMOUNTS
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"market:{bet_id}")],
        ]
    )
    await common._edit_menu(cb, f"🎯 #{bet_id}: {view['question']}\n\n<b>{label}</b> — сколько ставим?", kb)
    await cb.answer()


@common.router.callback_query(F.data.startswith("bets:"))
async def cb_bet_place(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    _, bet_id, opt, amt = cb.data.split(":")
    bet_id, opt = int(bet_id), int(opt)
    try:
        amount_micro = common._to_micro(Decimal(amt))
    except Exception:
        await cb.answer("Неверная сумма", show_alert=True)
        return

    wait = common._throttle(user.id, "bet")
    if wait:
        await cb.answer(wait, show_alert=True)
        return

    bet = common.ledger.get_bet(bet_id)
    if not bet:
        await cb.answer("Рынок не найден", show_alert=True)
        return
    result = common.ledger.place_bet(bet_id, user.id, opt, amount_micro)
    if result == "ok":
        options = json.loads(bet["options"])
        bal = common.ledger.balance(user.id)
        text = (
            f"✅ Ставка принята!\n"
            f"🎯 #{bet_id} — <b>{options[opt]}</b> на <b>{common._fmt(amount_micro)} USDC</b>\n"
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
        await common._edit_menu(cb, text, kb)
        await cb.answer()
    elif result == "deadline":
        await cb.answer("⏰ Приём ставок закрыт", show_alert=True)
    elif result == "closed":
        await cb.answer("Рынок уже закрыт", show_alert=True)
    elif result == "balance":
        await cb.answer("❌ Недостаточно баланса. /deposit", show_alert=True)
    else:
        await cb.answer("Что-то пошло не так", show_alert=True)


@common.router.message(Command("resolve"))
async def cmd_resolve(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 3 or not common.BET_ID_RE.match(parts[1]):
        await message.answer("Формат: /resolve &lt;id&gt; &lt;номер&gt;")
        return
    bet_id = int(parts[1])
    try:
        winning_idx = int(parts[2]) - 1
    except ValueError:
        await message.answer("Формат: /resolve &lt;id&gt; &lt;номер&gt;")
        return
    ok, msg = common.ledger.resolve_bet(bet_id, winning_idx, message.from_user.id)
    if not ok:
        await message.answer(f"❌ {msg}")
        return
    await message.answer(f"✅ <b>Ставка #{bet_id} закрыта!</b>\n{msg}\n\nВыплаты разосланы победителям.")
    await _notify_bet_result(message, bet_id)


async def _notify_bet_result(message: types.Message, bet_id: int) -> None:
    bet = common.ledger.get_bet(bet_id)
    if not bet:
        return
    payouts = common.ledger.payouts_for(bet_id)
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
                f"🏆 <b>Ты выиграл {common._fmt(total)} USDC!</b>\n"
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
    bet = common.ledger.get_bet(bet_id)
    if not bet:
        return
    positions = common.ledger._bet_positions(bet_id)
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


@common.router.message(Command("cancel"))
async def cmd_cancel(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.BET_ID_RE.match(parts[1]):
        await message.answer("Формат: /cancel &lt;id&gt;")
        return
    ok, msg = common.ledger.cancel_bet(int(parts[1]), message.from_user.id)
    if not ok:
        await message.answer(f"❌ {msg}")
        return
    await message.answer(f"✅ {msg}")
    await _notify_bet_cancelled(message, int(parts[1]))


@common.router.message(Command("mybets"))
async def cmd_mybets(message: types.Message) -> None:
    positions = common.ledger.user_positions(message.from_user.id)
    if not positions:
        await message.answer("🎲 У тебя нет открытых позиций. Ставят: /bets")
        return
    lines = ["📌 <b>Твои открытые позиции</b>\n"]
    for p in positions:
        lines.append(
            f"🎯 #{p['bet_id']} <b>{p['question']}</b>\n"
            f"   • {p['option']} — поставлено <b>{common._fmt(p['stake_micro'])} USDC</b>\n"
            f"   • потенциальный выигрыш: <b>{common._fmt(p['potential_micro'])} USDC</b>"
        )
    await message.answer("\n\n".join(lines))
