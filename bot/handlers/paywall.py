from bot import i18n

'Paywall (paid content) handlers + reaction tips + message indexing.'
import re
import time
from decimal import Decimal

from aiogram import types
from aiogram.filters import Command, CommandObject

from . import _common as common

__all__ = ['PAYWALL_DRAFT_TTL', 'PAYWALL_HELP', '_index_message', '_paywall_channel_cmd', '_paywall_channels_cmd', '_paywall_draft', '_paywall_subscribe_cmd', 'cmd_paywall', 'on_reaction']
PAYWALL_DRAFT_TTL = 300
_paywall_draft: dict[int, tuple[int, str, float]] = {}
PAYWALL_HELP = '🔐 <b>Платный контент</b>\n• /paywall create 5 Мой отчёт — создать пост за 5 USDC\n  (после этого пришли контент одним сообщением)\n• /paywall list — все платные посты\n• /paywall buy &lt;id&gt; — купить и открыть контент\n• /paywall cancel — отменить создание\n\n📡 <b>Платные каналы</b>\n• /paywall channel 5 — в канале: доступ за 5 USDC / 30 дней\n• /paywall channel off — выключить продажу доступа\n• /paywall subscribe @канал — купить/продлить доступ\n• /paywall channels — платные каналы и мои подписки\n\nПродавец получает USDC на баланс сразу после покупки.\nПокупка идёт с баланса (/deposit). AI-агенты платят через API:\nPOST /api/x402/paywall?item=&lt;id&gt;&amount=&lt;usdc&gt; (x402-протокол).'

@common.router.message(Command('paywall'))
async def cmd_paywall(message: types.Message, command: CommandObject) -> None:
    await common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    args = (command.args or '').strip()
    if not args:
        await message.answer(PAYWALL_HELP)
        return
    parts = args.split(maxsplit=1)
    sub = parts[0]
    uid = message.from_user.id
    lang = await common.user_lang(uid)
    if sub == 'create' and len(parts) == 2:
        m = re.match('^(\\d{1,9}(?:\\.\\d{1,6})?)\\s+(.+)$', parts[1])
        if not m:
            await message.answer(i18n.t(lang, 'paywall_format_create'))
            return
        amount = Decimal(m.group(1))
        if amount <= 0 or amount > common.config.MAX_TIP_USDC:
            await message.answer(i18n.t(lang, 'paywall_price_range', max=common.config.MAX_TIP_USDC))
            return
        title = m.group(2).strip()
        if len(title) > common.config.PAYWALL_MAX_TITLE_LEN:
            await message.answer(i18n.t(lang, 'paywall_title_too_long', n=common.config.PAYWALL_MAX_TITLE_LEN))
            return
        _paywall_draft[uid] = (common._to_micro(amount), title, time.time())
        await message.answer(i18n.t(lang, 'paywall_draft_ok', amount=common._fmt(common._to_micro(amount)), title=title))
        return
    if sub == 'cancel':
        if _paywall_draft.pop(uid, None):
            await message.answer(i18n.t(lang, 'paywall_cancel_created'))
        else:
            await message.answer(i18n.t(lang, 'paywall_no_active'))
        return
    if sub == 'list':
        rows = await common.ledger.paywall_items_list()
        if not rows:
            await message.answer(i18n.t(lang, 'paywall_empty'))
            return
        lines = [
            f"#{r['id']} — {r['title']} — <b>{common._fmt(int(r['price_micro']))} USDC</b>"
            f"{' ✅' if await common.ledger.paywall_purchased(int(r['id']), uid) else ''}"
            for r in rows
        ]
        text = (
            i18n.t(lang, 'paywall_list_header')
            + "\n\n"
            + "\n".join(lines)
            + "\n\n"
            + i18n.t(lang, 'paywall_list_buy_hint')
        )
        await message.answer(text)
        lines = [f"#{r['id']} — {r['title']} — <b>{common._fmt(int(r['price_micro']))} USDC</b>{(' ✅' if await common.ledger.paywall_purchased(int(r['id']), uid) else '')}" for r in rows]
        await message.answer(i18n.t(lang, 'paywall_list_header', lines='\n'.join(lines)) + '\n\n' + i18n.t(lang, 'paywall_list_buy_hint'))
        return
    if sub == 'buy':
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer(i18n.t(lang, 'paywall_format_buy'))
            return
        item = await common.ledger.paywall_item(int(parts[1]))
        if item is None:
            await message.answer(i18n.t(lang, 'paywall_post_not_found'))
            return
        res = await common.ledger.buy_paywall(uid, int(parts[1]))
        if res == 'ok':
            await message.answer(i18n.t(lang, 'paywall_bought_for', amount=common._fmt(int(item['price_micro'])), content=item['content']))
        elif res == 'dup':
            await message.answer(i18n.t(lang, 'paywall_already_bought', content=item['content']))
        elif res == 'self':
            await message.answer(i18n.t(lang, 'paywall_own_post'))
        elif res == 'insufficient':
            await message.answer(i18n.t(lang, 'paywall_insufficient', amount=common._fmt(int(item['price_micro']))))
        return
    if sub == 'channel':
        await _paywall_channel_cmd(message, parts[1:] if len(parts) == 2 else [])
        return
    if sub == 'subscribe':
        await _paywall_subscribe_cmd(message, parts[1] if len(parts) == 2 else '')
        return
    if sub == 'channels':
        await _paywall_channels_cmd(message)
        return
    await message.answer(PAYWALL_HELP)

async def _paywall_channel_cmd(message: types.Message, args: list[str]) -> None:
    """/paywall channel <price> | off — must run inside the channel (bot admin)."""
    uid = message.from_user.id
    chat = message.chat
    if chat.type != 'channel':
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_need_admin_channel'))
        return
    try:
        bot_member = await message.bot.get_chat_member(chat.id, message.bot.id)
        if bot_member.status not in ('administrator', 'creator'):
            await message.answer(i18n.t(await common.user_lang(uid), 'paywall_bot_admin'))
            return
        user_member = await message.bot.get_chat_member(chat.id, uid)
        if user_member.status not in ('administrator', 'creator'):
            await message.answer(i18n.t(await common.user_lang(uid), 'paywall_user_admin'))
            return
    except Exception:
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_check_error'))
        return
    if not args or args[0] == 'off':
        await common.ledger.disable_paywall_channel(chat.id)
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_channel_disabled'))
        return
    m = re.match('^(\\d{1,9}(?:\\.\\d{1,6})?)$', args[0])
    if not m:
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_channel_format'))
        return
    amount = Decimal(m.group(1))
    if amount <= 0 or amount > common.config.MAX_TIP_USDC:
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_price_range', max=common.config.MAX_TIP_USDC))
        return
    if not await common.ledger.set_paywall_channel(chat.id, uid, common._to_micro(amount)):
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_channel_limit', n=common.config.PAYWALL_MAX_CHANNELS_PER_USER))
        return
    channel_name = getattr(chat, 'username', None) or str(chat.id)
    await message.answer(i18n.t(await common.user_lang(uid), 'paywall_channel_active', amount=common._fmt(common._to_micro(amount)), channel=channel_name))

async def _paywall_subscribe_cmd(message: types.Message, target: str) -> None:
    """/paywall subscribe <@channel|id> — buy or extend channel access."""
    uid = message.from_user.id
    target = target.strip().lstrip('@')
    if not target:
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_subscribe_format'))
        return
    if target.lstrip('-').isdigit():
        chat_id = int(target)
    else:
        try:
            chat = await message.bot.get_chat(target)
            chat_id = chat.id
        except Exception:
            await message.answer(i18n.t(await common.user_lang(uid), 'paywall_channel_not_found_msg'))
            return
    ch = await common.ledger.paywall_channel(chat_id)
    if ch is None:
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_not_selling'))
        return
    try:
        member = await message.bot.get_chat_member(chat_id, uid)
        if member.status in ('administrator', 'creator'):
            await message.answer(i18n.t(await common.user_lang(uid), 'paywall_you_are_admin'))
            return
        inside = member.status in ('member', 'restricted')
    except Exception:
        inside = False
    res = await common.ledger.subscribe_channel(chat_id, uid)
    if res == 'self':
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_self_channel'))
        return
    if res == 'insufficient':
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_subscribe_insufficient', amount=common._fmt(int(ch['price_micro']))))
        return
    if res != 'ok':
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_subscribe_fail'))
        return
    expires = (await common.ledger.channel_subscription(chat_id, uid))['expires_at']
    until = time.strftime('%d.%m.%Y %H:%M', time.localtime(int(expires)))
    if inside:
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_access_renewed', until=until))
    else:
        try:
            invite = await message.bot.create_chat_invite_link(chat_id, member_limit=1, expire_date=int(time.time()) + 3600)
            await message.answer(i18n.t(await common.user_lang(uid), 'paywall_access_invite', until=until, link=invite.invite_link))
        except Exception:
            await message.answer(i18n.t(await common.user_lang(uid), 'paywall_access_manual', until=until))
    try:
        title = (await message.bot.get_chat(chat_id)).title
    except Exception:
        title = str(chat_id)
    try:
        await message.bot.send_message(int(ch['owner_tg']), i18n.t('ru', 'paywall_owner_notified', amount=common._fmt(int(ch['price_micro'])), title=title))
    except Exception:
        pass

async def _paywall_channels_cmd(message: types.Message) -> None:
    """/paywall channels — paid channels and my subscriptions."""
    uid = message.from_user.id
    rows = await common.ledger.paywall_channels_list()
    if not rows:
        await message.answer(i18n.t(await common.user_lang(uid), 'paywall_channels_empty'))
        return
    lines = []
    for ch in rows:
        title = str(ch['chat_id'])
        try:
            title = (await message.bot.get_chat(int(ch['chat_id']))).title
        except Exception:
            pass
        sub = await common.ledger.channel_subscription(int(ch['chat_id']), uid)
        state = i18n.t(await common.user_lang(uid), 'paywall_channel_state', amount=common._fmt(int(ch['price_micro'])))
        if sub and int(sub['expires_at']) > time.time():
            until = time.strftime('%d.%m', time.localtime(int(sub['expires_at'])))
            state += i18n.t(await common.user_lang(uid), 'paywall_channel_until', until=until)
        lines.append(f'• {title}{state}')
    await message.answer(i18n.t(await common.user_lang(uid), 'paywall_channels_header', lines='\n'.join(lines)))

async def _index_message(message: types.Message) -> None:
    """Index message -> author so reactions can tip. Privacy mode must be off."""
    user = message.from_user
    if not user or user.is_bot:
        return
    draft = _paywall_draft.pop(user.id, None)
    if draft:
        price_micro, title, ts = draft
        if time.time() - ts > PAYWALL_DRAFT_TTL:
            await message.answer(i18n.t(await common.user_lang(user.id), 'paywall_draft_timeout'))
            return
        if (message.text or '').startswith('/'):
            await message.answer(i18n.t(await common.user_lang(user.id), 'paywall_draft_cancelled_cmd'))
            return
        content = (message.text or message.caption or '').strip()
        if not content:
            _paywall_draft[user.id] = draft
            await message.answer(i18n.t(await common.user_lang(user.id), 'paywall_need_content'))
            return
        if len(content) > common.config.PAYWALL_MAX_CONTENT_LEN:
            await message.answer(i18n.t(await common.user_lang(user.id), 'paywall_content_too_long', n=common.config.PAYWALL_MAX_CONTENT_LEN))
            return
        item_id = await common.ledger.create_paywall(user.id, title, price_micro, content)
        if item_id is None:
            await message.answer(i18n.t(await common.user_lang(user.id), 'paywall_post_limit', n=common.config.PAYWALL_MAX_ITEMS_PER_USER))
            return
        await message.answer(i18n.t(await common.user_lang(user.id), 'paywall_post_created', id=item_id, title=title, amount=common._fmt(price_micro)))
        return
    try:
        await common.ledger.record_message(message.chat.id, message.message_id, user.id)
    except Exception:
        pass

@common.router.message_reaction()
async def on_reaction(update: types.MessageReactionUpdated) -> None:
    """Emoji reaction = instant USDC tip to the message author (once per user)."""
    reactor = update.user
    if not reactor or reactor.is_bot:
        return
    if not (await common.ledger.get_settings(reactor.id))['reaction_tips']:
        return
    amounts = [common.config.REACTION_TIPS[r.emoji] for r in update.new_reaction if isinstance(r, types.ReactionTypeEmoji) and r.emoji in common.config.REACTION_TIPS]
    if not amounts:
        return
    amount_micro = common._to_micro(max(amounts))
    if await common._throttle(reactor.id, 'react'):
        return
    ok, reason, author_id = await common.ledger.tip_by_reaction(update.chat.id, update.message_id, reactor.id, amount_micro)
    if not ok:
        if reason == 'balance':
            try:
                await update.bot.send_message(reactor.id, i18n.t('ru', 'paywall_reaction_balance'))
            except Exception:
                pass
        return
    try:
        await update.bot.send_message(author_id, i18n.t('ru', 'paywall_reaction_received', amount=common._fmt(amount_micro), reactor=reactor.username or str(reactor.id)))
    except Exception:
        pass
    try:
        await update.bot.send_message(reactor.id, i18n.t('ru', 'paywall_reaction_sent', amount=common._fmt(amount_micro)))
    except Exception:
        pass
