"""Operator visibility for the autonomous agent (/agent, admin-only).

Shows live caps state (spend, actions, circuit breaker), the agent's recent
on-chain attest trail tail and the last audit entry — everything an operator
needs to see what the agent did while they were away, without opening the
web dashboard (/api/agent/status shows the same data for the owner).

Fail-safe by design: when the agent is disabled or its state files are
absent, the command says so instead of crashing.
"""
import json
import os

from aiogram import types
from aiogram.filters import Command

from bot import i18n

from . import _common as common


def _audit_tail(limit: int = 3) -> list[dict]:
    """Last N audit entries (newest last in file, so read all and slice)."""
    from agent.config import STATE_DIR

    path = os.path.join(STATE_DIR, "agent_audit.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        return [json.loads(ln) for ln in lines[-limit:]]
    except (OSError, ValueError):
        return []


@common.router.message(Command('agent'))
async def cmd_agent(message: types.Message) -> None:
    if common.config.ADMIN_TG_ID is None or message.from_user.id != common.config.ADMIN_TG_ID:
        await message.answer(i18n.t(await common.user_lang(message.from_user.id), 'agent_admin_only'))
        return

    try:
        from agent import caps, config as agent_config
    except Exception:
        await message.answer(i18n.t(await common.user_lang(message.from_user.id), 'agent_admin_only'))
        return

    lang = await common.user_lang(message.from_user.id)

    if agent_config.AGENT_TG_ID <= 0:
        await message.answer(i18n.t(lang, 'agent_disabled'))
        return

    try:
        s = caps.get_status()
    except Exception as e:
        await message.answer(i18n.t(lang, 'agent_status_error', err=common._esc(str(e)[:120])))
        return
    lines = [
        i18n.t(lang, 'agent_header', tg_id=agent_config.AGENT_TG_ID),
        i18n.t(lang, 'agent_spend', spent=f"{s['daily_spent_usdc']:.2f}", cap=f"{s['daily_cap_usdc']:.2f}"),
        i18n.t(lang, 'agent_actions', done=s['actions_this_hour'], cap=s['max_actions_per_hour']),
        i18n.t(lang, 'agent_errors', n=s['consecutive_errors'], cb='🔴' if s['cooldown_active'] else '🟢'),
        i18n.t(lang, 'agent_caps_line', daily=f"{agent_config.DAILY_SPEND_CAP_USDC:.0f}", per_tx=f"{agent_config.PER_TX_CAP_USDC:.0f}"),
    ]

    entries = _audit_tail()
    if entries:
        lines.append(i18n.t(lang, 'agent_recent'))
        for e in reversed(entries):  # newest first
            lines.append(f"• #{e['market_id']}: {common._esc(e['question'][:60])} — ${e['bet_amount_usdc']:.2f} ({int(e['confidence'] * 100)}%)")
    else:
        lines.append(i18n.t(lang, 'agent_no_audit'))

    await message.answer('\n'.join(lines))
