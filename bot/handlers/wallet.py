"""Wallet handlers: balance, deposit, link, confirm, import/export, withdraw."""

import time
from decimal import Decimal

from aiogram import types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from eth_utils import is_address, to_checksum_address

from . import _common as common


async def _balance_text(tg_id: int) -> str:
    common.ledger.ensure_user(tg_id, None)
    bal = common.ledger.balance(tg_id)
    addr = common.ledger.linked_address(tg_id)
    link_line = f"\n🔗 Кошелёк: <code>{common._esc(addr)}</code>" if addr else "\n🔗 Кошелёк не привязан — /link"
    pos = common.ledger.user_positions(tg_id)
    bets_line = ""
    if pos:
        stake = sum(p["stake_micro"] for p in pos)
        potential = sum(p["potential_micro"] for p in pos)
        bets_line = (
            f"\n🎲 В игре: <b>{len(pos)}</b> позиция(и) на <b>{common._fmt(stake)} USDC</b>\n"
            f"🏆 Потенциальный выигрыш: <b>{common._fmt(potential)} USDC</b>\n"
            f"📌 Твои ставки: /mybets"
        )
    fees = common.ledger.creator_fees(tg_id)
    fees_line = f"\n🧾 Заработано на рынках: <b>{common._fmt(fees)} USDC</b>" if fees else ""
    return (
        f"💰 Баланс: <b>{bal:.6f}".rstrip("0").rstrip(".")
        + f" USDC</b>{link_line}{bets_line}{fees_line}"
    )


async def _deposit_text(tg_id: int) -> str:
    addr = common.base.hot_wallet()
    linked = common.ledger.linked_address(tg_id)
    if linked:
        return (
            f"💳 Отправь USDC на адрес бота\n"
            f"🟦 <b>Сеть Base</b> · монета USDC (ERC-20)\n\n"
            f"<code>{addr}</code>\n\n"
            f"С твоего привязанного кошелька <code>{common._esc(linked)}</code> — зачислится автоматически ✅\n"
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
    uname = await common._get_bot_username(bot)
    link = f"https://t.me/{uname}?start=donate_{tg_id}"
    return (
        f"💛 <b>Твоя страница донатов</b>\n\n"
        f"Скинь эту ссылку куда угодно — по ней откроется твой адрес для USDC:\n"
        f"<code>{link}</code>\n\n"
        f"По ссылке сразу видно, кому и куда платить — без посредников."
    )


@common.router.message(Command("balance"))
async def cmd_balance(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _balance_text(message.from_user.id))


@common.router.message(Command("deposit"))
async def cmd_deposit(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    text = await _deposit_text(message.from_user.id)
    qr = common._qr_bytes(str(common.base.hot_wallet()))
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
                            + str(common.base.hot_wallet()),
                        )
                    ]
                ]
            ),
        )
    else:
        await message.answer(text)


@common.router.message(Command("donate"))
async def cmd_donate(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _donate_text(message.bot, message.from_user.id))


@common.router.message(Command("claim"))
async def cmd_claim(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.TX_HASH_RE.match(parts[1]):
        await message.answer("Формат: /claim <i>&lt;0x…tx_hash&gt;</i>")
        return
    ok, amount_micro, sender, reason = common.ledger.claim(message.from_user.id, parts[1].lower())
    if not ok:
        if reason == "not_owner":
            await message.answer(
                f"❌ Этот депозит отправлен с кошелька <code>{common._esc(sender)}</code>.\n"
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
        f"✅ Зачтено <b>{common._fmt(amount_micro)} USDC</b> от <code>{common._esc(sender)}</code>"
    )


@common.router.message(Command("link"))
async def cmd_link(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.USDC_ADDR_RE.match(parts[1]) or not is_address(parts[1]):
        await message.answer("Формат: /link <i>&lt;0x…адрес&gt;</i>")
        return
    address = to_checksum_address(parts[1])
    nonce = common.ledger.new_link_nonce(message.from_user.id, address)
    sign_text = f"Tippy: link {message.from_user.id}:{nonce}"
    await message.answer(
        "🔗 <b>Привязка кошелька</b>\n\n"
        "Подпиши сообщение в своём кошельке (WalletConnect / MetaMask / любой)\n\n"
        "🖊 Сообщение:\n"
        f"<code>{sign_text}</code>\n\n"
        "Потом пришли сюда /confirm <i>&lt;0x…подпись&gt;</i>"
    )


@common.router.message(Command("confirm"))
async def cmd_confirm(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.SIG_RE.match(parts[1]):
        await message.answer("Формат: /confirm <i>&lt;0x…подпись&gt;</i>")
        return
    row = common.ledger.get_link_nonce(message.from_user.id)
    if not row:
        await message.answer("❌ Сначала начни привязку: /link <i>&lt;адрес&gt;</i>")
        return
    address, nonce = row["address"], row["nonce"]
    if int(time.time()) - row["created_at"] > common.config.LINK_NONCE_TTL_SECONDS:
        await message.answer(
            f"⏳ Код привязки устарел (действует {common.config.LINK_NONCE_TTL_SECONDS // 60} мин). "
            f"Начни заново: /link <i>&lt;адрес&gt;</i>"
        )
        return
    sign_text = f"Tippy: link {message.from_user.id}:{nonce}"
    try:
        recovered = common.base.recover_signer(sign_text, parts[1])
    except Exception:
        await message.answer("❌ Не удалось разобрать подпись.")
        return
    if recovered.lower() != address.lower():
        await message.answer(
            f"❌ Подпись не совпадает: подписавший <code>{common._esc(recovered)}</code>, "
            f"ожидали <code>{common._esc(address)}</code>"
        )
        return
    common.ledger.confirm_link(message.from_user.id, address, nonce)
    claimed = common.ledger.claim_for_sender(message.from_user.id, address)
    extra = f"\nСразу зачислено: {len(claimed)} депозит(ов)" if claimed else ""
    await message.answer(
        f"✅ Кошелёк <code>{common._esc(address)}</code> привязан.\n"
        f"Теперь депозиты с него зачисляются автоматически.{extra}"
    )


@common.router.message(Command("wallet"))
async def cmd_wallet(message: types.Message) -> None:
    """Personal wallet: /wallet (address) or /wallet export (key + seed)."""
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.strip().split()
    if len(parts) > 1 and parts[1].lower() == "export":
        row = common.ledger.get_wallet(message.from_user.id)
        if not row:
            row = _ensure_wallet(message.from_user.id)
        key = common.wallets.decrypt(row["key_enc"])
        seed = common.wallets.decrypt(row["seed_enc"])
        await message.answer(
            f"🔑 <b>Твой кошелёк</b>\n\n"
            f"Адрес: <code>{row['address']}</code>\n"
            f"Приватный ключ: <code>{key}</code>\n"
            f"Сид-фраза: <code>{seed}</code>\n\n"
            f"⚠️ <b>Не показывай это никому.</b> Кто знает ключ — тот владеет средствами. "
            f"Экспортнув ключ, ты можешь забрать баланс на любой кошелёк (/withdraw)."
        )
        return
    row = common.ledger.get_wallet(message.from_user.id)
    if not row:
        row = _ensure_wallet(message.from_user.id)
    await message.answer(
        f"👛 <b>Твой кошелёк</b>\n\n"
        f"Адрес: <code>{row['address']}</code>\n"
        f"🟦 Сеть Base · монета USDC\n\n"
        f"Ключ и сид-фраза доступны: /wallet export\n"
        f"Привязать свой кошелёк сид-фразой: /import &lt;фраза&gt;"
    )


@common.router.message(Command("import"))
async def cmd_import(message: types.Message) -> None:
    """Attach an existing wallet by BIP-39 seed phrase (self-custody import)."""
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Формат: /import <i>&lt;12 или 24 слова&gt;</i>")
        return
    seed = " ".join(parts[1:])
    if not common.wallets.is_valid_seed(seed):
        await message.answer("❌ Сид-фраза должна содержать 12 или 24 слова.")
        return
    try:
        address, key = common.wallets.wallet_from_seed(seed)
    except Exception:
        await message.answer("❌ Не удалось восстановить кошелёк из этой сид-фразы.")
        return
    own = common.ledger.wallet_address(message.from_user.id)
    if own and own.lower() != address.lower():
        await message.answer(
            f"⚠️ У тебя уже есть кошелёк <code>{common._esc(own)}</code>. "
            f"Сначала выведи с него средства (/withdraw), затем импортируй новый."
        )
        return
    if not own and common.ledger.wallet_address_exists(address):
        await message.answer(
            f"❌ Кошелёк <code>{common._esc(address)}</code> уже привязан к другому пользователю."
        )
        return
    common.ledger.save_wallet(
        message.from_user.id, address, common.wallets.encrypt(key), common.wallets.encrypt(seed)
    )
    await message.answer(
        f"✅ Кошелёк <code>{common._esc(address)}</code> импортирован.\n"
        f"Ключ и сид хранятся зашифрованными, выгрузить: /wallet export"
    )


@common.router.message(Command("export"))
async def cmd_export(message: types.Message) -> None:
    """Owner only: hot-wallet private key (operational access)."""
    if message.from_user.id != common.config.ADMIN_TG_ID:
        await message.answer("❌ Только владелец бота.")
        return
    await message.answer(
        f"🟦 <b>Hot wallet бота</b>\n\n"
        f"Адрес: <code>{common.base.hot_wallet()}</code>\n"
        f"Приватный ключ: <code>{common.config.HOT_WALLET_KEY}</code>\n\n"
        f"⚠️ Это ключ, который держит балансы пользователей. Никому не передавай."
    )


def _ensure_wallet(tg_id: int) -> dict:
    """Create a personal wallet for the user if missing; returns the row."""
    row = common.ledger.get_wallet(tg_id)
    if row:
        return row
    address, key, seed = common.wallets.new_wallet()
    common.ledger.save_wallet(
        tg_id, address, common.wallets.encrypt(key), common.wallets.encrypt(seed)
    )
    return common.ledger.get_wallet(tg_id)


@common.router.message(Command("withdraw"))
async def cmd_withdraw(message: types.Message) -> None:
    parts = message.text.strip().split()
    if len(parts) != 3 or not common.USDC_ADDR_RE.match(parts[1]) or not common.AMOUNT_RE.match(parts[2]):
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
    if amount < common.config.MIN_WITHDRAW_USDC:
        await message.answer(
            f"Минимум для вывода: <b>{common.config.MIN_WITHDRAW_USDC:.2f} USDC</b>."
        )
        return
    if common.ledger.withdrawals_today(message.from_user.id) >= common.config.MAX_WITHDRAWS_PER_DAY:
        await message.answer(
            f"⏳ Лимит <b>{common.config.MAX_WITHDRAWS_PER_DAY} выводов в сутки</b>. "
            f"Попробуй завтра."
        )
        return
    wait = common._throttle(message.from_user.id, "withdraw")
    if wait:
        await message.answer(wait)
        return
    amount_micro = common._to_micro(amount)
    fee_micro = common.base.withdraw_fee(amount_micro)
    total_micro = amount_micro + fee_micro
    bal = common.ledger.balance(message.from_user.id)
    if bal < Decimal(total_micro) / Decimal(10**common.config.USDC_DECIMALS):
        await message.answer(
            f"❌ Недостаточно баланса. Нужно <b>{common._fmt(total_micro)} USDC</b> "
            f"(сумма + комиссия {common._fmt(fee_micro)}).\nТвой баланс: <b>{bal:.6f}".rstrip("0").rstrip(".") + " USDC</b>"
        )
        return

    # Atomically debit and reserve the withdrawal as 'pending' BEFORE touching
    # the chain, so a crash between the debit and the send is detected and
    # refunded by the withdraw watcher (tx_hash stays NULL -> refund after timeout).
    wd_id = common.ledger.reserve_withdraw(
        message.from_user.id, to_address, amount_micro, fee_micro
    )
    if wd_id is None:
        await message.answer("❌ Недостаточно баланса.")
        return

    try:
        tx_hash = common.base.send_usdc(to_address, amount_micro)
        common.ledger.mark_withdraw_done(wd_id, tx_hash)
        common.ledger.record_withdraw_fee(
            message.from_user.id, to_address, fee_micro, tx_hash
        )
        await message.answer(
            f"✅ Отправлено <b>{common._fmt(amount_micro)} USDC</b> "
            f"(комиссия {common._fmt(fee_micro)})\n"
            f"Tx: <code>https://basescan.org/tx/{tx_hash}</code>"
        )
    except Exception as e:
        # Full refund (incl. fee) on failure — never charge for a failed send.
        common.ledger.refund_withdraw(wd_id, message.from_user.id, total_micro)
        await message.answer(f"❌ Ошибка отправки: {e}")
