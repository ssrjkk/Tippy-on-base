"""Stats / leaderboard / history handlers."""
from aiogram import types
from aiogram.filters import Command

from bot import i18n

from . import _common as common

__all__ = ['_history_text', '_stats_text', '_top_text', 'cmd_history', 'cmd_stats', 'cmd_top']

@common.router.message(Command('stats'))
async def cmd_stats(message: types.Message) -> None:
    await common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _stats_text(message.from_user.id))

async def _stats_text(tg_id: int) -> str:
    await common.ledger.ensure_user(tg_id, None)
    sent, received, won, lost = await common.ledger.user_stats(tg_id)
    creator_fees = await common.ledger.creator_fees(tg_id)
    bal = await common.ledger.balance(tg_id)
    lang = await common.user_lang(tg_id)
    fees_line = i18n.t(lang, 'markets_earning_line', amount=common._fmt(creator_fees)) if creator_fees else ''
    bal_str = f'{bal:.6f}'.rstrip('0').rstrip('.')
    return f"{i18n.t(lang, 'stats_your')}\n\n{i18n.t(lang, 'stats_sent', amount=common._fmt(sent))}\n{i18n.t(lang, 'stats_received', amount=common._fmt(received))}\n{i18n.t(lang, 'stats_won', amount=common._fmt(won))}\n{i18n.t(lang, 'stats_bet', amount=common._fmt(lost))}\n{i18n.t(lang, 'menu_balance', bal=bal_str)}" + fees_line

async def _top_text() -> str:
    rows = await common.ledger.top_tippers(10)
    if not rows:
        return i18n.t('ru', 'top_empty')
    lines = []
    for i, row in enumerate(rows, 1):
        uname = await common.ledger.username_of(row['tg_id']) or f"id{row['tg_id']}"
        medal = '🥇' if i == 1 else '🥈' if i == 2 else '🥉' if i == 3 else '▫️'
        lines.append(f"{medal} <b>@{uname}</b> — {common._fmt(row['total'])} USDC")
    return i18n.t('ru', 'top_title') + '\n\n' + '\n'.join(lines)

async def _history_text(tg_id: int, limit: int=15) -> str:
    rows = await common.ledger.history(tg_id, limit)
    if not rows:
        return i18n.t(await common.user_lang(tg_id), 'history_empty')
    lines = []
    lang = await common.user_lang(tg_id)
    for r in rows:
        emoji = common.KIND_EMOJI.get(r['kind'], '•')
        amt = common._fmt(r['amount'])
        if r['kind'] == 'tip':
            cid = int(r['counterparty']) if r['counterparty'].isdigit() else None
            cname = await common.ledger.username_of(cid) if cid else None
            who = f'@{cname}' if cname else r['counterparty'] or '?'
            lines.append(f'{emoji} {amt} → {who}')
        elif r['kind'] == 'deposit':
            lines.append(f"{emoji} +{amt} <code>{common._esc(r['counterparty'])}</code>")
        elif r['kind'] == 'withdraw':
            lines.append(f"{emoji} −{amt} → <code>{common._esc(r['counterparty'])}</code>")
        elif r['kind'] == 'bet':
            lines.append(f"{emoji} −{amt} #{r['counterparty']} ({r['note']})")
        elif r['kind'] == 'bet_win':
            lines.append(f"{emoji} +{amt} #{r['counterparty']}")
        elif r['kind'] == 'bet_cancel':
            lines.append(f"{emoji} +{amt} #{r['counterparty']}{i18n.t(lang, 'hist_bet_cancel')}")
        elif r['kind'] == 'fee':
            lines.append(f"{emoji} −{amt}{i18n.t(lang, 'hist_fee')}")
        elif r['kind'] == 'x402':
            sender = r['counterparty'] or '?'
            short = f'{sender[:10]}…{sender[-4:]}' if sender.startswith('0x') else sender
            lines.append(f"{emoji} +{amt} {i18n.t(lang, 'hist_agent')} <code>{common._esc(short)}</code>")
        elif r['kind'] == 'paywall':
            lines.append(f"{emoji} −{amt} #{r['counterparty']}{i18n.t(lang, 'hist_paywall')}")
        elif r['kind'] == 'paywall_earn':
            lines.append(f"{emoji} +{amt} #{r['counterparty']}{i18n.t(lang, 'hist_paywall_sell')}")
        elif r['kind'] == 'channel_pay':
            lines.append(f"{emoji} −{amt} #{r['counterparty']}{i18n.t(lang, 'hist_channel_sub')}")
        elif r['kind'] == 'channel_earn':
            lines.append(f"{emoji} +{amt} #{r['counterparty']}{i18n.t(lang, 'hist_channel_sell')}")
        else:
            lines.append(f"{emoji} {amt} ({r['kind']})")
    return i18n.t(lang, 'history_title') + '\n\n' + '\n'.join(lines)

@common.router.message(Command('top'))
async def cmd_top(message: types.Message) -> None:
    await message.answer(await _top_text())

@common.router.message(Command('history'))
async def cmd_history(message: types.Message) -> None:
    await common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.strip().split()
    limit = 15
    if len(parts) == 2 and parts[1].isdigit():
        limit = min(max(int(parts[1]), 1), 50)
    await message.answer(await _history_text(message.from_user.id, limit))
