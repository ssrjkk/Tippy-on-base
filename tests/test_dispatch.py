"""Dispatcher-level integration tests.

Feed REAL aiogram Update objects through the REAL router (filters, Command
parsing, callback routing) with a real Bot that only fakes the Telegram
transport. This verifies wiring that unit tests can't: filters match,
handlers are dispatched, and bot API calls carry the right payloads.
"""

import asyncio
from decimal import Decimal
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods.base import TelegramType
from aiogram.types import Update

from bot import handlers

ALICE, BOB = 2001, 2002


class RecorderSession(BaseSession):
    """Fake Telegram transport: records API calls, returns canned responses."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError

    async def make_request(
        self,
        bot: Bot,
        method: TelegramType,
        timeout: int | None = None,
    ) -> TelegramType:
        name = method.__api_method__
        try:
            payload = method.model_dump(mode="python", exclude_none=True)
        except Exception:
            payload = {}
        self.calls.append((name, payload))

        if name == "getMe":
            result = {
                "id": 1,
                "is_bot": True,
                "first_name": "BaseTipBot",
                "username": "base_tipbot",
                "can_join_groups": True,
                "can_read_all_group_messages": True,
                "supports_inline_queries": False,
            }
        elif name == "answerCallbackQuery":
            result = True
        else:
            chat = payload.get("chat_id") or 0
            result = {
                "message_id": 9000,
                "date": 0,
                "chat": {"id": chat, "type": "private"},
            }
        response = self.check_response(
            bot=bot,
            method=method,
            status_code=200,
            content=f'{{"ok": true, "result": {json_dumps(result)}}}',
        )
        return response.result


def json_dumps(v: Any) -> str:
    import json

    return json.dumps(v, default=str)


def _mk_bot(session) -> Bot:
    # Mirror the production construction (DefaultBotProperties), so the fake
    # transport exercises the same Bot object real polling uses.
    from aiogram.client.default import DefaultBotProperties

    return Bot(
        token="0:test",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


_dp: Dispatcher | None = None


def _mk_dp() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        _dp.include_router(handlers.router)
    return _dp


def _message_update(
    text: str,
    chat_id: int = -100,
    user_id: int = ALICE,
    username: str = "alice",
    message_id: int = 10,
    reply_to: dict | None = None,
) -> Update:
    d = {
        "update_id": 1,
        "message": {
            "message_id": message_id,
            "date": 0,
            "chat": {"id": chat_id, "type": "private", "username": username},
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "F",
                "username": username,
            },
            "text": text,
        },
    }
    if reply_to:
        d["message"]["reply_to_message"] = reply_to
    return Update.model_validate(d)


def _callback_update(data: str, user_id: int = ALICE, username: str = "alice") -> Update:
    d = {
        "update_id": 2,
        "callback_query": {
            "id": f"cq{data}",
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "F",
                "username": username,
            },
            "chat_instance": "-1000",
            "data": data,
            "message": {
                "message_id": 55,
                "date": 0,
                "chat": {"id": -100, "type": "private", "username": username},
                "from": {"id": 0, "is_bot": True, "first_name": "b"},
                "text": "menu",
            },
        },
    }
    return Update.model_validate(d)


def _reaction_update(
    chat_id: int, message_id: int, user_id: int, username: str, emoji: str
) -> Update:
    d = {
        "update_id": 3,
        "message_reaction": {
            "chat": {"id": chat_id, "type": "group", "title": "t"},
            "message_id": message_id,
            "user": {
                "id": user_id,
                "is_bot": False,
                "first_name": "R",
                "username": username,
            },
            "date": 0,
            "old_reaction": [],
            "new_reaction": [{"type": "emoji", "emoji": emoji}],
        },
    }
    return Update.model_validate(d)


def test_dispatch_start_shows_menu(ledger):
    s = RecorderSession()
    asyncio.run(_mk_dp().feed_update(_mk_bot(s), _message_update("/start")))
    assert ledger.user_exists(ALICE)
    assert "sendMessage" in [n for n, _ in s.calls]
    text = next(p["text"] for n, p in s.calls if n == "sendMessage")
    assert "Tippy" in text


def test_dispatch_balance(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    s = RecorderSession()
    asyncio.run(_mk_dp().feed_update(_mk_bot(s), _message_update("/balance")))
    text = next(p["text"] for n, p in s.calls if n == "sendMessage")
    assert "Баланс" in text and "10" in text


def test_dispatch_tip_flow(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    s = RecorderSession()
    asyncio.run(
        _mk_dp().feed_update(
            _mk_bot(s), _message_update("/tip 5 @bob", user_id=ALICE, username="alice")
        )
    )
    assert ledger.balance(ALICE) == Decimal("5.000000")
    assert ledger.balance(BOB) == Decimal("5.000000")
    sent_to = [p["chat_id"] for n, p in s.calls if n == "sendMessage"]
    assert BOB in sent_to  # recipient notified


def test_dispatch_menu_callback_edit(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    s = RecorderSession()
    asyncio.run(_mk_dp().feed_update(_mk_bot(s), _callback_update("bal")))
    edits = [p for n, p in s.calls if n == "editMessageText"]
    assert edits, "callback must edit the menu message"
    assert "Баланс" in edits[0]["text"]
    assert "answerCallbackQuery" in [n for n, _ in s.calls]


def test_dispatch_bets_with_keyboard(ledger):
    ledger.create_bet(ALICE, "Кто победит?", ["А", "Б"])
    s = RecorderSession()
    asyncio.run(_mk_dp().feed_update(_mk_bot(s), _message_update("/bets")))
    sends = [p for n, p in s.calls if n == "sendMessage"]
    assert sends and "reply_markup" in sends[-1]


def test_dispatch_reaction_tip(ledger):
    ledger.credit(BOB, 10_000_000, "deposit")
    s = RecorderSession()
    bot = _mk_bot(s)
    dp = _mk_dp()
    asyncio.run(dp.feed_update(bot, _message_update("hello", message_id=7)))
    asyncio.run(
        dp.feed_update(bot, _reaction_update(-100, 7, BOB, "bob", "🔥"))
    )
    assert ledger.balance(ALICE) == Decimal("1.000000")
    assert ledger.balance(BOB) == Decimal("9.000000")
    sent_to = [p["chat_id"] for n, p in s.calls if n == "sendMessage"]
    assert ALICE in sent_to  # author notified


def test_dispatch_donate_landing_with_qr(ledger, monkeypatch):
    ledger.ensure_user(BOB, "bob")
    monkeypatch.setattr(
        handlers.qrlib, "qr_bytes", lambda *a, **k: b"\x89PNG\r\n\x1a\n"
    )
    s = RecorderSession()
    asyncio.run(
        _mk_dp().feed_update(
            _mk_bot(s),
            _message_update("/start donate_2002", user_id=ALICE, username="alice"),
        )
    )
    assert "sendPhoto" in [n for n, _ in s.calls]


def test_dispatch_market_full_lifecycle(ledger):
    """AMM prediction market end-to-end through the REAL router: create ->
    trade -> card -> two-tap resolve -> winner DM. Only Telegram transport
    is faked; filters, parsing, ledger math are all real."""
    ledger.credit(ALICE, 1_000_000_000, "deposit")
    s = RecorderSession()
    bot = _mk_bot(s)
    dp = _mk_dp()

    asyncio.run(
        dp.feed_update(
            bot,
            _message_update("/market create 50 Кто победит? | Алиса | Боб", user_id=ALICE),
        )
    )
    markets = ledger.open_markets()
    assert len(markets) == 1
    mid = int(markets[0]["id"])
    assert ledger.balance(ALICE) == Decimal("950.000000")

    ledger.credit(BOB, 100_000_000, "deposit")
    asyncio.run(
        dp.feed_update(bot, _message_update(f"/trade {mid} 1 10", user_id=BOB, username="bob"))
    )
    pos = ledger.user_market_position(mid, BOB)
    assert pos.get(0, {}).get("shares", 0) > 0

    asyncio.run(dp.feed_update(bot, _callback_update(f"mk:{mid}", user_id=BOB)))
    edits = [p for n, p in s.calls if n == "editMessageText"]
    assert edits and "%" in edits[-1]["text"], "card must show live odds"

    asyncio.run(dp.feed_update(bot, _callback_update(f"mkres:{mid}", user_id=ALICE)))
    asyncio.run(dp.feed_update(bot, _callback_update(f"mkres:{mid}:0", user_id=ALICE)))
    m = ledger.get_market(mid)
    assert m["status"] == "resolved"
    assert m["winner"] == 0
    sent_to = [p["chat_id"] for n, p in s.calls if n == "sendMessage"]
    assert BOB in sent_to, "winner must be notified"


def test_dispatch_ask_disabled_hint(ledger, monkeypatch):
    from bot import ai as ai_mod

    monkeypatch.setattr(ai_mod.config, "AI_API_KEY", None)
    s = RecorderSession()
    asyncio.run(_mk_dp().feed_update(_mk_bot(s), _message_update("/ask что такое base?")))
    text = next(p["text"] for n, p in s.calls if n == "sendMessage")
    assert "не подключён" in text


def test_dispatch_tx_lookup(ledger, monkeypatch):
    from bot import base

    h = "0x" + "ab" * 32
    monkeypatch.setattr(
        base,
        "tx_info",
        lambda t: {
            "hash": t,
            "from": "0x" + "1" * 40,
            "to": "0x" + "2" * 40,
            "status": True,
            "value_micro": 5_000_000,
            "usdc_to": "0x" + "3" * 40,
        },
    )
    s = RecorderSession()
    asyncio.run(_mk_dp().feed_update(_mk_bot(s), _message_update(f"/tx {h}")))
    text = next(p["text"] for n, p in s.calls if n == "sendMessage")
    assert "USDC перевод" in text and "Basescan" in text
