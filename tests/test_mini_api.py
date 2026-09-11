"""Tests for the Mini App API endpoints (web/mini.py)."""

import pytest
from fastapi.testclient import TestClient

from web import server
from web.auth import COOKIE_NAME, make_session


def _auth(client, tg_id):
    """Attach a valid session cookie for tg_id."""
    client.cookies.set(COOKIE_NAME, make_session(tg_id))
    return client


@pytest.fixture()
def client(ledger, monkeypatch):
    from bot.ledger import AsyncLedger

    monkeypatch.setattr(server, "ledger", AsyncLedger(ledger))
    return TestClient(server.app)


# ── helpers ──────────────────────────────────────────────────────────

TG_USER = 1001
TG_OTHER = 1002


# ── tests ────────────────────────────────────────────────────────────

def test_mini_state_returns_balance(client, ledger):
    ledger.ensure_user(TG_USER, None)
    ledger.credit(TG_USER, 5_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.get("/api/mini/state")
    assert r.status_code == 200
    data = r.json()
    assert "balance_usdc" in data
    assert data["balance_usdc"] == 5.0
    assert data["tg_id"] == TG_USER


def test_mini_state_no_auth(client, ledger):
    ledger.ensure_user(TG_USER, None)

    r = client.get("/api/mini/state")
    assert r.status_code == 401


def test_mini_tip_self_tip_rejected(client, ledger):
    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/tip", json={"to": str(TG_USER), "amount": 1.0})
    assert r.status_code == 400
    assert "cannot tip yourself" in r.json()["detail"]


def test_mini_tip_unknown_user(client, ledger):
    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/tip", json={"to": "ghost_user_xyz", "amount": 1.0})
    assert r.status_code == 404


def test_mini_tip_success(client, ledger):
    ledger.ensure_user(TG_USER, "alice")
    ledger.ensure_user(TG_OTHER, "bob")
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/tip", json={"to": str(TG_OTHER), "amount": 2.5})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["new_balance"] == pytest.approx(7.5)


def test_mini_trade_invalid_market(client, ledger):
    ledger.ensure_user(TG_USER, None)
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/trade", json={
        "market_id": 99999, "option": 0, "amount": 1.0
    })
    assert r.status_code == 400


def test_mini_create_validation(client, ledger):
    ledger.ensure_user(TG_USER, None)
    ledger.credit(TG_USER, 100_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/create", json={
        "kind": "market",
        "question": "Hi",
        "options": ["Yes"],
        "hours": 24,
    })
    assert r.status_code == 400


def test_mini_lang_valid(client, ledger):
    ledger.ensure_user(TG_USER, None)
    _auth(client, TG_USER)

    r = client.post("/api/mini/lang", json={"lang": "en"})
    assert r.status_code == 200
    assert r.json()["lang"] == "en"


def test_mini_lang_invalid(client, ledger):
    ledger.ensure_user(TG_USER, None)
    _auth(client, TG_USER)

    r = client.post("/api/mini/lang", json={"lang": "xx"})
    assert r.status_code == 400
    assert "unsupported language" in r.json()["detail"]


def test_mini_tip_enforces_max_cap(client, ledger, monkeypatch):
    """The Mini App must enforce MAX_TIP_USDC like the Telegram handlers do —
    before it used to route straight to the ledger with no cap."""
    from bot import config

    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 10_000_000_000, "deposit")
    _auth(client, TG_USER)

    over = float(config.MAX_TIP_USDC) + 1
    r = client.post("/api/mini/tip", json={"to": str(TG_OTHER), "amount": over})
    assert r.status_code == 400
    assert "cap" in r.json()["detail"]


def test_mini_trade_enforces_max_cap(client, ledger, monkeypatch):
    from bot import config

    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 10_000_000_000, "deposit")
    _auth(client, TG_USER)

    over = float(config.MARKET_MAX_TRADE_USDC) + 1
    r = client.post("/api/mini/trade", json={
        "market_id": 99999, "option": 0, "amount": over,
    })
    assert r.status_code == 400
    assert "cap" in r.json()["detail"]


def test_mini_create_enforces_subsidy_caps(client, ledger, monkeypatch):
    """Markets must respect MARKET_MIN/MAX_SUBSIDY_USDC (previously any
    micro-positive subsidy passed, bypassing the Telegram handler's range)."""
    import web.mini
    from bot import config

    monkeypatch.setattr(web.mini.config, "MONEY_CMD_COOLDOWN_SECONDS", 0)
    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 100_000_000_000, "deposit")
    _auth(client, TG_USER)

    low = float(config.MARKET_MIN_SUBSIDY_USDC) / 100
    r = client.post("/api/mini/create", json={
        "kind": "market",
        "question": "Will it rain tomorrow?",
        "options": ["Yes", "No"],
        "hours": 24,
        "subsidy_usdc": low,
    })
    assert r.status_code == 400
    assert "minimum" in r.json()["detail"]

    high = float(config.MARKET_MAX_SUBSIDY_USDC) + 1
    r = client.post("/api/mini/create", json={
        "kind": "market",
        "question": "Will it rain tomorrow?",
        "options": ["Yes", "No"],
        "hours": 24,
        "subsidy_usdc": high,
    })
    assert r.status_code == 400
    assert "maximum" in r.json()["detail"]


def test_mini_money_throttle_blocks_rapid_repeat(client, ledger, monkeypatch):
    """Per-user money cooldown must apply to the Mini App (it previously only
    had the shared per-IP limiter). A second money action within the cooldown
    window returns 429, even though the first was a benign 404."""
    import web.mini

    monkeypatch.setattr(web.mini.config, "MONEY_CMD_COOLDOWN_SECONDS", 60)
    ledger.ensure_user(TG_USER, "alice")
    _auth(client, TG_USER)

    r1 = client.post("/api/mini/tip", json={"to": "ghost_user_xyz", "amount": 0.5})
    assert r1.status_code == 404  # passed the throttle, failed target resolution

    r2 = client.post("/api/mini/tip", json={"to": str(TG_OTHER), "amount": 0.5})
    assert r2.status_code == 429  # throttled by the per-user cooldown
