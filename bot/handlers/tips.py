"""/tip  handlers."""
from decimal import Decimal
from aiogram import types
from aiogram.filters import Command
from bot import i18n
from . import _common as common
__all__ = ['_notify_tip_received', '_resolve_in_chat', 'cmd_rain', 'cmd_tip']

@common.router.message(Command('rain'))
async def cmd_rain(message: types.Message) -> None:
    chat = message.chat
    lang = await common.user_lang(message.from_user.id)
    if chat.type not in ('group', 'supergroup'):
        await message.answer(i18n.t(lang, 'rain_only_groups'))
        return
    parts = message.text.strip().split()
    if len(parts) not in (2, 3) or not common.AMOUNT_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'rain_format'))
        return
    amount = Decimal(parts[1])
    if amount <= 0:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if amount > common.config.RAIN_MAX_USDC:
        await message.answer(i18n.t(lang, 'rain_max', n=f'{common.config.RAIN_MAX_USDC:.0f}'))
        return
    count = int(parts[2]) if len(parts) == 3 else 5
    if count < 1:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if count > common.config.RAIN_MAX_RECIPIENTS:
        await message.answer(i18n.t(lang, 'rain_max_participants', n=str(common.config.RAIN_MAX_RECIPIENTS)))
        return
    wait = await common._throttle(message.from_user.id, 'rain')
    if wait:
        await message.answer(wait)
        return
    amount_micro = common._to_micro(amount)
    ok, text, chosen = await common.ledger.rain(chat.id, message.from_user.id, amount_micro, count)
    if not ok:
        await message.answer(text)
        return
    names = []
    for tid in chosen[:8]:
        uname = await common.ledger.username_of(tid) or f'id{tid}'
        names.append(f'@{uname}')
    tail = i18n.t(lang, 'rain_and_more', n=len(chosen) - 8) if len(chosen) > 8 else ''
    names_str = ', '.join(names)
    await message.answer(i18n.t(lang, 'rain_recipients', names=names_str, tail=tail))

@common.router.message(Command('tip'))
async def cmd_tip(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) >= 2 and common.AMOUNT_RE.match(parts[1]):
        amount = Decimal(parts[1])
        rest = parts[2:]
    elif len(parts) == 1:
        amount = Decimal('1')
        rest = []
    else:
        await message.answer(i18n.t(lang, 'tip_need_amount'))
        return
    if amount <= 0:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if amount > common.config.MAX_TIP_USDC:
        await message.answer(i18n.t(lang, 'tip_max', n=f'{common.config.MAX_TIP_USDC:.0f}'))
        return
    amount_micro = common._to_micro(amount)
    if message.reply_to_message and message.reply_to_message.from_user:
        to_id = message.reply_to_message.from_user.id
        to_name = message.reply_to_message.from_user.username
    elif rest:
        target = rest[0]
        if not target.startswith('@'):
            await message.answer(i18n.t(lang, 'tip_need_recipient'))
            return
        username = target[1:]
        to_id = await common.ledger.find_by_username(username)
        if to_id is None:
            to_id = await _resolve_in_chat(message, username)
        if to_id is None:
            await message.answer(i18n.t(lang, 'tip_user_not_found', user=username))
            return
        to_name = username
    else:
        await message.answer(i18n.t(lang, 'tip_who'))
        return
    if to_id == message.from_user.id:
        await message.answer(i18n.t(lang, 'tip_self'))
        return
    wait = await common._throttle(message.from_user.id, 'tip')
    if wait:
        await message.answer(wait)
        return
    if not await common.ledger.transfer(message.from_user.id, to_id, amount_micro):
        await message.answer(i18n.t(lang, 'tip_no_balance'))
        return
    sender_name = message.from_user.username or f'id{message.from_user.id}'
    mention = f"<a href='tg://user?id={to_id}'>@{to_name or to_id}</a>"
    bal = await common.ledger.balance(message.from_user.id)
    bal_str = f'{bal:.4f}'.rstrip('0').rstrip('.')
    await message.answer(i18n.t(lang, 'tip_sent', sender=sender_name, mention=mention, amount=common._fmt(amount_micro), bal=bal_str))
    await _notify_tip_received(message, to_id, amount_micro, sender_name)

async def _notify_tip_received(message: types.Message, to_id: int, amount_micro: int, sender: str) -> None:
    if to_id == message.from_user.id:
        return
    try:
        lang = await common.user_lang(to_id)
        await message.bot.send_message(to_id, i18n.t(lang, 'tip_received', amount=common._fmt(amount_micro), sender=sender))
    except Exception:
        pass

async def _resolve_in_chat(message: types.Message, username: str) -> int | None:
    try:
        async for member in message.chat.get_members(limit=200):
            user = member.user
            if user.username and user.username.lower() == username.lower():
                await common.ledger.ensure_user(user.id, user.username)
                return user.id
    except Exception:
        return None
    return None