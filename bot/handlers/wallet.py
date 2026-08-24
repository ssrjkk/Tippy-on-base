"""Wallet handlers: balance, deposit, link, confirm, import/export, withdraw."""
import time
from decimal import Decimal
from aiogram import types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from eth_utils import is_address, to_checksum_address
from bot import i18n
from . import _common as common

async def _balance_text(tg_id: int) -> str:
    await common.ledger.ensure_user(tg_id, None)
    lang = await common.user_lang(tg_id)
    bal = await common.ledger.balance(tg_id)
    addr = await common.ledger.linked_address(tg_id)
    link_line = i18n.t(lang, 'bal_linked', addr=common._esc(addr)) if addr else i18n.t(lang, 'bal_nolink')
    pos = await common.ledger.user_positions(tg_id)
    bets_line = ''
    if pos:
        stake = sum((p['stake_micro'] for p in pos))
        potential = sum((p['potential_micro'] for p in pos))
        bets_line = i18n.t(lang, 'bal_ingame', n=len(pos), stake=common._fmt(stake), pot=common._fmt(potential))
    fees = await common.ledger.creator_fees(tg_id)
    fees_line = i18n.t(lang, 'bal_fees', fees=common._fmt(fees)) if fees else ''
    bal_str = f'{bal:.6f}'.rstrip('0').rstrip('.')
    return i18n.t(lang, 'menu_balance', bal=bal_str) + f'{link_line}{bets_line}{fees_line}'

async def _deposit_text(tg_id: int) -> str:
    lang = await common.user_lang(tg_id)
    linked = await common.ledger.linked_address(tg_id)
    head = i18n.t(lang, 'dep_head')
    public = i18n.t(lang, 'dep_public')
    disc = i18n.t(lang, 'dep_disclaimer')

    # Show CREATE2 address if enabled, else shared hot wallet
    from bot.create2 import get_deposit_address, is_create2_enabled
    c2_addr = get_deposit_address(tg_id)
    if is_create2_enabled() and c2_addr:
        addr = c2_addr
        source_note = "\n\n🔑 Твой личный адрес — средства автоматически на баланс."
    else:
        addr = common.base.hot_wallet()
        source_note = ""

    if linked:
        mid = i18n.t(lang, 'dep_linked', addr=common._esc(linked))
    else:
        mid = i18n.t(lang, 'dep_claim')
    return f'{head}\n\n<code>{addr}</code>\n\n{mid}{source_note}\n{public}\n\n{disc}'

async def _donate_text(bot, tg_id: int) -> str:
    uname = await common._get_bot_username(bot)
    link = f'https://t.me/{uname}?start=donate_{tg_id}'
    return i18n.t(await common.user_lang(tg_id), 'donate_text', link=link)

@common.router.message(Command('balance'))
async def cmd_balance(message: types.Message) -> None:
    await common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _balance_text(message.from_user.id))

@common.router.message(Command('deposit'))
async def cmd_deposit(message: types.Message) -> None:
    await common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    text = await _deposit_text(message.from_user.id)
    lang = await common.user_lang(message.from_user.id)
    qr = await common._qr_bytes(str(common.base.hot_wallet()))
    if qr:
        await message.answer_photo(BufferedInputFile(qr, filename='qr.png'), caption=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'deposit_qr_button'), url='https://basescan.org/address/' + str(common.base.hot_wallet()))]]))
    else:
        await message.answer(text)

@common.router.message(Command('donate'))
async def cmd_donate(message: types.Message) -> None:
    await common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _donate_text(message.bot, message.from_user.id))

@common.router.message(Command('claim'))
async def cmd_claim(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.TX_HASH_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'claim_format'))
        return
    ok, amount_micro, sender, reason = await common.ledger.claim(message.from_user.id, parts[1].lower())
    if not ok:
        if reason == 'not_owner':
            await message.answer(i18n.t(lang, 'confirm_not_owner', addr=common._esc(sender)))
            return
        await message.answer(i18n.t(lang, 'claim_fail'))
        return
    bal = await common.ledger.balance(message.from_user.id)
    await message.answer(i18n.t(lang, 'claim_ok', amount=common._fmt(amount_micro), bal=f'{bal:.6f}'.rstrip('0').rstrip('.')))

@common.router.message(Command('link'))
async def cmd_link(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.USDC_ADDR_RE.match(parts[1]) or (not is_address(parts[1])):
        await message.answer(i18n.t(lang, 'link_need_address'))
        return
    address = to_checksum_address(parts[1])
    nonce = await common.ledger.new_link_nonce(message.from_user.id, address)
    sign_text = f'Tippy: link {message.from_user.id}:{nonce}'
    await message.answer(i18n.t(lang, 'link_sign_prompt', text=sign_text))

@common.router.message(Command('confirm'))
async def cmd_confirm(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.SIG_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'confirm_need_signature'))
        return
    row = await common.ledger.get_link_nonce(message.from_user.id)
    if not row:
        await message.answer(i18n.t(lang, 'confirm_no_nonce'))
        return
    address, nonce = (row['address'], row['nonce'])
    if int(time.time()) - row['created_at'] > common.config.LINK_NONCE_TTL_SECONDS:
        await message.answer(i18n.t(lang, 'confirm_expired', min=str(common.config.LINK_NONCE_TTL_SECONDS // 60)))
        return
    sign_text = f'Tippy: link {message.from_user.id}:{nonce}'
    try:
        recovered = await common.base.recover_signer(sign_text, parts[1])
    except Exception:
        await message.answer(i18n.t(lang, 'confirm_sig_error'))
        return
    if recovered.lower() != address.lower():
        await message.answer(i18n.t(lang, 'confirm_bad_sig'))
        return
    await common.ledger.confirm_link(message.from_user.id, address, nonce)
    claimed = await common.ledger.claim_for_sender(message.from_user.id, address)
    extra = i18n.t(lang, 'confirm_extra', n=len(claimed)) if claimed else ''
    await message.answer(i18n.t(lang, 'confirm_ok', addr=common._esc(address), extra=extra))

@common.router.message(Command('wallet'))
async def cmd_wallet(message: types.Message) -> None:
    await common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) > 1 and parts[1].lower() == 'export':
        if not await common.require_private(message):
            return
        row = await common.ledger.get_wallet(message.from_user.id)
        if not row:
            row = await _ensure_wallet(message.from_user.id)
        privkey = common.wallets.decrypt(row['key_enc'])
        seed = common.wallets.decrypt(row['seed_enc'])
        await message.answer(i18n.t(lang, 'wallet_key_export', addr=row['address'], privkey=privkey, seed=seed))
        return
    row = await common.ledger.get_wallet(message.from_user.id)
    if not row:
        row = await _ensure_wallet(message.from_user.id)
    await message.answer(i18n.t(lang, 'wallet_addr', addr=row['address']))

@common.router.message(Command('import'))
async def cmd_import(message: types.Message) -> None:
    if not await common.require_private(message):
        return
    await common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer(i18n.t(lang, 'import_format'))
        return
    seed = ' '.join(parts[1:])
    if not common.wallets.is_valid_seed(seed):
        await message.answer(i18n.t(lang, 'import_bad_seed'))
        return
    try:
        address, key = common.wallets.wallet_from_seed(seed)
    except Exception:
        await message.answer(i18n.t(lang, 'import_seed_error'))
        return
    own = await common.ledger.wallet_address(message.from_user.id)
    if own and own.lower() != address.lower():
        await message.answer(i18n.t(lang, 'import_has_wallet', addr=common._esc(own)))
        return
    if not own and await common.ledger.wallet_address_exists(address):
        await message.answer(i18n.t(lang, 'import_wallet_taken', addr=common._esc(address)))
        return
    await common.ledger.save_wallet(message.from_user.id, address, common.wallets.encrypt(key), common.wallets.encrypt(seed))
    await message.answer(i18n.t(lang, 'import_ok', addr=common._esc(address)))

@common.router.message(Command('export'))
async def cmd_export(message: types.Message) -> None:
    if not await common.require_private(message):
        return
    lang = await common.user_lang(message.from_user.id)
    if message.from_user.id != common.config.ADMIN_TG_ID:
        await message.answer(i18n.t(lang, 'admin_only'))
        return
    await message.answer(i18n.t(lang, 'hot_wallet_admin', addr=common.base.hot_wallet()))

async def _ensure_wallet(tg_id: int) -> dict:
    row = await common.ledger.get_wallet(tg_id)
    if row:
        return row
    address, key, seed = common.wallets.new_wallet()
    await common.ledger.save_wallet(tg_id, address, common.wallets.encrypt(key), common.wallets.encrypt(seed))
    return await common.ledger.get_wallet(tg_id)

@common.router.message(Command('withdraw'))
async def cmd_withdraw(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) != 3 or not common.USDC_ADDR_RE.match(parts[1]) or (not common.AMOUNT_RE.match(parts[2])):
        await message.answer(i18n.t(lang, 'withdraw_format'))
        return
    to_address = parts[1]
    if not is_address(to_address):
        await message.answer(i18n.t(lang, 'withdraw_bad_address'))
        return
    amount = Decimal(parts[2])
    if amount <= 0:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if amount < common.config.MIN_WITHDRAW_USDC:
        await message.answer(i18n.t(lang, 'withdraw_min', n=f'{common.config.MIN_WITHDRAW_USDC:.2f}'))
        return
    if await common.ledger.withdrawals_today(message.from_user.id) >= common.config.MAX_WITHDRAWS_PER_DAY:
        await message.answer(i18n.t(lang, 'withdraw_daily_limit', n=str(common.config.MAX_WITHDRAWS_PER_DAY)))
        return
    wait = await common._throttle(message.from_user.id, 'withdraw')
    if wait:
        await message.answer(wait)
        return
    amount_micro = common._to_micro(amount)
    fee_micro = common.base.withdraw_fee(amount_micro)
    total_micro = amount_micro + fee_micro
    bal = await common.ledger.balance(message.from_user.id)
    if bal < Decimal(total_micro) / Decimal(10 ** common.config.USDC_DECIMALS):
        bal_str = f'{bal:.6f}'.rstrip('0').rstrip('.')
        await message.answer(i18n.t(lang, 'withdraw_balance_short', need=common._fmt(total_micro), fee=common._fmt(fee_micro), bal=bal_str))
        return
    # AML check: flag large/rapid withdrawals (silent — admin sees in audit log)
    await common.ledger.check_aml_withdraw(message.from_user.id, amount_micro, to_address)
    wd_id = await common.ledger.reserve_withdraw(message.from_user.id, to_address, amount_micro, fee_micro)
    if wd_id is None:
        await message.answer(i18n.t(lang, 'tip_no_balance'))
        return
    try:
        tx_hash = await common.base.send_usdc(to_address, amount_micro)
        await common.ledger.mark_withdraw_done(wd_id, tx_hash)
        await common.ledger.record_withdraw_fee(message.from_user.id, to_address, fee_micro, tx_hash)
        tx_url = f'{common.config.BASESCAN_URL}/tx/{tx_hash}'
        await message.answer(i18n.t(lang, 'withdraw_ok', amount=common._fmt(amount_micro), fee=common._fmt(fee_micro), tx_url=tx_url, tx=tx_hash[:22]))
    except Exception as e:
        await common.ledger.refund_withdraw(wd_id, message.from_user.id, total_micro)
        await message.answer(i18n.t(lang, 'withdraw_error', error=str(e)))

@common.router.message(Command('tx'))
async def cmd_tx(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.TX_HASH_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'tx_format'))
        return
    info = await common.base.tx_info(parts[1])
    if not info:
        await message.answer(i18n.t(lang, 'tx_not_found'))
    else:
        status = '⏳ pending' if info['status'] is None else '✅ confirmed' if info['status'] else '❌ reverted'
        usdc_line = ''
        if info['value_micro'] is not None:
            usdc_line = f"🪙 USDC: <b>{common._fmt(info['value_micro'])} USDC</b> → <code>{info['usdc_to']}</code>"
        to_addr = info['to'] or 'contract creation'
        await message.answer(i18n.t(lang, 'tx_info', from_addr=info['from'], to_addr=to_addr, status=status, usdc_line=usdc_line, url=f"{common.config.BASESCAN_URL}/tx/{info['hash']}"))