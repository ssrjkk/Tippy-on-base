"""Telegram webhook endpoint tests (FastAPI TestClient, fake transport)."""

import asyncio

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods.base import TelegramType

from bot import config


@pytest.fixture()
def client(ledger, monkeypatch):
    from web import server

    monkeypatch.setattr(server, "ledger", ledger)
    return TestClient(server.app)


from fastapi.testclient import TestClient  # noqa: E402


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
        result = {
            "message_id": 9000,
            "date": 0,
            "chat": {"id": payload.get("chat_id") or 0, "type": "private"},
        }
        import json

        response = self.check_response(
            bot=bot,
            method=method,
            status_code=200,
            content=f'{{"ok": true, "result": {json.dumps(result, default=str)}}}',
        )
        return response.result


def _mk_bot(session) -> Bot:
    return Bot(token="0:test", session=session, default=ParseMode.HTML)


def _update(text: str) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1,
            "text": text,
            "from": {"id": 777, "is_bot": False, "first_name": "T"},
            "chat": {"id": 777, "type": "private"},
        },
    }


def _post(client, body: dict, secret: str | None = None):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}
    return client.post("/telegram-webhook", json=body, headers=headers)


def test_webhook_rejects_oversized_body(client):
    from web import hook

    big = _update("/start")
    big["update_id"] = 0
    r = client.post(
        "/telegram-webhook",
        content=b"x" * (hook.WEBHOOK_MAX_BODY + 1),
        headers={"X-Telegram-Bot-Api-Secret-Token": hook.webhook_secret()},
    )
    assert r.status_code == 413


def test_webhook_requires_secret(client):
    assert _post(client, _update("/balance")).status_code == 403
    assert _post(client, _update("/balance"), secret="wrong-secret").status_code == 403


def test_webhook_dispatches_update(client, monkeypatch):
    from web import hook

    session = RecorderSession()
    monkeypatch.setattr(hook, "bot", _mk_bot(session))
    r = _post(client, _update("/balance"), secret=hook.webhook_secret())
    assert r.status_code == 200
    sent = [c for c in session.calls if c[0] == "sendMessage"]
    assert sent and "Баланс" in sent[0][1]["text"]


def test_webhook_malformed_update_still_200(client, monkeypatch):
    import warnings

    from web import hook

    monkeypatch.setattr(hook, "bot", _mk_bot(RecorderSession()))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # aiogram warns about the unknown update type
        r = _post(client, {"update_id": 1}, secret=hook.webhook_secret())
    assert r.status_code == 200


def test_webhook_registers_with_telegram_on_start(monkeypatch):
    from bot import main
    from web import hook

    calls = []

    async def fake_set_webhook(url, secret_token, **kw):
        calls.append((url, secret_token))

    monkeypatch.setattr(config, "WEBHOOK_URL", "https://tipbot.example.com/tg")
    monkeypatch.setattr(hook.bot, "set_webhook", fake_set_webhook)
    stop = asyncio.Event()

    async def run():
        task = asyncio.create_task(main._run_webhook(stop))
        await asyncio.sleep(0.01)
        stop.set()
        await task

    asyncio.run(run())
    assert calls == [("https://tipbot.example.com/tg", hook.webhook_secret())]
