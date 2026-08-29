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
