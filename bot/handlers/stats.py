"""Stats / leaderboard / history handlers."""

from aiogram import types
from aiogram.filters import Command

from . import _common as common

__all__ = [
    "_history_text",
    "_stats_text",
    "_top_text",
    "cmd_history",
    "cmd_stats",
    "cmd_top",
]


@common.router.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(await _stats_text(message.from_user.id))


async def _stats_text(tg_id: int) -> str:
    common.ledger.ensure_user(tg_id, None)
    sent, received, won, lost = common.ledger.user_stats(tg_id)
    creator_fees = common.ledger.creator_fees(tg_id)
    bal = common.ledger.balance(tg_id)
    fees_line = f"\n🧾 Заработано на рынках: <b>{common._fmt(creator_fees)} USDC</b>" if creator_fees else ""
    return (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"💸 Отправил чаевых: <b>{common._fmt(sent)} USDC</b>\n"
        f"💛 Получил чаевых: <b>{common._fmt(received)} USDC</b>\n"
        f"🏆 Выиграл ставками: <b>{common._fmt(won)} USDC</b>\n"
        f"🎲 Поставил в рынках: <b>{common._fmt(lost)} USDC</b>\n"
        f"💰 Баланс: <b>{bal:.6f}".rstrip("0").rstrip(".") + " USDC</b>"
        + fees_line
    )


async def _top_text() -> str:
    rows = common.ledger.top_tippers(10)
    if not rows:
        return "🏆 Пока никто не кидал чаевых. Будь первым!"
    lines = []
    for i, row in enumerate(rows, 1):
        uname = common.ledger.username_of(row["tg_id"]) or f"id{row['tg_id']}"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
        lines.append(f"{medal} <b>@{uname}</b> — {common._fmt(row['total'])} USDC")
    return "🏆 <b>Топ чаевых (все время)</b>\n\n" + "\n".join(lines)


async def _history_text(tg_id: int, limit: int = 15) -> str:
    rows = common.ledger.history(tg_id, limit)
    if not rows:
        return "🧾 Пока нет операций. Пополни: /deposit"
    lines = []
    for r in rows:
        emoji = common.KIND_EMOJI.get(r["kind"], "•")
        amt = common._fmt(r["amount"])
        if r["kind"] == "tip":
            cid = int(r["counterparty"]) if r["counterparty"].isdigit() else None
            cname = common.ledger.username_of(cid) if cid else None
            who = f"@{cname}" if cname else (r["counterparty"] or "?")
            lines.append(f"{emoji} {amt} → {who}")
        elif r["kind"] == "deposit":
            lines.append(f"{emoji} +{amt} <code>{common._esc(r['counterparty'])}</code>")
        elif r["kind"] == "withdraw":
            lines.append(f"{emoji} −{amt} → <code>{common._esc(r['counterparty'])}</code>")
        elif r["kind"] == "bet":
            lines.append(f"{emoji} −{amt} #{r['counterparty']} ({r['note']})")
        elif r["kind"] == "bet_win":
            lines.append(f"{emoji} +{amt} #{r['counterparty']}")
        elif r["kind"] == "bet_cancel":
            lines.append(f"{emoji} +{amt} #{r['counterparty']} (отмена)")
        elif r["kind"] == "fee":
            lines.append(f"{emoji} −{amt} (комиссия вывода)")
        elif r["kind"] == "x402":
            sender = r["counterparty"] or "?"
            short = f"{sender[:10]}…{sender[-4:]}" if sender.startswith("0x") else sender
            lines.append(f"{emoji} +{amt} от агента <code>{common._esc(short)}</code>")
        elif r["kind"] == "paywall":
            lines.append(f"{emoji} −{amt} #{r['counterparty']} (платный контент)")
        elif r["kind"] == "paywall_earn":
            lines.append(f"{emoji} +{amt} #{r['counterparty']} (продажа)")
        elif r["kind"] == "channel_pay":
            lines.append(f"{emoji} −{amt} #{r['counterparty']} (подписка на канал)")
        elif r["kind"] == "channel_earn":
            lines.append(f"{emoji} +{amt} #{r['counterparty']} (продажа доступа)")
        else:
            lines.append(f"{emoji} {amt} ({r['kind']})")
    return "🧾 <b>Последние операции</b>\n\n" + "\n".join(lines)


@common.router.message(Command("top"))
async def cmd_top(message: types.Message) -> None:
    await message.answer(await _top_text())


@common.router.message(Command("history"))
async def cmd_history(message: types.Message) -> None:
    common.ledger.ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.strip().split()
    limit = 15
    if len(parts) == 2 and parts[1].isdigit():
        limit = min(max(int(parts[1]), 1), 50)
    await message.answer(await _history_text(message.from_user.id, limit))
