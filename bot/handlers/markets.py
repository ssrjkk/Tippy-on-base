"""Prediction markets v2: Polymarket-style LMSR AMM handlers."""
import json
import time
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot import i18n
from ..ledger import lmsr_prices
from . import _common as common
from .bets import _parse_deadline, _rel_deadline
__all__ = ['_market_card', '_market_create', '_markets_text', '_notify_market_result', 'cb_market_card', 'cb_market_list', 'cb_mk_buy', 'cb_mk_cancel', 'cb_mk_do', 'cb_mk_resolve', 'cb_mk_sell', 'cb_mk_selldo', 'cmd_market', 'cmd_markets', 'cmd_positions', 'cmd_sell', 'cmd_trade']
_SELL_PCTS = ('25', '50', '100')

def _pct(p: Decimal) -> str:
    return f'{int((p * 100).to_integral_value(rounding=ROUND_HALF_UP))}%'

def _bar(p: Decimal, width: int=10) -> str:
    filled = int((p * width).to_integral_value(rounding='ROUND_HALF_UP'))
    return '▰' * filled + '▱' * (width - filled)

@common.router.message(Command('market'))
async def cmd_market(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer(i18n.t(lang, 'market_trade_format'))
        return
    if parts[1] == 'create':
        await _market_create(message, parts)
        return
    await message.answer(i18n.t(lang, 'market_trade_format'))

async def _market_create(message: types.Message, parts: list[str]) -> None:
    lang = await common.user_lang(message.from_user.id)
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
    if not segs:
        await message.answer(i18n.t(lang, 'market_trade_format'))
        return
    head = segs[0].split(None, 1)
    if len(head) != 2 or not common.AMOUNT_RE.match(head[0]):
        await message.answer(i18n.t(lang, 'market_trade_format'))
        return
    try:
        subsidy = common._to_micro(Decimal(head[0]))
    except Exception:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if subsidy < common._to_micro(common.config.MARKET_MIN_SUBSIDY_USDC):
        await message.answer(i18n.t(lang, 'market_min_bank', n=f'{common.config.MARKET_MIN_SUBSIDY_USDC:.0f}'))
        return
    if subsidy > common._to_micro(common.config.MARKET_MAX_SUBSIDY_USDC):
        await message.answer(i18n.t(lang, 'market_max_bank', n=f'{common.config.MARKET_MAX_SUBSIDY_USDC:.0f}'))
        return
    segs = [head[1], *segs[1:]]
    if len(segs) < 3:
        await message.answer(i18n.t(lang, 'market_min_options'))
        return
    if len(segs) > 4:
        await message.answer(i18n.t(lang, 'market_max_options'))
        return
    question = segs[0]
    options = segs[1:]
    if len(question) > 200:
        await message.answer(i18n.t(lang, 'market_question_long'))
        return
    for o in options:
        if len(o) > common.config.MAX_OPTION_LEN:
            await message.answer(i18n.t(lang, 'market_option_long', n=common.config.MAX_OPTION_LEN, o=o[:40]))
            return
    wait = await common._throttle(message.from_user.id, 'market')
    if wait:
        await message.answer(wait)
        return
    result = await common.ledger.create_market(message.from_user.id, question, options, subsidy, close_at=deadline)
    if result == 'balance':
        await message.answer(i18n.t(lang, 'market_balance'))
        return
    dl = i18n.t(lang, 'market_deadline_fmt', time=datetime.fromtimestamp(deadline).strftime('%d.%m %H:%M')) if deadline else i18n.t(lang, 'market_no_deadline_card')
    opts_text = '\n'.join((f'{i + 1}) {o}' for i, o in enumerate(options)))
    await message.answer(i18n.t(lang, 'market_created_msg', id=result, question=question, options=opts_text, liquidity=i18n.t(lang, 'market_liquidity', amount=common._fmt(subsidy)), deadline=dl, hint=i18n.t(lang, 'market_trade_hint', id=result)))

async def _market_card(m: dict, tg_id: int | None=None) -> str:
    lang = await common.user_lang(tg_id) if tg_id else 'ru'
    mid = int(m['id'])
    options = json.loads(m['options'])
    quantities = await common.ledger.market_quantities(mid)
    prices = lmsr_prices(quantities, int(m['b_micro']))
    lines = [f"📈 #{mid} <b>{m['question']}</b>"]
    if m['status'] == 'resolved':
        w = int(m['winner']) if m['winner'] is not None else -1
        label = options[w] if 0 <= w < len(options) else '?'
        lines.append(i18n.t(lang, 'market_card_resolved', label=label))
    elif m['status'] == 'cancelled':
        lines.append(i18n.t(lang, 'market_card_cancelled'))
    elif m['close_at'] and int(time.time()) > int(m['close_at']):
        lines.append(i18n.t(lang, 'market_card_deadline_passed'))
    elif m['close_at']:
        lines.append(f"⏰ {_rel_deadline(int(m['close_at']))}")
    pos = await common.ledger.user_market_position(mid, tg_id) if tg_id else {}
    for i, o in enumerate(options):
        p = prices[i]
        mine = ''
        if i in pos and pos[i]['shares'] > 0:
            value_micro = int(pos[i]['shares'] * p)
            mine = '\n' + i18n.t(lang, 'market_your_shares', shares=common._fmt(pos[i]['shares']), value=common._fmt(value_micro))
        lines.append(f'{i + 1}) {o} — <b>{_pct(p)}</b> {_bar(p)}{mine}')
    escrow = int(m['escrow_micro'])
    lines.append(i18n.t(lang, 'market_liquidity_pool', amount=common._fmt(escrow)))
    if m['status'] == 'open':
        lines.append('\n' + i18n.t(lang, 'market_resolution_note'))
    return '\n'.join(lines)

async def _markets_text(tg_id: int | None=None) -> tuple[str, InlineKeyboardMarkup]:
    lang = await common.user_lang(tg_id) if tg_id else 'ru'
    markets = await common.ledger.open_markets(8)
    if not markets:
        return (i18n.t(lang, 'market_open'), InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'btn_mk_create'), callback_data='mkcreate')]]))
    lines = [i18n.t(lang, 'market_list_header'), '']
    kb_rows = []
    for m in markets:
        mid = int(m['id'])
        prices = await common.ledger.market_prices(mid) or []
        top = max(range(len(prices)), key=lambda i: prices[i]) if prices else 0
        leader = json.loads(m['options'])[top] if prices else '?'
        lines.append(f"#{mid} {m['question']}{i18n.t(lang, 'market_fav', leader=f'{leader[:30]} {_pct(prices[top])}')}" if prices else f"#{mid} {m['question']}")
        kb_rows.append([InlineKeyboardButton(text=f"📈 #{mid}: {m['question'][:36]}", callback_data=f'mk:{mid}')])
    lines.append(i18n.t(lang, 'market_list_hint'))
    kb_rows.append([InlineKeyboardButton(text=i18n.t(lang, 'btn_mk_create'), callback_data='mkcreate')])
    return ('\n'.join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))

@common.router.message(Command('markets'))
async def cmd_markets(message: types.Message) -> None:
    text, kb = await _markets_text(message.from_user.id)
    await message.answer(text, reply_markup=kb)

@common.router.callback_query(F.data == 'markets_amm')
@common.router.callback_query(F.data == 'mkcreate')
async def cb_market_list(cb: types.CallbackQuery) -> None:
    text, kb = await _markets_text(cb.from_user.id if cb.from_user else None)
    await common._edit_menu(cb, text, kb)
    await cb.answer()

@common.router.callback_query(F.data.startswith('mk:'))
async def cb_market_card(cb: types.CallbackQuery) -> None:
    lang = await common.user_lang(cb.from_user.id) if cb.from_user else 'ru'
    mid = int(cb.data.split(':', 1)[1])
    m = await common.ledger.get_market(mid)
    if not m:
        await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)
        return
    user = cb.from_user
    rows = []
    if m['status'] == 'open':
        rows.append([InlineKeyboardButton(text=i18n.t(lang, 'btn_buy'), callback_data=f'mkbuy:{mid}:0'), InlineKeyboardButton(text=i18n.t(lang, 'btn_sell'), callback_data=f'mksell:{mid}:0')])
        if user and int(m['creator']) == user.id:
            rows.append([InlineKeyboardButton(text=i18n.t(lang, 'btn_close_market'), callback_data=f'mkres:{mid}'), InlineKeyboardButton(text=i18n.t(lang, 'btn_cancel_action'), callback_data=f'mkcancel:{mid}')])
    rows.append([InlineKeyboardButton(text=i18n.t(lang, 'btn_all_markets_v2'), callback_data='markets_amm'), InlineKeyboardButton(text=i18n.t(lang, 'btn_bets'), callback_data='bets')])
    await common._edit_menu(cb, await _market_card(m, user.id if user else None), InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()

@common.router.callback_query(F.data.startswith('mkbuy:'))
async def cb_mk_buy(cb: types.CallbackQuery) -> None:
    lang = await common.user_lang(cb.from_user.id) if cb.from_user else 'ru'
    _, mid, opt = cb.data.split(':')
    m = await common.ledger.get_market(int(mid))
    if not m:
        await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)
        return
    options = json.loads(m['options'])
    idx = int(opt)
    label = options[idx] if idx < len(options) else '?'
    rows = [[InlineKeyboardButton(text=i18n.t(lang, 'btn_buy_amount', amount=a), callback_data=f'mkdo:{mid}:{idx}:{a}') for a in common.QUICK_AMOUNTS], [InlineKeyboardButton(text=i18n.t(lang, 'btn_back_short'), callback_data=f'mk:{mid}')]]
    await common._edit_menu(cb, i18n.t(lang, 'market_buy_card', mid=mid, label=label), InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()

@common.router.callback_query(F.data.startswith('mkdo:'))
async def cb_mk_do(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = await common.user_lang(user.id)
    _, mid, opt, amt = cb.data.split(':')
    try:
        spend = common._to_micro(Decimal(amt))
    except Exception:
        await cb.answer(i18n.t(lang, 'bad_amount'), show_alert=True)
        return
    wait = await common._throttle(user.id, 'market')
    if wait:
        await cb.answer(wait, show_alert=True)
        return
    status, info = await common.ledger.buy_shares(int(mid), user.id, int(opt), spend)
    if status != 'ok':
        msgs = {'closed': i18n.t(lang, 'market_closed'), 'deadline': i18n.t(lang, 'market_trade_deadline'), 'badopt': i18n.t(lang, 'market_trade_badopt'), 'balance': i18n.t(lang, 'market_balance'), 'toosmall': i18n.t(lang, 'market_trade_toosmall')}
        await cb.answer(msgs.get(status, i18n.t(lang, 'error_generic')), show_alert=True)
        return
    bal = await common.ledger.balance(user.id)
    bal_str = format(bal, '.4f').rstrip('0').rstrip('.')
    text = i18n.t(lang, 'market_buy_ok_detail', mid=mid, label=info['label'], shares=common._fmt(info['shares']), price=_pct(info['price']), cost=common._fmt(info['cost']), bal=bal_str)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'btn_market'), callback_data=f'mk:{mid}'), InlineKeyboardButton(text=i18n.t(lang, 'btn_sell'), callback_data=f'mksell:{mid}:{opt}')]])
    await common._edit_menu(cb, text, kb)
    await cb.answer()

@common.router.callback_query(F.data.startswith('mksell:'))
async def cb_mk_sell(cb: types.CallbackQuery) -> None:
    lang = await common.user_lang(cb.from_user.id) if cb.from_user else 'ru'
    _, mid, opt = cb.data.split(':')
    user = cb.from_user
    if not user:
        return
    pos = await common.ledger.user_market_position(int(mid), user.id)
    idx = int(opt)
    held = pos.get(idx, {}).get('shares', 0)
    if held <= 0:
        await cb.answer(i18n.t(lang, 'market_no_shares'), show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=i18n.t(lang, 'btn_sell_pct', pct=p), callback_data=f'mkselldo:{mid}:{idx}:{p}') for p in _SELL_PCTS], [InlineKeyboardButton(text=i18n.t(lang, 'btn_back_short'), callback_data=f'mk:{mid}')]]
    await common._edit_menu(cb, i18n.t(lang, 'market_sell_card', mid=mid, held=common._fmt(held)), InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()

@common.router.callback_query(F.data.startswith('mkselldo:'))
async def cb_mk_selldo(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = await common.user_lang(user.id)
    _, mid, opt, pct = cb.data.split(':')
    wait = await common._throttle(user.id, 'market')
    if wait:
        await cb.answer(wait, show_alert=True)
        return
    pos = await common.ledger.user_market_position(int(mid), user.id)
    idx = int(opt)
    held = pos.get(idx, {}).get('shares', 0)
    if held <= 0:
        await cb.answer(i18n.t(lang, 'market_no_shares_short'), show_alert=True)
        return
    shares = held * int(pct) // 100
    if shares <= 0:
        await cb.answer(i18n.t(lang, 'market_too_little'), show_alert=True)
        return
    status, info = await common.ledger.sell_shares(int(mid), user.id, idx, shares)
    if status != 'ok':
        await cb.answer(i18n.t(lang, 'market_sell_error'), show_alert=True)
        return
    text = i18n.t(lang, 'market_sell_ok_detail', mid=mid, label=info['label'], shares=common._fmt(info['shares']), price=_pct(info['price']), value=common._fmt(info['value']))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'btn_market'), callback_data=f'mk:{mid}')]])
    await common._edit_menu(cb, text, kb)
    await cb.answer()

@common.router.callback_query(F.data.startswith('mkres:'))
async def cb_mk_resolve(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = await common.user_lang(user.id)
    parts = cb.data.split(':')
    mid = int(parts[1])
    m = await common.ledger.get_market(mid)
    if not m:
        await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)
        return
    if m['status'] != 'open':
        await cb.answer(i18n.t(lang, 'market_closed'), show_alert=True)
        return
    if int(m['creator']) != user.id:
        await cb.answer(i18n.t(lang, 'market_only_creator'), show_alert=True)
        return
    if len(parts) == 2:
        options = json.loads(m['options'])
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f'🏆 {o[:30]}', callback_data=f'mkres:{mid}:{i}')] for i, o in enumerate(options)] + [[InlineKeyboardButton(text=i18n.t(lang, 'btn_back_short'), callback_data=f'mk:{mid}')]])
        await common._edit_menu(cb, i18n.t(lang, 'market_resolve_title', mid=mid, question=m['question']), kb)
        await cb.answer()
        return
    idx = int(parts[2])
    ok, msg, payouts = await common.ledger.resolve_market(mid, idx, user.id)
    if not ok:
        await cb.answer(msg, show_alert=True)
        return
    await _notify_market_result(cb.message, mid, payouts)
    new_m = await common.ledger.get_market(mid)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'btn_all_markets_v2'), callback_data='markets_amm')]])
    await common._edit_menu(cb, i18n.t(lang, 'market_closed_header', id=mid) + f'\n{msg}\n\n' + await _market_card(new_m, user.id), kb)
    await cb.answer()

@common.router.callback_query(F.data.startswith('mkcancel:'))
async def cb_mk_cancel(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    mid = int(cb.data.split(':', 1)[1])
    m = await common.ledger.get_market(mid)
    if not m:
        lang = await common.user_lang(user.id)
        await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)
        return
    ok, msg = await common.ledger.cancel_market(mid, user.id)
    if not ok:
        await cb.answer(msg, show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i18n.t(lang, 'btn_all_markets_v2'), callback_data='markets_amm')]])
    await common._edit_menu(cb, f'✅ {msg}', kb)
    await cb.answer()

@common.router.message(Command('trade'))
async def cmd_trade(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) != 4 or not common.BET_ID_RE.match(parts[1]) or (not common.AMOUNT_RE.match(parts[3])):
        await message.answer(i18n.t(lang, 'market_trade_format'))
        return
    try:
        mid, opt = (int(parts[1]), int(parts[2]) - 1)
        spend = common._to_micro(Decimal(parts[3]))
    except ValueError:
        await message.answer(i18n.t(lang, 'market_trade_format'))
        return
    if spend <= 0:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if spend > common._to_micro(common.config.MARKET_MAX_TRADE_USDC):
        await message.answer(i18n.t(lang, 'market_trade_max', n=f'{common.config.MARKET_MAX_TRADE_USDC:.0f}'))
        return
    wait = await common._throttle(message.from_user.id, 'market')
    if wait:
        await message.answer(wait)
        return
    status, info = await common.ledger.buy_shares(mid, message.from_user.id, opt, spend)
    if status == 'closed':
        await message.answer(i18n.t(lang, 'market_trade_closed'))
        return
    if status == 'deadline':
        await message.answer(i18n.t(lang, 'market_trade_deadline'))
        return
    if status == 'badopt':
        await message.answer(i18n.t(lang, 'market_trade_badopt'))
        return
    if status == 'balance':
        await message.answer(i18n.t(lang, 'market_balance'))
        return
    if status == 'toosmall':
        await message.answer(i18n.t(lang, 'market_trade_toosmall'))
        return
    bal = await common.ledger.balance(message.from_user.id)
    bal_str = format(bal, '.4f').rstrip('0').rstrip('.')
    await message.answer(i18n.t(lang, 'market_buy_ok_detail', mid=mid, label=info['label'], shares=common._fmt(info['shares']), price=_pct(info['price']), cost=common._fmt(info['cost']), bal=bal_str) + '\n\n' + i18n.t(lang, 'market_sell_hint', mid=mid, opt=opt + 1))

@common.router.message(Command('sell'))
async def cmd_sell(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) not in (3, 4) or not common.BET_ID_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'market_sell_format'))
        return
    mid, opt = (int(parts[1]), int(parts[2]) - 1)
    pct = 100
    if len(parts) == 4:
        raw = parts[3].rstrip('%')
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            await message.answer(i18n.t(lang, 'market_sell_pct_format'))
            return
        pct = int(raw)
    wait = await common._throttle(message.from_user.id, 'market')
    if wait:
        await message.answer(wait)
        return
    pos = await common.ledger.user_market_position(mid, message.from_user.id)
    held = pos.get(opt, {}).get('shares', 0)
    if held <= 0:
        await message.answer(i18n.t(lang, 'market_sell_no_shares'))
        return
    status, info = await common.ledger.sell_shares(mid, message.from_user.id, opt, held * pct // 100)
    if status != 'ok':
        await message.answer(i18n.t(lang, 'market_trade_closed'))
        return
    await message.answer(i18n.t(lang, 'market_sell_done', mid=mid, label=info['label'], shares=common._fmt(info['shares']), price=_pct(info['price']), value=common._fmt(info['value'])))

@common.router.message(Command('positions'))
async def cmd_positions(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    positions = await common.ledger.user_market_positions(message.from_user.id)
    if not positions:
        await message.answer(i18n.t(lang, 'market_positions_empty'))
        return
    lines = [i18n.t(lang, 'market_positions_header')]
    total_value = 0
    total_cost = 0
    for p in positions:
        value_micro = int(p['value'])
        total_value += value_micro
        total_cost += max(p['cost'], 0)
        pnl = value_micro - p['cost']
        sign = '+' if pnl >= 0 else '−'
        lines.append(f"📈 #{p['market_id']} <b>{p['question'][:60]}</b>\n" + i18n.t(lang, 'market_position_line', option=p['option'], shares=common._fmt(p['shares']), price=_pct(p['price']), value=common._fmt(value_micro), pnl=f'{sign}{common._fmt(abs(pnl))}'))
    lines.append(i18n.t(lang, 'market_total_value', value=common._fmt(total_value)))
    await message.answer('\n\n'.join(lines))

async def _notify_market_result(message: types.Message, mid: int, payouts: list[dict]) -> None:
    m = await common.ledger.get_market(mid)
    if not m:
        return
    options = json.loads(m['options'])
    winner_label = ''
    if m['winner'] is not None and int(m['winner']) < len(options):
        winner_label = options[int(m['winner'])]
    for p in payouts:
        try:
            lang = await common.user_lang(int(p['tg_id']))
        except Exception:
            lang = 'ru'
        if p['win']:
            line = i18n.t(lang, 'market_win', amount=common._fmt(p['net_micro']), mid=mid, question=m['question'], winner=winner_label)
        else:
            line = i18n.t(lang, 'market_lose', mid=mid, question=m['question'], winner=winner_label)
        try:
            await message.bot.send_message(int(p['tg_id']), line)
        except Exception:
            pass