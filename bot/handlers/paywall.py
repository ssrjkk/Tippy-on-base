"""Paywall (paid content) handlers + reaction tips + message indexing."""

import re
import time
from decimal import Decimal

from aiogram import types
from aiogram.filters import Command, CommandObject

from . import _common as common

__all__ = [
    "PAYWALL_DRAFT_TTL",
    "PAYWALL_HELP",
    "_index_message",
    "_paywall_channel_cmd",
    "_paywall_channels_cmd",
    "_paywall_draft",
    "_paywall_subscribe_cmd",
    "cmd_paywall",
    "on_reaction",
]

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


@common.router.message(Command("paywall"))
async def cmd_paywall(message: types.Message, command: CommandObject) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
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
        if amount <= 0 or amount > common.config.MAX_TIP_USDC:
            await message.answer(f"Цена должна быть 0 &lt; цена ≤ {common.config.MAX_TIP_USDC} USDC")
            return
        title = m.group(2).strip()
        if len(title) > common.config.PAYWALL_MAX_TITLE_LEN:
            await message.answer(
                f"⚠️ Заголовок слишком длинный: максимум {common.config.PAYWALL_MAX_TITLE_LEN} символов."
            )
            return
        _paywall_draft[uid] = (common._to_micro(amount), title, time.time())
        await message.answer(
            f"💰 {common._fmt(common._to_micro(amount))} USDC · «{title}»\n\n"
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
        rows = common.ledger.paywall_items_list()
        if not rows:
            await message.answer("🔐 Платных постов пока нет. Создай первый: /paywall create 5 Заголовок")
            return
        lines = [
            f"#{r['id']} — {r['title']} — <b>{common._fmt(int(r['price_micro']))} USDC</b>"
            f"{' ✅' if common.ledger.paywall_purchased(int(r['id']), uid) else ''}"
            for r in rows
        ]
        await message.answer("🔐 <b>Платные посты</b>\n\n" + "\n".join(lines) + "\n\nКупить: /paywall buy &lt;id&gt;")
        return
    if sub == "buy":
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Формат: /paywall buy &lt;id&gt;")
            return
        item = common.ledger.paywall_item(int(parts[1]))
        if item is None:
            await message.answer("❌ Пост не найден.")
            return
        res = common.ledger.buy_paywall(uid, int(parts[1]))
        if res == "ok":
            await message.answer(f"✅ Куплено за {common._fmt(int(item['price_micro']))} USDC.\n\n{item['content']}")
        elif res == "dup":
            await message.answer(f"🔓 Уже куплено. Контент:\n\n{item['content']}")
        elif res == "self":
            await message.answer("❌ Это твой пост — покупать его не нужно.")
        elif res == "insufficient":
            await message.answer(
                f"❌ Недостаточно средств: нужно {common._fmt(int(item['price_micro']))} USDC.\nПополни: /deposit"
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
        common.ledger.disable_paywall_channel(chat.id)
        await message.answer("📡 Продажа доступа к каналу <b>выключена</b>. Подписки продолжают действовать.")
        return
    m = re.match(r"^(\d{1,9}(?:\.\d{1,6})?)$", args[0])
    if not m:
        await message.answer("Формат: /paywall channel &lt;цена USDC за 30 дней&gt; или /paywall channel off")
        return
    amount = Decimal(m.group(1))
    if amount <= 0 or amount > common.config.MAX_TIP_USDC:
        await message.answer(f"Цена должна быть 0 &lt; цена ≤ {common.config.MAX_TIP_USDC} USDC")
        return
    if not common.ledger.set_paywall_channel(chat.id, uid, common._to_micro(amount)):
        await message.answer(
            f"⚠️ Лимит: не больше {common.config.PAYWALL_MAX_CHANNELS_PER_USER} платных каналов на юзера."
        )
        return
    await message.answer(
        f"📡 Канал продаётся: <b>{common._fmt(common._to_micro(amount))} USDC / 30 дней</b>.\n"
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
    ch = common.ledger.paywall_channel(chat_id)
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
    res = common.ledger.subscribe_channel(chat_id, uid)
    if res == "self":
        await message.answer("❌ Это твой канал — подписка не нужна.")
        return
    if res == "insufficient":
        await message.answer(
            f"❌ Недостаточно средств: нужно {common._fmt(int(ch['price_micro']))} USDC.\nПополни: /deposit"
        )
        return
    if res != "ok":
        await message.answer("❌ Не получилось оформить подписку.")
        return
    expires = common.ledger.channel_subscription(chat_id, uid)["expires_at"]
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
            f"💰 +{common._fmt(int(ch['price_micro']))} USDC — подписка на канал «{title}».",
        )
    except Exception:
        pass


async def _paywall_channels_cmd(message: types.Message) -> None:
    """/paywall channels — paid channels and my subscriptions."""
    uid = message.from_user.id
    rows = common.ledger.paywall_channels_list()
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
        sub = common.ledger.channel_subscription(int(ch["chat_id"]), uid)
        state = f" — <b>{common._fmt(int(ch['price_micro']))} USDC/30д</b>"
        if sub and int(sub["expires_at"]) > time.time():
            until = time.strftime("%d.%m", time.localtime(int(sub["expires_at"])))
            state += f" — 🔑 до {until}"
        lines.append(f"• {title}{state}")
    await message.answer(
        "📡 <b>Платные каналы</b>\n\n" + "\n".join(lines) + "\n\nКупить: /paywall subscribe @канал"
    )


# ---------- reaction tips ----------


# NOTE: no @router.message() decorator here — this filter-less catch-all must be
# REGISTERED LAST (after every command handler), which __init__.py does
# explicitly; aiogram stops on the first matching handler.
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
        if len(content) > common.config.PAYWALL_MAX_CONTENT_LEN:
            await message.answer(
                f"⚠️ Контент слишком длинный: максимум {common.config.PAYWALL_MAX_CONTENT_LEN} символов.\n"
                "Пришли заново (укороти или разбей на части)."
            )
            return
        item_id = common.ledger.create_paywall(user.id, title, price_micro, content)
        if item_id is None:
            await message.answer(
                f"⚠️ Лимит: не больше {common.config.PAYWALL_MAX_ITEMS_PER_USER} платных постов на юзера."
            )
            return
        await message.answer(
            f"✅ Пост #{item_id} «{title}» создан за {common._fmt(price_micro)} USDC.\n"
            f"Посмотреть: /paywall list · купить: /paywall buy {item_id}"
        )
        return
    try:
        common.ledger.record_message(message.chat.id, message.message_id, user.id)
    except Exception:
        pass


@common.router.message_reaction()
async def on_reaction(update: types.MessageReactionUpdated) -> None:
    """Emoji reaction = instant USDC tip to the message author (once per user)."""
    reactor = update.user
    if not reactor or reactor.is_bot:
        return
    if not common.ledger.get_settings(reactor.id)["reaction_tips"]:
        return  # user disabled reaction tips in /settings
    amounts = [
        common.config.REACTION_TIPS[r.emoji]
        for r in update.new_reaction
        if isinstance(r, types.ReactionTypeEmoji) and r.emoji in common.config.REACTION_TIPS
    ]
    if not amounts:
        return
    amount_micro = common._to_micro(max(amounts))
    if common._throttle(reactor.id, "react"):
        return  # silent: reaction spam is throttled quietly
    ok, reason, author_id = common.ledger.tip_by_reaction(
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
            f"⚡ +{common._fmt(amount_micro)} USDC — реакция от "
            f"{'@' + reactor.username if reactor.username else reactor.id}!\n"
            f"Баланс: /balance",
        )
    except Exception:
        pass
    try:
        await update.bot.send_message(
            reactor.id,
            f"⚡ Отправлено {common._fmt(amount_micro)} USDC автору сообщения.",
        )
    except Exception:
        pass
