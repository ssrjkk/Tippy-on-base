"""Bets / prediction-market handlers."""
import json
import time
from datetime import datetime
from decimal import Decimal
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot import i18n
from . import _common as common
__all__ = ['_bet_card', '_bet_create', '_bet_place', '_bets_text', '_market_detail_text', '_notify_bet_cancelled', '_notify_bet_result', '_parse_deadline', '_rel_deadline', 'cb_bet_amount', 'cb_bet_place', 'cb_market', 'cb_res', 'cmd_bet', 'cmd_bets', 'cmd_cancel', 'cmd_mybets', 'cmd_resolve']

async def _lang(tg_id: int | None) -> str:
    if tg_id is None:
        return 'ru'
    return await common.user_lang(tg_id)

@common.router.message(Command('bet'))
async def cmd_bet(message: types.Message) -> None:
    lang = await _lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer(i18n.t(lang, 'bet_format'))
        return
    if parts[1] == 'create':
        await _bet_create(message, parts)
        return
    await _bet_place(message, parts)

async def _bet_create(message: types.Message, parts: list[str]) -> None:
    lang = await _lang(message.from_user.id)
    body = ' '.join(parts[2:])
    segs = [s.strip() for s in body.split('|') if s.strip()]
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
        await message.answer(i18n.t(lang, 'bet_create_help'))
        return
    if len(segs) > 5:
        await message.answer(i18n.t(lang, 'bet_max_options'))
        return
    question = segs[0]
    options = segs[1:]
    if len(question) > 200:
        await message.answer(i18n.t(lang, 'bet_question_long'))
        return
    for o in options:
        if len(o) > common.config.MAX_OPTION_LEN:
            await message.answer(i18n.t(lang, 'bet_option_long', n=common.config.MAX_OPTION_LEN, o=o[:40]))
            return
    bet_id = await common.ledger.create_bet(message.from_user.id, question, options, close_at=deadline)
    if deadline:
        dl = i18n.t(lang, 'bet_deadline_to', time=datetime.fromtimestamp(deadline).strftime('%d.%m %H:%M'))
    else:
        dl = i18n.t(lang, 'bet_no_deadline')
    opt_lines = '\n'.join((f'{i + 1}) {o}' for i, o in enumerate(options)))
    await message.answer(f"{i18n.t(lang, 'bet_created', id=bet_id)}\n\n<b>{question}</b>\n{opt_lines}{dl}\n\n{i18n.t(lang, 'bet_howto', id=bet_id)}")

def _parse_deadline(s: str) -> int | None:
    m = common.DEADLINE_RE.match(s.lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    secs = n * (3600 if unit == 'h' else 86400)
    return int(time.time()) + secs

async def _bet_place(message: types.Message, parts: list[str]) -> None:
    lang = await _lang(message.from_user.id)
    if len(parts) != 4 or not common.BET_ID_RE.match(parts[1]) or (not common.AMOUNT_RE.match(parts[3])):
        await message.answer(i18n.t(lang, 'bet_format'))
        return
    try:
        bet_id = int(parts[1])
        option_idx = int(parts[2]) - 1
    except ValueError:
        await message.answer(i18n.t(lang, 'bet_format'))
        return
    amount = Decimal(parts[3])
    if amount <= 0:
        await message.answer(i18n.t(lang, 'amount_positive'))
        return
    if amount > common.config.MAX_BET_USDC:
        await message.answer(i18n.t(lang, 'bet_max_amount', n=f'{common.config.MAX_BET_USDC:.0f}'))
        return
    amount_micro = common._to_micro(amount)
    wait = await common._throttle(message.from_user.id, 'bet')
    if wait:
        await message.answer(wait)
        return
    bet = await common.ledger.get_bet(bet_id)
    if not bet:
        await message.answer(i18n.t(lang, 'bet_not_found'))
        return
    options = json.loads(bet['options'])
    if option_idx < 0 or option_idx >= len(options):
        await message.answer(i18n.t(lang, 'bet_bad_option'))
        return
    result = await common.ledger.place_bet(bet_id, message.from_user.id, option_idx, amount_micro)
    if result == 'closed':
        await message.answer(i18n.t(lang, 'bet_closed'))
        return
    if result == 'deadline':
        await message.answer(i18n.t(lang, 'bet_deadline_passed'))
        return
    if result == 'badopt':
        await message.answer(i18n.t(lang, 'bet_bad_option'))
        return
    if result == 'balance':
        await message.answer(i18n.t(lang, 'no_balance'))
        return
    bal = await common.ledger.balance(message.from_user.id)
    bal_str = f'{bal:.4f}'.rstrip('0').rstrip('.')
    await message.answer(i18n.t(lang, 'bet_confirmed', id=bet_id, label=options[option_idx], amount=common._fmt(amount_micro), bal=bal_str))

def _rel_deadline(ts: int) -> str:
    """Relative deadline for cards."""
    left = ts - int(time.time())
    if left <= 0:
        return 'deadline passed'
    days, rem = divmod(left, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {mins}m'
    return f'{mins}m'

async def _bet_card(bet, tg_id: int | None=None) -> str:
    view = await common.ledger.market_view(bet['id'])
    if not view:
        return ''
    return await _market_detail_text(view, tg_id)

async def _market_detail_text(view: dict, tg_id: int | None=None) -> str:
    lang = await _lang(tg_id)
    lines = [f"🎯 #{view['id']} <b>{view['question']}</b>"]
    if view['status'] == 'resolved':
        winner = view['options'][view['winner']]['label'] if view['winner'] is not None and view['winner'] < len(view['options']) else '?'
        lines.append(i18n.t(lang, 'bet_resolved', winner=winner))
    elif view['status'] == 'cancelled':
        lines.append(i18n.t(lang, 'bet_cancelled'))
    elif view['expired']:
        lines.append(i18n.t(lang, 'bet_expired', id=view['id']))
    elif view['close_at']:
        creator = view['creator']['username'] or 'id' + str(view['creator']['id'])
        lines.append(f"⏰ {_rel_deadline(view['close_at'])} · @{creator}")
    else:
        creator = view['creator']['username'] or 'id' + str(view['creator']['id'])
        lines.append(f'⌛ /resolve · @{creator}')
    my_stake = await common.ledger.user_bet_stake(view['id'], tg_id) if tg_id else {}
    for o in view['options']:
        mine = f" · <b>{i18n.t(lang, 'your_stake', amt=common._fmt(my_stake[o['index']]))}</b>" if my_stake.get(o['index']) else ''
        backers = f"{o['backers']}👤" if o['backers'] else ''
        lines.append(f"{o['index'] + 1}) {o['label']} — <b>{common._fmt(o['pool'])} USDC</b> ({o['probability']}%, {backers}){mine}")
    lines.append(i18n.t(lang, 'bet_pot', pot=common._fmt(view['pot']), backers=view['total_backers']))
    lines.append(i18n.t(lang, 'bet_fee_note'))
    return '\n'.join(lines)

async def _bets_text(tg_id: int | None=None) -> tuple[str, InlineKeyboardMarkup]:
    lang = await _lang(tg_id)
    bets = await common.ledger.open_bets(8)
    if not bets:
        return (i18n.t(lang, 'bet_empty'), InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'btn_mk_create'), callback_data='betcreate')]]))
    lines = [i18n.t(lang, 'bet_list_header'), '']
    for b in bets:
        view = await common.ledger.market_view(b['id'])
        if not view:
            continue
        if view['expired']:
            meta = i18n.t(lang, 'bet_list_expired', id=view['id'])
        elif view['close_at']:
            meta = ' ⏰ ' + _rel_deadline(view['close_at'])
        else:
            meta = ''
        lines.append(f"#{view['id']} {view['question']} — {common._fmt(view['pot'])} USDC · {view['total_backers']}👤{meta}")
    lines.append(i18n.t(lang, 'bet_list_hint'))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🎯 #{b['id']}: {b['question'][:38]}", callback_data=f"market:{b['id']}")] for b in bets] + [[InlineKeyboardButton(text=i18n.t(lang, 'btn_mk_create'), callback_data='betcreate')]])
    return ('\n'.join(lines), kb)

@common.router.message(Command('bets'))
async def cmd_bets(message: types.Message) -> None:
    text, kb = await _bets_text(message.from_user.id)
    await message.answer(text, reply_markup=kb)

@common.router.callback_query(F.data.startswith('market:'))
async def cb_market(cb: types.CallbackQuery) -> None:
    lang = await _lang(cb.from_user.id)
    bet_id = int(cb.data.split(':', 1)[1])
    view = await common.ledger.market_view(bet_id)
    if not view:
        await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=f"{o['index'] + 1}) {o['label'][:22]} — {o['probability']}%", callback_data=f"betq:{bet_id}:{o['index']}")] for o in view['options']]
    user = cb.from_user
    if user and view['status'] == 'open' and (user.id == view['creator']['id']):
        rows.append([InlineKeyboardButton(text='🏁 ' + i18n.t(lang, 'btn_close_market'), callback_data=f'res:{bet_id}')])
    rows.append([InlineKeyboardButton(text=i18n.t(lang, 'btn_all_markets'), callback_data='bets')])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await common._edit_menu(cb, await _market_detail_text(view, user.id if user else None), kb)
    await cb.answer()

@common.router.callback_query(F.data.startswith('res:'))
async def cb_res(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = await _lang(user.id)
    parts = cb.data.split(':')
    bet_id = int(parts[1])
    view = await common.ledger.market_view(bet_id)
    if not view:
        await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)
        return
    if view['status'] != 'open':
        await cb.answer(i18n.t(lang, 'market_closed'), show_alert=True)
        return
    if user.id != view['creator']['id']:
        await cb.answer(i18n.t(lang, 'market_only_creator'), show_alert=True)
        return
    if len(parts) == 2:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🏆 {o['label'][:30]}", callback_data=f"res:{bet_id}:{o['index']}")] for o in view['options']] + [[InlineKeyboardButton(text='◀️ ' + i18n.t(lang, 'btn_back'), callback_data=f'market:{bet_id}')]])
        await common._edit_menu(cb, i18n.t(lang, 'bet_resolve_title', id=bet_id, question=view['question'], pot=common._fmt(view['pot'])), kb)
        await cb.answer()
        return
    idx = int(parts[2])
    if idx >= len(view['options']):
        await cb.answer(i18n.t(lang, 'bet_bad_option'), show_alert=True)
        return
    ok, msg = await common.ledger.resolve_bet(bet_id, idx, user.id)
    if not ok:
        await cb.answer(msg, show_alert=True)
        return
    await _notify_bet_result(cb.message, bet_id)
    new_view = await common.ledger.market_view(bet_id)
    text = i18n.t(lang, 'bet_resolved_header', id=bet_id) + f'\n{msg}\n\n' + await _market_detail_text(new_view, user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'btn_all_markets'), callback_data='bets')]])
    await common._edit_menu(cb, text, kb)
    await cb.answer()

@common.router.callback_query(F.data.startswith('betq:'))
async def cb_bet_amount(cb: types.CallbackQuery) -> None:
    lang = await _lang(cb.from_user.id)
    _, bet_id, opt = cb.data.split(':')
    view = await common.ledger.market_view(int(bet_id))
    if not view:
        await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)
        return
    label = view['options'][int(opt)]['label']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f'{a} USDC', callback_data=f'bets:{bet_id}:{opt}:{a}') for a in common.QUICK_AMOUNTS], [InlineKeyboardButton(text='◀️ ' + i18n.t(lang, 'btn_back'), callback_data=f'market:{bet_id}')]])
    await common._edit_menu(cb, i18n.t(lang, 'bet_amount_ask', id=bet_id, question=view['question'], label=label), kb)
    await cb.answer()

@common.router.callback_query(F.data.startswith('bets:'))
async def cb_bet_place(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = await _lang(user.id)
    _, bet_id, opt, amt = cb.data.split(':')
    bet_id, opt = (int(bet_id), int(opt))
    try:
        amount_micro = common._to_micro(Decimal(amt))
    except Exception:
        await cb.answer(i18n.t(lang, 'bad_amount'), show_alert=True)
        return
    wait = await common._throttle(user.id, 'bet')
    if wait:
        await cb.answer(wait, show_alert=True)
        return
    bet = await common.ledger.get_bet(bet_id)
    if not bet:
        await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)
        return
    result = await common.ledger.place_bet(bet_id, user.id, opt, amount_micro)
    if result == 'ok':
        options = json.loads(bet['options'])
        bal = await common.ledger.balance(user.id)
        bal_str = f'{bal:.4f}'.rstrip('0').rstrip('.')
        text = i18n.t(lang, 'bet_confirmed', id=bet_id, label=options[opt], amount=common._fmt(amount_micro), bal=bal_str)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'btn_market'), callback_data=f'market:{bet_id}'), InlineKeyboardButton(text=i18n.t(lang, 'btn_all_markets'), callback_data='bets')]])
        await common._edit_menu(cb, text, kb)
        await cb.answer()
    elif result == 'deadline':
        await cb.answer(i18n.t(lang, 'bet_deadline_passed'), show_alert=True)
    elif result == 'closed':
        await cb.answer(i18n.t(lang, 'bet_closed'), show_alert=True)
    elif result == 'balance':
        await cb.answer(i18n.t(lang, 'no_balance'), show_alert=True)
    else:
        await cb.answer(i18n.t(lang, 'error_generic'), show_alert=True)

@common.router.message(Command('resolve'))
async def cmd_resolve(message: types.Message) -> None:
    lang = await _lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) != 3 or not common.BET_ID_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'resolve_format'))
        return
    bet_id = int(parts[1])
    try:
        winning_idx = int(parts[2]) - 1
    except ValueError:
        await message.answer(i18n.t(lang, 'resolve_format'))
        return
    ok, msg = await common.ledger.resolve_bet(bet_id, winning_idx, message.from_user.id)
    if not ok:
        await message.answer(f'❌ {msg}')
        return
    await message.answer(i18n.t(lang, 'bet_resolved_header', id=bet_id) + f'\n{msg}\n\n' + i18n.t(lang, 'bet_payouts_sent'))
    await _notify_bet_result(message, bet_id)

async def _notify_bet_result(message: types.Message, bet_id: int) -> None:
    bet = await common.ledger.get_bet(bet_id)
    if not bet:
        return
    payouts = await common.ledger.payouts_for(bet_id)
    winner_label = ''
    if bet['winner'] is not None:
        options = json.loads(bet['options'])
        if bet['winner'] < len(options):
            winner_label = options[bet['winner']]
    by_user: dict[int, list[dict]] = {}
    for p in payouts:
        by_user.setdefault(p['tg_id'], []).append(p)
    for tg_id, rows in by_user.items():
        lang = await _lang(tg_id)
        won = [r for r in rows if r['win']]
        if won:
            total = sum((r['net_micro'] for r in won))
            line = i18n.t(lang, 'bet_notify_win', id=bet_id, question=bet['question'], winner=winner_label, amount=common._fmt(total))
        else:
            labels = '», «'.join((r['option'] for r in rows))
            line = i18n.t(lang, 'bet_notify_lose', id=bet_id, question=bet['question'], winner=winner_label, labels=labels)
        try:
            await message.bot.send_message(tg_id, line)
        except Exception:
            pass

async def _notify_bet_cancelled(message: types.Message, bet_id: int) -> None:
    bet = await common.ledger.get_bet(bet_id)
    if not bet:
        return
    positions = await common.ledger._bet_positions(bet_id)
    seen = set()
    for p in positions:
        tg_id = int(p['tg_id'])
        if tg_id in seen:
            continue
        seen.add(tg_id)
        lang = await _lang(tg_id)
        try:
            await message.bot.send_message(tg_id, i18n.t(lang, 'bet_notify_cancel', id=bet_id, question=bet['question']))
        except Exception:
            pass

@common.router.message(Command('cancel'))
async def cmd_cancel(message: types.Message) -> None:
    lang = await _lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.BET_ID_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'cancel_format'))
        return
    ok, msg = await common.ledger.cancel_bet(int(parts[1]), message.from_user.id)
    if not ok:
        await message.answer(f'❌ {msg}')
        return
    await message.answer(f'✅ {msg}')
    await _notify_bet_cancelled(message, int(parts[1]))

@common.router.message(Command('mybets'))
async def cmd_mybets(message: types.Message) -> None:
    lang = await _lang(message.from_user.id)
    positions = await common.ledger.user_positions(message.from_user.id)
    if not positions:
        await message.answer(i18n.t(lang, 'bet_my_empty'))
        return
    lines = [i18n.t(lang, 'bet_my_header')]
    for p in positions:
        lines.append(f"🎯 #{p['bet_id']} <b>{p['question']}</b>\n   • {p['option']} — {common._fmt(p['stake_micro'])} USDC\n   • {i18n.t(lang, 'potential_win', amt=common._fmt(p['potential_micro']))}")
    await message.answer('\n\n'.join(lines))