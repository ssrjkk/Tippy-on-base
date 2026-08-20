"""Tip / rain handlers."""

from decimal import Decimal

from aiogram import types
from aiogram.filters import Command

from . import _common as common

__all__ = [
    "_notify_tip_received",
    "_resolve_in_chat",
    "cmd_rain",
    "cmd_tip",
]


@common.router.message(Command("rain"))
async def cmd_rain(message: types.Message) -> None:
    chat = message.chat
    if chat.type not in ("group", "supergroup"):
        await message.answer("🌧️ /rain работает только в группах: разбросай USDC активным участникам!")
        return
    parts = message.text.strip().split()
    if len(parts) not in (2, 3) or not common.AMOUNT_RE.match(parts[1]):
        await message.answer("Формат: /rain 10  (или /rain 10 15 — на 15 человек)")
        return
    amount = Decimal(parts[1])
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if amount > common.config.RAIN_MAX_USDC:
        await message.answer(f"Максимум за один дождь: <b>{common.config.RAIN_MAX_USDC:.0f} USDC</b>.")
        return
    count = int(parts[2]) if len(parts) == 3 else 5
    if count < 1:
        await message.answer("Количество участников должно быть больше нуля.")
        return
    if count > common.config.RAIN_MAX_RECIPIENTS:
        await message.answer(f"Максимум участников: <b>{common.config.RAIN_MAX_RECIPIENTS}</b>.")
        return
    wait = common._throttle(message.from_user.id, "rain")
    if wait:
        await message.answer(wait)
        return
    amount_micro = common._to_micro(amount)
    ok, text, chosen = common.ledger.rain(chat.id, message.from_user.id, amount_micro, count)
    if not ok:
        await message.answer(text)
        return
    names = []
    for tid in chosen[:8]:
        uname = common.ledger.username_of(tid) or f"id{tid}"
        names.append(f"@{uname}")
    tail = f" и ещё {len(chosen) - 8}" if len(chosen) > 8 else ""
    await message.answer(f"{text}\n🎁 Получили: {', '.join(names)}{tail}\n\n🌧️ Дождь закончился!")


@common.router.message(Command("tip"))
async def cmd_tip(message: types.Message) -> None:
    parts = message.text.strip().split()

    if len(parts) >= 2 and common.AMOUNT_RE.match(parts[1]):
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
    if amount > common.config.MAX_TIP_USDC:
        await message.answer(f"Максимум чаевых за раз: <b>{common.config.MAX_TIP_USDC:.0f} USDC</b>.")
        return
    amount_micro = common._to_micro(amount)

    if message.reply_to_message and message.reply_to_message.from_user:
        to_id = message.reply_to_message.from_user.id
        to_name = message.reply_to_message.from_user.username
    elif rest:
        target = rest[0]
        if not target.startswith("@"):
            await message.answer("Укажи получателя: /tip 5 @username")
            return
        username = target[1:]
        to_id = common.ledger.find_by_username(username)
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

    wait = common._throttle(message.from_user.id, "tip")
    if wait:
        await message.answer(wait)
        return

    if not common.ledger.transfer(message.from_user.id, to_id, amount_micro):
        await message.answer("❌ Недостаточно баланса. Пополни: /deposit")
        return

    sender_name = message.from_user.username or f"id{message.from_user.id}"
    mention = f"<a href='tg://user?id={to_id}'>@{to_name or to_id}</a>"
    bal = common.ledger.balance(message.from_user.id)
    await message.answer(
        f"💸 <b>{sender_name}</b> → {mention}\n"
        f"<b>{common._fmt(amount_micro)} USDC</b>\n"
        f"Остаток: {bal:.4f}".rstrip("0").rstrip(".") + " USDC"
    )
    await _notify_tip_received(message, to_id, amount_micro, sender_name)


async def _notify_tip_received(message: types.Message, to_id: int, amount_micro: int, sender: str) -> None:
    if to_id == message.from_user.id:
        return
    try:
        await message.bot.send_message(
            to_id,
            f"💸 <b>Тебе кинули {common._fmt(amount_micro)} USDC</b>\n"
            f"От: @{sender}\n\nБаланс: /balance",
        )
    except Exception:
        pass


async def _resolve_in_chat(message: types.Message, username: str) -> int | None:
    try:
        async for member in message.chat.get_members(limit=200):
            user = member.user
            if user.username and user.username.lower() == username.lower():
                common.ledger.ensure_user(user.id, user.username)
                return user.id
    except Exception:
        return None
    return None
