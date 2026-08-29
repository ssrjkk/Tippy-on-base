"""Web dashboard API tests (FastAPI TestClient)."""

import types

import pytest
from fastapi.testclient import TestClient

from web.auth import COOKIE_NAME, make_session


def _auth(client, tg_id):
    """Attach a valid owner session cookie for tg_id to the TestClient."""
    client.cookies.set(COOKIE_NAME, make_session(tg_id))
    return client


@pytest.fixture()
def client(ledger, monkeypatch):
    from bot.ledger import AsyncLedger
    from web import server

    # Routes now `await ledger.x()`; wrap the injected sync ledger so the
    # await resolves against the hermetic test database.
    monkeypatch.setattr(server, "ledger", AsyncLedger(ledger))
    return TestClient(server.app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """The web rate limiter keeps a module-global hit counter (_rl_state).
    Without a reset it accumulates across the whole suite and 429s later
    tests once the per-IP window fills. Clear it around every test."""
    from web import server

    server._rl_state.clear()
    yield
    server._rl_state.clear()


def test_info(client):
    r = client.get("/api/info")
    assert r.status_code == 200
    assert "bot_username" in r.json()


def test_stats_zeros(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["users"] == 0
    assert data["volume_usdc"] == 0.0
    assert data["open_markets"] == 0
    assert data["fees_usdc"] == 0.0


def test_stats_tracks_fees(client, ledger):
    ledger.credit(1, 1_000_000, "deposit")
    ledger.record_withdraw_fee(1, "0x" + "a" * 40, 10_000, "0x" + "1" * 64)
    data = client.get("/api/stats").json()
    assert data["fees_usdc"] == 0.01


def test_wallet_shape(client, monkeypatch):
    from web import server

    async def _raise():
        raise ConnectionError("no rpc")

    monkeypatch.setattr(server, "hot_balance", _raise)
    r = client.get("/api/wallet")
    assert r.status_code == 200
    data = r.json()
    assert data["address"].startswith("0x")
    assert data["balance_usdc"] is None  # RPC unavailable in tests


def test_unknown_user_404(client):
    assert client.get("/api/user/42424242").status_code == 404


def test_user_endpoint_with_data(client, ledger):
    ledger.credit(777, 5_000_000, "deposit")
    ledger.transfer(777, 778, 2_000_000)
    _auth(client, 777)
    r = client.get("/api/user/777")
    assert r.status_code == 200
    data = r.json()
    assert data["is_owner"] is True
    assert data["balance_usdc"] == 3.0
    assert data["tips_sent_usdc"] == 2.0
    assert data["tips_received_usdc"] == 0.0


def test_user_endpoint_public_view_for_stranger(client, ledger):
    ledger.credit(777, 5_000_000, "deposit")
    ledger.transfer(777, 778, 2_000_000)
    r = client.get("/api/user/777")
    assert r.status_code == 200
    data = r.json()
    assert data["is_owner"] is False
    # No live balance, positions, history, or full financial profile for a
    # stranger: only public aggregates.
    assert "balance_usdc" not in data
    assert "history" not in data
    assert "positions" not in data


def test_market_and_leaderboard(client, ledger):
    ledger.credit(1, 100_000_000, "deposit")
    ledger.credit(2, 100_000_000, "deposit")
    bid = ledger.create_bet(1, "Сыграем?", ["Да", "Нет"])
    ledger.place_bet(bid, 2, 0, 10_000_000)
    ledger.transfer(2, 1, 5_000_000)  # tip: user 2 is a tipper

    r = client.get("/api/markets")
    assert r.status_code == 200
    markets = r.json()
    assert len(markets) == 1
    assert markets[0]["pot_usdc"] == 10.0

    r = client.get(f"/api/market/{bid}")
    assert r.status_code == 200
    assert r.json()["options"][0]["pool_usdc"] == 10.0

    assert client.get("/api/market/99999").status_code == 404

    lb = client.get("/api/leaderboard").json()
    assert lb[0]["total_usdc"] == 5.0  # user 2 tipped 5 USDC


def test_unknown_market_404(client):
    assert client.get("/api/market/99999").status_code == 404


def test_health(client, monkeypatch):
    from web import server

    class FakeEth:
        def __init__(self):
            self.block_number = 1000

    monkeypatch.setattr(server.base, "w3", types.SimpleNamespace(eth=FakeEth()))
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["hot_wallet"].startswith("0x")


def test_health_includes_scanner_status(client, monkeypatch):
    from web import server

    class FakeEth:
        def __init__(self):
            self.block_number = 1000

    monkeypatch.setattr(server.base, "w3", types.SimpleNamespace(eth=FakeEth()))
    data = client.get("/api/health").json()
    assert "chain_head" in data
    assert "last_scanned_block" in data
    assert "deposit_lag" in data


def test_health_deposit_lag_none_when_rpc_down(client, monkeypatch):
    from web import server

    class Boom:
        @property
        def block_number(self):
            raise ConnectionError("no rpc")

    monkeypatch.setattr(server.base, "w3", types.SimpleNamespace(eth=Boom()))
    data = client.get("/api/health").json()
    assert data["chain_head"] is None
    assert data["deposit_lag"] is None
    assert data["status"] == "ok"


def test_solvency_zeros(client):
    r = client.get("/api/solvency")
    assert r.status_code == 200
    data = r.json()
    assert data["liabilities_usdc"] == 0.0
    assert data["pending_deposits_usdc"] == 0.0
    assert data["owed_usdc"] == 0.0
    assert data["vault_address"] is None
    assert data["vault_balance_usdc"] is None
    assert data["reserves_source"] == "hot_wallet"


def test_solvency_tracks_liabilities(client, ledger):
    ledger.credit(777, 12_000_000, "deposit")
    ledger.credit(778, 3_000_000, "deposit")
    ledger.record_pending("0x" + "5" * 64, "0xowner", 7_000_000)
    r = client.get("/api/solvency").json()
    assert r["liabilities_usdc"] == 15.0
    assert r["pending_deposits_usdc"] == 7.0
    assert r["owed_usdc"] == 22.0


def test_solvency_insolvent_when_rpc_down(client, monkeypatch):
    from web import server

    async def _raise():
        raise ConnectionError("no rpc")

    monkeypatch.setattr(server, "hot_balance", _raise)
    monkeypatch.setattr(server, "vault_balance", _raise)
    r = client.get("/api/solvency").json()
    assert r["solvent"] is None
    assert r["reserve_usdc"] is None


def test_solvency_uses_vault_as_primary_reserve(client, ledger, monkeypatch):
    from web import server

    vault_addr = "0x" + "ab" * 20
    monkeypatch.setattr(server.config, "VAULT_ADDRESS", vault_addr)
    async def _vb():
        return 25.5
    monkeypatch.setattr(server, "vault_balance", _vb)
    ledger.credit(777, 12_000_000, "deposit")
    r = client.get("/api/solvency").json()
    assert r["vault_address"] == vault_addr
    assert r["vault_balance_usdc"] == 25.5
    assert r["reserves_source"] == "vault"
    assert r["reserve_usdc"] == 13.5
    assert r["solvent"] is True


def test_solvency_vault_rpc_down_keeps_hot_wallet_source(client, monkeypatch):
    from web import server

    monkeypatch.setattr(server.config, "VAULT_ADDRESS", "0x" + "cd" * 20)

    async def _raise():
        raise ConnectionError("no rpc")

    monkeypatch.setattr(server, "vault_balance", _raise)
    r = client.get("/api/solvency").json()
    assert r["vault_balance_usdc"] is None
    assert r["reserve_usdc"] is None
    assert r["solvent"] is None


# ---------- x402 (agent tips over HTTP) ----------


def test_x402_first_call_returns_invoice(client, ledger, monkeypatch):

    ledger.credit(777, 1_000_000, "deposit")
    r = client.post("/api/x402/tip?recipient=777&amount=5")
    assert r.status_code == 402
    body = r.json()
    assert body["detail"] == "payment required"
    assert body["amount_usdc"] == 5.0
    assert body["pay_to"].startswith("0x")
    for header in ("x-402-recipient", "x-402-amount", "x-402-expires-at", "x-402-idempotency-key"):
        assert header in r.headers
    assert r.headers["x-402-amount"] == "5000000"


def test_x402_rejects_bad_recipient_and_amount(client, ledger):
    assert client.post("/api/x402/tip?recipient=999999&amount=5").status_code == 404
    ledger.credit(777, 1_000_000, "deposit")
    assert client.post("/api/x402/tip?recipient=777&amount=0").status_code == 400
    assert client.post("/api/x402/tip?recipient=777&amount=-1").status_code == 400
    assert client.post("/api/x402/tip?recipient=777&amount=99999").status_code == 400
    assert client.post("/api/x402/tip").status_code == 400


def test_x402_payment_credited_once(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    tx = "0x" + "ab" * 32
    monkeypatch.setattr(
        x402, "_verify_payment", lambda h, amt: {"sender": "0x" + "11" * 20, "amount_micro": amt}
    )
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": tx}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tip"]["amount_usdc"] == 5.0
    assert body["tip"]["tx_hash"] == tx
    assert ledger.user_view(777)["balance_micro"] == 6_000_000

    # replay of the same tx is refused before any RPC call
    r2 = client.post("/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": tx})
    assert r2.status_code == 409
    assert ledger.user_view(777)["balance_micro"] == 6_000_000
    assert ledger.x402_paid(tx)


def test_x402_payment_by_username(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    ledger.ensure_user(777, "alice")
    monkeypatch.setattr(
        x402, "_verify_payment", lambda h, amt: {"sender": "0x" + "11" * 20, "amount_micro": amt}
    )
    r = client.post(
        "/api/x402/tip?recipient=alice&amount=2", headers={"x-402-payment": "0x" + "cd" * 32}
    )
    assert r.status_code == 200
    assert ledger.user_view(777)["balance_micro"] == 3_000_000


def test_x402_unverified_payment_gets_invoice_again(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    monkeypatch.setattr(x402, "_verify_payment", lambda h, amt: None)
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": "0x" + "ef" * 32}
    )
    assert r.status_code == 402
    assert r.headers["x-402-amount"] == "5000000"
    assert ledger.user_view(777)["balance_micro"] == 1_000_000


def test_x402_rpc_down_returns_402(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")

    def _raise():
        raise ConnectionError("no rpc")

    monkeypatch.setattr(x402.base.w3.eth, "get_transaction_receipt", _raise)
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": "0x" + "aa" * 32}
    )
    assert r.status_code == 402
    assert ledger.user_view(777)["balance_micro"] == 1_000_000


def _transfer_log(to, value_micro, from_addr=None):
    from web import x402

    def pad(a):
        return "0x" + "0" * 24 + a[2:].lower()

    return {
        "address": x402.config.USDC_ADDRESS,
        "topics": [
            x402.base.w3.keccak(text="Transfer(address,address,uint256)").hex(),
            pad("0x" + (from_addr or "11" * 20)),
            pad(to),
        ],
        "data": "0x" + f"{int(value_micro):064x}",
        "transactionHash": b"\x00" * 32,
        "blockHash": b"\x00" * 32,
        "blockNumber": 1,
        "logIndex": 0,
        "transactionIndex": 0,
    }


def test_x402_verifies_real_transfer_logs(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    receive = x402._x402_receive_address() or str(x402.hot_wallet())
    pay_to = receive[2:].lower()
    logs = [
        _transfer_log("0x" + "99" * 20, 9_999_999),  # unrelated transfer — ignored
        _transfer_log("0x" + pay_to, 5_000_000, from_addr="33" * 20),  # the payment
    ]
    monkeypatch.setattr(
        x402.base.w3.eth, "get_transaction_receipt", lambda h: {"status": 1, "logs": logs}
    )
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": "0x" + "aa" * 32}
    )
    assert r.status_code == 200
    assert r.json()["tip"]["sender"] == "0x" + "33" * 20
    assert ledger.user_view(777)["balance_micro"] == 6_000_000


def test_x402_reverted_tx_not_credited(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    monkeypatch.setattr(
        x402.base.w3.eth,
        "get_transaction_receipt",
        lambda h: {"status": 0, "logs": []},
    )
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": "0x" + "bb" * 32}
    )
    assert r.status_code == 402
    assert ledger.user_view(777)["balance_micro"] == 1_000_000


def test_x402_paywall_invoice(client, ledger):
    item_id = ledger.create_paywall(777, "Мой отчёт", 5_000_000, "секретный контент")
    r = client.post(f"/api/x402/paywall?item={item_id}&amount=5")
    assert r.status_code == 402
    body = r.json()
    assert body["detail"] == "payment required"
    assert body["amount_usdc"] == 5.0
    assert body["item"] == str(item_id)
    assert r.headers["x-402-amount"] == "5000000"
    # unknown item -> 404, missing params -> 400
    assert client.post("/api/x402/paywall?item=999&amount=5").status_code == 404
    assert client.post("/api/x402/paywall?item=1").status_code == 400


def test_x402_paywall_purchase_returns_content(client, ledger, monkeypatch):
    from web import x402

    item_id = ledger.create_paywall(777, "Мой отчёт", 5_000_000, "секретный контент")
    tx = "0x" + "cc" * 32
    monkeypatch.setattr(
        x402, "_verify_payment", lambda h, amt: {"sender": "0x" + "22" * 20, "amount_micro": amt}
    )
    r = client.post(
        f"/api/x402/paywall?item={item_id}&amount=5", headers={"x-402-payment": tx}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["item"]["title"] == "Мой отчёт"
    assert body["content"] == "секретный контент"
    # owner is credited on-chain
    assert ledger.user_view(777)["balance_micro"] == 5_000_000
    assert ledger.x402_paid(tx)


def test_x402_paywall_lowball_reinvoiced_at_price(client, ledger):
    """An agent cannot buy the content cheaper than the listed price: a lowball
    amount gets a fresh invoice at the real price, and paying the real price
    is what actually unlocks the content."""
    item_id = ledger.create_paywall(777, "Мой отчёт", 5_000_000, "секретный контент")
    lowball = client.post(f"/api/x402/paywall?item={item_id}&amount=0.01")
    assert lowball.status_code == 402
    assert lowball.headers["x-402-amount"] == "5000000"
    assert lowball.json()["amount_usdc"] == 5.0
    # paying only the lowball amount never unlocks anything
    assert client.post(
        f"/api/x402/paywall?item={item_id}&amount=0.01", headers={"x-402-payment": "0x" + "ee" * 32}
    ).status_code == 402
    # no purchases were recorded
    assert ledger.paywall_purchased(item_id, 777) is False


def test_x402_paywall_replay_409(client, ledger, monkeypatch):
    from web import x402

    item_id = ledger.create_paywall(777, "Мой отчёт", 5_000_000, "секретный контент")
    tx = "0x" + "dd" * 32
    monkeypatch.setattr(
        x402, "_verify_payment", lambda h, amt: {"sender": "0x" + "22" * 20, "amount_micro": amt}
    )
    first = client.post(
        f"/api/x402/paywall?item={item_id}&amount=5", headers={"x-402-payment": tx}
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/x402/paywall?item={item_id}&amount=5", headers={"x-402-payment": tx}
    )
    assert second.status_code == 409
    # owner was credited exactly once
    assert ledger.user_view(777)["balance_micro"] == 5_000_000


def test_qr_endpoint(client):
    r = client.get("/qr", params={"data": "base:0x" + "ab" * 20, "size": 220})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_qr_endpoint_rejects_long_data(client):
    assert client.get("/qr", params={"data": "x" * 2000}).status_code == 400


def test_frame_page_exposes_paywall_card(client, ledger, monkeypatch):
    from bot import config as cfg

    monkeypatch.setattr(cfg, "WEBHOOK_URL", "https://tipbot.example.com")
    monkeypatch.setattr(cfg, "BOT_USERNAME", "base_tipbot")
    item_id = ledger.create_paywall(777, "Альфа-сигнал", 1_000_000, "секрет")
    r = client.get(f"/frame/{item_id}")
    assert r.status_code == 200
    html = r.text
    assert "fc:frame" in html
    assert "https://tipbot.example.com/static/frame.png" in html
    assert "https://t.me/base_tipbot?start=paywall_" + str(item_id) in html
    assert "x402" in html
    assert "https://tipbot.example.com/api/x402/paywall?item=" + str(item_id) in html
    # unknown item -> fallback page
    assert "not found" in client.get("/frame/999").text


def test_markets_status_filter(client, ledger):
    ledger.credit(1, 100_000_000, "deposit")
    ledger.credit(2, 100_000_000, "deposit")
    bid = ledger.create_bet(1, "Сыграем?", ["Да", "Нет"])
    ledger.place_bet(bid, 2, 0, 10_000_000)
    ledger.resolve_bet(bid, 0, 1)

    open_ = client.get("/api/markets").json()
    assert open_ == []  # resolved market is not "open"

    resolved = client.get("/api/markets?status=resolved").json()
    assert len(resolved) == 1
    assert resolved[0]["id"] == bid
    assert resolved[0]["status"] == "resolved"


def test_user_page_served(client):
    r = client.get("/u/777")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_user_history_in_api(client, ledger):
    ledger.credit(777, 5_000_000, "deposit")
    _auth(client, 777)
    r = client.get("/api/user/777")
    data = r.json()
    assert data["history"]
    assert data["history"][0]["kind"] == "deposit"
    assert data["history"][0]["amount_usdc"] == 5.0


def test_user_creator_fees_in_api(client, ledger):
    ledger.credit(1, 2_000_000, "deposit")
    ledger.credit(2, 2_000_000, "deposit")
    bid = ledger.create_bet(1, "Q", ["А", "Б"])
    ledger.place_bet(bid, 2, 0, 1_000_000)
    ledger.place_bet(bid, 2, 1, 1_000_000)
    ledger.resolve_bet(bid, 0, 1)
    _auth(client, 1)
    data = client.get("/api/user/1").json()
    assert data["creator_fees_usdc"] == 0.02


def test_market_page_served(client, ledger):
    bid = ledger.create_bet(1, "Вопрос?", ["А", "Б"])
    r = client.get(f"/m/{bid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_api_rate_limited(client, monkeypatch):
    from web import server

    class FakeEth:
        def __init__(self):
            self.block_number = 1000

    monkeypatch.setattr(server.base, "w3", types.SimpleNamespace(eth=FakeEth()))
    server._rl_state.clear()
    monkeypatch.setattr(server, "WEB_RATE_LIMIT", 2)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 429
    server._rl_state.clear()


def test_static_not_rate_limited(client, monkeypatch):
    from web import server

    server._rl_state.clear()
    monkeypatch.setattr(server, "WEB_RATE_LIMIT", 1)
    # Static pages are outside the limiter (they never touch RPC/CPU-heavy work).
    assert client.get("/").status_code == 200
    assert client.get("/style.css").status_code == 200
    server._rl_state.clear()


# ---------- volume analytics ----------


def test_stats_volume_30d(client, ledger):
    ledger.credit(1, 5_000_000, "deposit")
    ledger.transfer(1, 2, 2_000_000)
    data = client.get("/api/stats").json()
    assert data["volume_30d_usdc"] == 7.0


def test_volume_history(client, ledger):
    ledger.credit(1, 5_000_000, "deposit")
    r = client.get("/api/volume_history")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["day"] is not None
    assert data[0]["volume_usdc"] == 5.0


def test_volume_history_days_clamped(client, ledger):
    ledger.credit(1, 5_000_000, "deposit")
    r = client.get("/api/volume_history?days=400")
    assert r.status_code == 200  # clamped to 30, not rejected
    assert len(r.json()) == 1
    r = client.get("/api/volume_history?days=0")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ---------- backers ----------


def test_markets_include_backers(client, ledger):
    ledger.credit(1, 100_000_000, "deposit")
    ledger.credit(2, 100_000_000, "deposit")
    bid = ledger.create_bet(1, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, 1, 0, 10_000_000)
    ledger.place_bet(bid, 2, 0, 5_000_000)
    markets = client.get("/api/markets").json()
    assert len(markets) == 1
    assert markets[0]["total_backers"] == 2
    assert [o["backers"] for o in markets[0]["options"]] == [2, 0]


def test_market_detail_include_backers(client, ledger):
    ledger.credit(1, 100_000_000, "deposit")
    ledger.credit(2, 100_000_000, "deposit")
    ledger.credit(3, 100_000_000, "deposit")
    bid = ledger.create_bet(1, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, 1, 0, 10_000_000)
    ledger.place_bet(bid, 2, 0, 5_000_000)
    ledger.place_bet(bid, 3, 1, 3_000_000)
    market = client.get(f"/api/market/{bid}").json()
    assert market["total_backers"] == 3
    assert [o["backers"] for o in market["options"]] == [2, 1]


# ---------- static pages: HTML ↔ JS wiring ----------


def test_index_page_has_js_wiring(client):
    html = client.get("/").text
    for needle in (
        "stat-vol30",
        "volume-chart",
        "markets-list",
        "closed-markets-list",
        "leaderboard",
        "wallet-solvent",
        "wallet-vault-row",
        "wallet-vault-addr",
        "data-tg-link",
        "/app.js",
    ):
        assert needle in html
    app_js = client.get("/app.js").text
    assert "wallet-vault-row" in app_js
    assert "vault_address" in app_js
    assert "vault_balance_usdc" in app_js


def test_market_page_has_js_wiring(client, ledger):
    bid = ledger.create_bet(1, "Вопрос?", ["А", "Б"])
    html = client.get(f"/m/{bid}").text
    for needle in (
        "m-question",
        "m-options",
        "m-pot",
        "m-meta",
        "m-share",
        "data-close-at",
        "option-winner",
    ):
        assert needle in html


def test_user_page_has_js_wiring(client):
    html = client.get("/u/777").text
    for needle in (
        "u-balance",
        "u-received",
        "u-won",
        "history-list",
        "u-copy",
        "u-qr",
    ):
        assert needle in html


def test_css_has_new_ui_styles(client):
    css = client.get("/style.css").text
    for needle in (
        ".chart-bar",
        ".chip-open",
        ".option-winner",
        ".bar-fill-win",
        ".empty",
    ):
        assert needle in css


def test_miniapp_meta_tags_render_public_url(client, monkeypatch):
    """/app must embed fc:miniapp meta with the REAL public URL — the Base
    App crawls these tags to render the launch card."""
    from web import mini as mini_mod

    monkeypatch.setattr(mini_mod, "public_base_url", lambda: "https://tippy.example.com")
    r = client.get('/app')
    assert r.status_code == 200
    assert 'fc:miniapp' in r.text
    assert 'https://tippy.example.com/app' in r.text
    assert '__PUBLIC_URL__' not in r.text, "placeholder must never leak to users"


def test_farcaster_manifest_404_when_not_configured(client, monkeypatch):
    from pathlib import Path

    from web import server as web_server

    monkeypatch.setattr(web_server, "ROOT", Path(__file__).resolve().parent.parent)
    manifest = Path(__file__).resolve().parent.parent / 'deploy' / 'farcaster_manifest.json'
    exists = manifest.exists()
    if exists:
        pytest.skip('operator manifest present on this machine')
    r = client.get('/.well-known/farcaster.json')
    assert r.status_code == 404


def test_farcaster_manifest_served_when_configured(client):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    manifest = root / 'deploy' / 'farcaster_manifest.json'
    sample = '{"accountAssociation": {"header": "h", "payload": "p", "signature": "s"}}'
    manifest.write_text(sample, encoding='utf-8')
    try:
        r = client.get('/.well-known/farcaster.json')
        assert r.status_code == 200
        assert 'accountAssociation' in r.text
    finally:
        manifest.unlink()


def test_miniapp_webhook_always_200(client):
    r = client.post('/api/webhook-miniaction', json={'event': 'notification_clicked'})
    assert r.status_code == 200
    r = client.post('/api/webhook-miniaction', content=b'not json',
                    headers={'content-type': 'application/json'})
    assert r.status_code == 200  # never make the platform retry
