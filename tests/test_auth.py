"""Web login tests: Telegram widget HMAC + wallet signature + sessions.

No mocks of crypto: Telegram params are signed with the real algorithm
(HMAC-SHA256 keyed by SHA256(bot token)), wallet logins use a real
eth_account keypair and real EIP-191 recovery.
"""

import hashlib
import hmac
import os
import time

import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from web import auth
from web.server import app


@pytest.fixture()
def client(ledger):
    return TestClient(app)


def tg_signed(tg_id: int, username: str = "alice", auth_date: int | None = None) -> dict:
    """Build a Telegram Login Widget payload with the REAL algorithm."""
    params = {
        "id": str(tg_id),
        "first_name": "Alice",
        "username": username,
        "auth_date": str(auth_date or int(time.time())),
        "photo_url": "https://t.me/i/userpic/320/alice.jpg",
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hashlib.sha256(os.environ["BOT_TOKEN"].encode()).digest()
    params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return params


# ---------- session tokens ----------


def test_session_roundtrip():
    tok = auth.make_session(4242)
    assert auth.parse_session(tok) == 4242


def test_session_tamper_rejected():
    tok = auth.make_session(4242)
    payload, sig = tok.rsplit(".", 1)
    assert auth.parse_session(f"{payload}{'A' if sig[0] != 'A' else 'B'}{sig[1:]}") is None
    assert auth.parse_session(tok + "x") is None
    assert auth.parse_session(None) is None
    assert auth.parse_session("") is None


def test_session_expiry():
    tok = auth.make_session(7, ttl=-1)
    assert auth.parse_session(tok) is None


# ---------- telegram login ----------


def test_telegram_login_creates_user_and_cookie(client, ledger):
    r = client.get(
        "/api/auth/telegram",
        params=tg_signed(3001),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/me"
    cookie = r.cookies.get(auth.COOKIE_NAME)
    assert cookie and auth.parse_session(cookie) == 3001
    assert ledger.user_exists(3001)


def test_telegram_login_bad_hash(client):
    params = tg_signed(3002)
    params["hash"] = "0" * 64
    r = client.get("/api/auth/telegram", params=params, follow_redirects=False)
    assert r.status_code == 403


def test_telegram_login_stale_auth_date(client):
    old = int(time.time()) - 3 * 24 * 3600
    r = client.get(
        "/api/auth/telegram",
        params=tg_signed(3003, auth_date=old),
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_login_page_renders_widget(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "data-telegram-login" in r.text
    assert "Connect Wallet" in r.text


# ---------- wallet login ----------


def _wallet_message(address: str) -> str:
    nonce = hashlib.sha256(address.encode()).hexdigest()[:32]
    return (
        f"Tippy login\nAddress: {address}\nNonce: {nonce}\n"
        f"Expires: {int(time.time()) + 300}"
    )


def test_wallet_login_linked_account(client, ledger):
    acct = Account.from_key("0x" + "22" * 32)
    address = acct.address
    # Link the wallet through the REAL bot flow (nonce -> confirm).
    nonce = ledger.new_link_nonce(3101, address)
    assert ledger.confirm_link(3101, address, nonce)

    msg = _wallet_message(address)
    sig = acct.sign_message(
        __import__("eth_account.messages", fromlist=["encode_defunct"]).encode_defunct(text=msg)
    ).signature.to_0x_hex()

    r = client.post(
        "/api/auth/wallet",
        json={"address": address, "message": msg, "signature": sig},
    )
    assert r.status_code == 200, r.text
    assert auth.parse_session(r.cookies.get(auth.COOKIE_NAME)) == 3101


def test_wallet_login_unlinked_wallet(client):
    acct = Account.from_key("0x" + "33" * 32)
    msg = _wallet_message(acct.address)
    sig = acct.sign_message(
        __import__("eth_account.messages", fromlist=["encode_defunct"]).encode_defunct(text=msg)
    ).signature.to_0x_hex()
    r = client.post(
        "/api/auth/wallet",
        json={"address": acct.address, "message": msg, "signature": sig},
    )
    assert r.status_code == 404
    assert "not linked" in r.json()["detail"]


def test_wallet_login_wrong_signer(client, ledger):
    acct = Account.from_key("0x" + "44" * 32)
    impostor = Account.from_key("0x" + "45" * 32)
    nonce = ledger.new_link_nonce(3102, acct.address)
    ledger.confirm_link(3102, acct.address, nonce)

    msg = _wallet_message(acct.address)
    bad_sig = impostor.sign_message(
        __import__("eth_account.messages", fromlist=["encode_defunct"]).encode_defunct(text=msg)
    ).signature.to_0x_hex()
    r = client.post(
        "/api/auth/wallet",
        json={"address": acct.address, "message": msg, "signature": bad_sig},
    )
    assert r.status_code == 403


def test_wallet_login_expired_message(client, ledger):
    acct = Account.from_key("0x" + "55" * 32)
    nonce = ledger.new_link_nonce(3103, acct.address)
    ledger.confirm_link(3103, acct.address, nonce)

    msg = (
        f"Tippy login\nAddress: {acct.address}\nNonce: deadbeef\n"
        f"Expires: {int(time.time()) - 10}"
    )
    sig = acct.sign_message(
        __import__("eth_account.messages", fromlist=["encode_defunct"]).encode_defunct(text=msg)
    ).signature.to_0x_hex()
    r = client.post(
        "/api/auth/wallet",
        json={"address": acct.address, "message": msg, "signature": sig},
    )
    assert r.status_code == 403


# ---------- /api/me ----------


def test_me_requires_auth(client):
    assert client.get("/api/me").status_code == 401


def test_me_returns_positions_and_balance(client, ledger):
    ledger.credit(3201, 50_000_000, "deposit")
    market_id = ledger.create_market(3201, "Q?", ["Yes", "No"], 10_000_000)
    ledger.buy_shares(market_id, 3201, 0, 5_000_000)

    client.cookies.set(auth.COOKIE_NAME, auth.make_session(3201))
    r = client.get("/api/me")
    assert r.status_code == 200
    me = r.json()
    assert abs(me["balance_usdc"] - 35.0) < 1e-6  # 50 - 10 subsidy - 5 trade
    assert len(me["positions"]) == 1
    pos = me["positions"][0]
    assert pos["market_id"] == market_id
    assert pos["option"] == "Yes"
    assert pos["shares"] > 0
    assert me["bot_link"].startswith("https://t.me/")


def test_me_unknown_user_rejected(client):
    client.cookies.set(auth.COOKIE_NAME, auth.make_session(999999))
    assert client.get("/api/me").status_code == 401
