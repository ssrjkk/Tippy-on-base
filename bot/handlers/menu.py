"""Menu / onboarding / settings handlers."""

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from bot import i18n

from . import _common as common
from .bets import _bets_text, _market_detail_text
from .stats import _history_text, _stats_text, _top_text
from .wallet import _balance_text, _deposit_text, _donate_text


def _lang(tg_id: int) -> str:
    return i18n.norm(common.ledger.get_settings(tg_id).get("lang"))


def _fmt_balance(tg_id: int) -> str:
    bal = common.ledger.balance(tg_id)
    return f"{bal:.6f}".rstrip("0").rstrip(".")


@common.router.message(Command("about"))
async def cmd_about(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    lang = _lang(message.from_user.id)
    await message.answer(
        f"{i18n.t(lang, 'about_title')}\n\n{i18n.t(lang, 'about_body')}",
        reply_markup=common._menu_kb(lang),
    )


@common.router.callback_query(F.data == "about")
async def cb_about(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = _lang(user.id)
    await common._edit_menu(
        cb,
        f"{i18n.t(lang, 'about_title')}\n\n{i18n.t(lang, 'about_body')}",
        common._menu_kb(lang),
    )
    await cb.answer()


@common.router.message(Command("language"))
async def cmd_language(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    lang = _lang(message.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=i18n.LANG_NAME[code], callback_data=f"setlang:{code}"
                )
            ]
            for code in i18n.LANGS
        ]
    )
    await message.answer(i18n.t(lang, "lang_title"), reply_markup=kb)


@common.router.callback_query(F.data.startswith("setlang:"))
async def cb_setlang(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    code = cb.data.split(":", 1)[1]
    if code not in i18n.LANGS:
        await cb.answer()
        return
    common.ledger.set_setting(user.id, "lang", code)
    await common._edit_menu(
        cb,
        f"{i18n.t(code, 'lang_set', name=i18n.LANG_NAME[code])}\n\n"
        f"{i18n.t(code, 'menu_balance', bal=_fmt_balance(user.id))}",
        common._menu_kb(code),
    )
    await cb.answer()


@common.router.message(Command("start", "help"))
async def cmd_start(message: types.Message, command: CommandObject) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    args = command.args
    if command.command == "start" and args:
        m = common.DONATE_LINK_RE.match(args)
        if m:
            await _donate_landing(message, int(m.group(1)))
            return
        m = common.BET_LINK_RE.match(args)
        if m:
            await _send_market_deep_link(message, int(m.group(1)))
            return
        m = common.PAYWALL_LINK_RE.match(args)
        if m:
            await _send_paywall_deep_link(message, int(m.group(1)))
            return
    if command.command == "start":
        name = message.from_user.username or f"id{message.from_user.id}"
        lang = _lang(message.from_user.id)
        bal = common.ledger.balance(message.from_user.id)
        bal_s = f"{bal:.6f}".rstrip("0").rstrip(".")
        welcome = (
            f"{i18n.t(lang, 'start_hi', name=name)}\n\n"
            f"{i18n.t(lang, 'start_intro')}\n\n"
            f"💰 {i18n.t(lang, 'menu_balance', bal=bal_s)}\n\n"
            f"{i18n.t(lang, 'start_try')}\n\n"
            f"{i18n.t(lang, 'start_footer')}"
        )
        await message.answer(welcome, reply_markup=common._menu_kb(lang))
        return
    lang = _lang(message.from_user.id)
    await message.answer(common.help_text(lang), reply_markup=common._menu_kb(lang))


@common.router.message(Command("menu"))
async def cmd_menu(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    lang = _lang(message.from_user.id)
    await message.answer(
        i18n.t(lang, "menu_balance", bal=_fmt_balance(message.from_user.id)),
        reply_markup=common._menu_kb(lang),
    )


async def _donate_landing(message: types.Message, target_id: int) -> None:
    creator = common.ledger.username_of(target_id) or f"id{target_id}"
    addr = common.base.hot_wallet()
    caption = (
        f"💛 Поддержать <b>@{creator}</b>\n\n"
        f"Отправь USDC (<b>сеть Base</b>) на адрес:\n"
        f"<code>{addr}</code>\n\n"
        f"Зачислится на баланс отправителя автоматически после привязки "
        f"кошелька /link. Передать чаевые @{creator}: /tip 5 @{creator}. Спасибо! 🫡"
    )
    qr = common._qr_bytes(addr)
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
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)


async def _send_market_deep_link(message: types.Message, bet_id: int) -> None:
    """?start=bet_<id> from a shared market page: show the market + bet buttons.

    Turns the web dashboard into an onboarding funnel — anyone who opens a
    shared market link lands here and can place a bet in two taps.
    """
    view = common.ledger.market_view(bet_id)
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
    item = common.ledger.paywall_item(item_id)
    if item is None:
        await message.answer("🔐 Пост не найден. Все посты: /paywall list")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔓 Купить за {common._fmt(int(item['price_micro']))} USDC",
                    callback_data=f"paywall_buy:{item_id}",
                )
            ],
            [InlineKeyboardButton(text="🔐 Все посты", callback_data="paywall_list")],
        ]
    )
    await message.answer(
        f"🔐 Ты пришёл по ссылке на платный пост!\n\n"
        f"<b>{item['title']}</b>\n"
        f"Цена: <b>{common._fmt(int(item['price_micro']))} USDC</b>",
        reply_markup=kb,
    )


@common.router.callback_query(F.data.startswith("paywall_buy:"))
async def cb_paywall_buy(cb: types.CallbackQuery) -> None:
    """One-tap purchase from a shared deep link (Farcaster Frame funnel)."""
    item_id = int(cb.data.split(":", 1)[1])
    item = common.ledger.paywall_item(item_id)
    if item is None:
        await common._edit_menu(cb, "❌ Пост не найден.")
        return
    res = common.ledger.buy_paywall(cb.from_user.id, item_id)
    if res == "ok":
        await common._edit_menu(
            cb, f"✅ Куплено за {common._fmt(int(item['price_micro']))} USDC.\n\n{item['content']}"
        )
    elif res == "dup":
        await common._edit_menu(cb, f"🔓 Уже куплено. Контент:\n\n{item['content']}")
    elif res == "self":
        await common._edit_menu(cb, "❌ Это твой пост — покупать его не нужно.")
    else:
        await common._edit_menu(
            cb,
            f"❌ Недостаточно средств: нужно {common._fmt(int(item['price_micro']))} USDC.\nПополни: /deposit",
        )


@common.router.callback_query(
    F.data.in_({"bal", "dep", "top", "hist", "bets", "donate", "stats", "settings", "betcreate", "paywall_list", "tip", "ask", "wallet"})
)
async def on_menu(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = _lang(user.id)
    if cb.data == "bal":
        await common._edit_menu(cb, await _balance_text(user.id))
    elif cb.data == "dep":
        await common._edit_menu(cb, await _deposit_text(user.id))
    elif cb.data == "top":
        await common._edit_menu(cb, await _top_text())
    elif cb.data == "hist":
        await common._edit_menu(cb, await _history_text(user.id))
    elif cb.data == "bets":
        text, kb = await _bets_text(user.id)
        await common._edit_menu(cb, text, kb)
    elif cb.data == "donate":
        await common._edit_menu(cb, await _donate_text(cb.message.bot, user.id))
    elif cb.data == "stats":
        await common._edit_menu(cb, await _stats_text(user.id))
    elif cb.data == "settings":
        text, kb = await _settings_kb_text(user.id)
        await common._edit_menu(cb, text, kb)
    elif cb.data == "tip":
        await common._edit_menu(cb, i18n.t(lang, "hint_tip"))
    elif cb.data == "ask":
        await common._edit_menu(cb, i18n.t(lang, "hint_ask"))
    elif cb.data == "wallet":
        await common._edit_menu(cb, i18n.t(lang, "hint_wallet"))
    elif cb.data == "betcreate":
        await common._edit_menu(
            cb,
            "🎲 <b>Создание рынка</b>\n\n"
            "/bet create &lt;вопрос&gt; | &lt;вариант1&gt; | &lt;вариант2&gt; [24h]\n\n"
            "До 4 вариантов, опционально дедлайн (например <i>24h</i> или <i>7d</i>). "
            "Создатель получает 2% от выигрыша победителя.",
        )
    elif cb.data == "paywall_list":
        rows = common.ledger.paywall_items_list()
        if not rows:
            text = "🔐 Платных постов пока нет. Создай первый: /paywall create 5 Заголовок"
            await common._edit_menu(cb, text)
        else:
            lines = [
                f"#{r['id']} — {r['title']} — <b>{common._fmt(int(r['price_micro']))} USDC</b>"
                f"{' ✅' if common.ledger.paywall_purchased(int(r['id']), cb.from_user.id) else ''}"
                for r in rows
            ]
            await common._edit_menu(cb, "🔐 <b>Платные посты</b>\n\n" + "\n".join(lines))
    await cb.answer()


async def _settings_kb_text(tg_id: int) -> tuple[str, InlineKeyboardMarkup]:
    lang = _lang(tg_id)
    s = common.ledger.get_settings(tg_id)
    react = i18n.t(lang, "on") if s["reaction_tips"] else i18n.t(lang, "off")
    notif = i18n.t(lang, "on") if s["notify_deposits"] else i18n.t(lang, "off")
    text = (
        f"{i18n.t(lang, 'set_title')}\n\n"
        f"{i18n.t(lang, 'set_react', state=react)}\n\n"
        f"{i18n.t(lang, 'set_notif', state=notif)}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⚡ {i18n.t(lang, 'on') if s['reaction_tips'] else i18n.t(lang, 'off')}",
                    callback_data="set:react",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔔 {i18n.t(lang, 'on') if s['notify_deposits'] else i18n.t(lang, 'off')}",
                    callback_data="set:notif",
                )
            ],
            [
                InlineKeyboardButton(
                    text=i18n.t(lang, "btn_lang", name=i18n.LANG_NAME[lang]),
                    callback_data="set:lang",
                )
            ],
            [InlineKeyboardButton(text=i18n.t(lang, "btn_back"), callback_data="menu")],
        ]
    )
    return text, kb


@common.router.message(Command("settings"))
async def cmd_settings(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    text, kb = await _settings_kb_text(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@common.router.callback_query(F.data.startswith("set:"))
async def cb_settings(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    _, key = cb.data.split(":")
    if key == "lang":
        lang = _lang(user.id)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=i18n.LANG_NAME[code], callback_data=f"setlang:{code}"
                    )
                ]
                for code in i18n.LANGS
            ]
            + [[InlineKeyboardButton(text=i18n.t(lang, "btn_back"), callback_data="settings")]]
        )
        await common._edit_menu(cb, i18n.t(lang, "lang_title"), kb)
        await cb.answer()
        return
    if key == "react":
        cur = common.ledger.get_settings(user.id)["reaction_tips"]
        common.ledger.set_setting(user.id, "reaction_tips", not cur)
    elif key == "notif":
        cur = common.ledger.get_settings(user.id)["notify_deposits"]
        common.ledger.set_setting(user.id, "notify_deposits", not cur)
    else:
        await cb.answer()
        return
    text, kb = await _settings_kb_text(user.id)
    await common._edit_menu(cb, text, kb)
    await cb.answer()


@common.router.callback_query(F.data == "menu")
async def cb_menu(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = _lang(user.id)
    await common._edit_menu(
        cb,
        i18n.t(lang, "menu_balance", bal=_fmt_balance(user.id)),
        common._menu_kb(lang),
    )
    await cb.answer()


@common.router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message) -> None:
    if common.config.ADMIN_TG_ID is None or message.from_user.id != common.config.ADMIN_TG_ID:
        await message.answer("❌ Только для владельца бота.")
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /broadcast &lt;текст&gt;")
        return
    sent = 0
    for row in common.ledger.all_users():
        try:
            await message.bot.send_message(row["tg_id"], parts[1])
            sent += 1
        except Exception:
            pass
    await message.answer(f"📣 Разослано: {sent} пользователям.")
