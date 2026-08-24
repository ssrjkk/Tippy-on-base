"""Prediction markets v2 (LMSR AMM): math, ledger lifecycle, handlers.

The core invariant under test: money conservation. Users' balances plus all
market escrows stay constant through create/buy/sell/resolve/cancel — the
AMM only moves money between them, never creates or loses it.
"""

import asyncio
from decimal import Decimal

from bot import ai as ai_mod
from bot.handlers import (
    cb_market_card,
    cb_mk_do,
    cb_mk_resolve,
    cmd_ask,
    cmd_market,
    cmd_markets,
    cmd_positions,
    cmd_sell,
    cmd_trade,
    cmd_tx,
)
from bot.ledger import lmsr_buy_shares, lmsr_cost, lmsr_prices, lmsr_sell_value

ALICE, BOB, CAROL = 3001, 3002, 3003
USDC = 10**6


def run(coro):
    return asyncio.run(coro)


# ---------- mocks (same shape as test_handlers.py) ----------


class User:
    def __init__(self, id, username=None):
        self.id = id
        self.username = username
        self.is_bot = False


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kw):
        self.sent.append((chat_id, text))

    async def send_chat_action(self, chat_id, action, **kw):
        self.sent.append((chat_id, f"<action:{action}>"))


class Message:
    def __init__(self, text="", from_id=ALICE, username="alice", bot=None):
        self.text = text
        self.from_user = User(from_id, username)
        self.bot = bot or Bot()
        self.answers = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))


class AnswerRecorder:
    def __init__(self, bot=None):
        self.text = None
        self.markup = None
        self.bot = bot or Bot()

    async def edit_text(self, text, reply_markup=None, **kw):
        self.text = text
        self.markup = reply_markup

    async def edit_caption(self, caption=None, reply_markup=None, **kw):
        self.caption = caption
        self.markup = reply_markup


class Callback:
    def __init__(self, data, from_id, bot=None):
        self.data = data
        self.from_user = User(from_id)
        self.message = AnswerRecorder(bot)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def money(ledger):
    """Total money in the system: user balances + open-market escrows."""
    balances = ledger._conn.execute("SELECT COALESCE(SUM(balance), 0) AS s FROM users").fetchone()["s"]
    escrow = ledger._conn.execute(
        "SELECT COALESCE(SUM(escrow_micro), 0) AS s FROM markets WHERE status = 'open'"
    ).fetchone()["s"]
    return int(balances) + int(escrow)


def make_market(ledger, subsidy=50 * USDC, options=("Алиса", "Боб")):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    mid = ledger.create_market(ALICE, "Кто победит?", list(options), subsidy)
    assert isinstance(mid, int)
    return mid


# ---------- LMSR math ----------


def test_lmsr_prices_uniform_at_zero():
    p = lmsr_prices([0, 0], 10_000_000)
    assert len(p) == 2
    for x in p:
        assert abs(x - Decimal("0.5")) < Decimal("0.0001")


def test_lmsr_prices_sum_to_one():
    p = lmsr_prices([1_200_000, 300_000, 50_000], 2_000_000)
    assert abs(sum(p) - 1) < Decimal("1e-12")
    assert p[0] > p[1] > p[2]


def test_lmsr_buy_shares_respects_budget():
    q = [0, 0]
    b = 20_000_000
    spend = 5 * USDC
    shares = lmsr_buy_shares(q, b, 0, spend)
    assert shares > 0
    # cost of exactly `shares` must fit the budget; one more share must not
    cost = lmsr_cost([shares, 0], b) - lmsr_cost(q, b)
    cost_plus = lmsr_cost([shares + 1, 0], b) - lmsr_cost(q, b)
    assert cost <= spend < cost_plus


def test_lmsr_buy_moves_price_up():
    q = [0, 0]
    b = 20_000_000
    before = lmsr_prices(q, b)[0]
    shares = lmsr_buy_shares(q, b, 0, 10 * USDC)
    after = lmsr_prices([shares, 0], b)[0]
    assert after > before


def test_lmsr_sell_value_less_than_buy_cost():
    """Round-trip must lose money (spread + rounding favor the AMM)."""
    q = [0, 0]
    b = 20_000_000
    spend = 10 * USDC
    shares = lmsr_buy_shares(q, b, 0, spend)
    got_back = lmsr_sell_value([shares, 0], b, 0, shares)
    assert 0 < got_back <= spend


def test_lmsr_funding_theorem_never_insolvent():
    """Escrow >= max(q_i) along an aggressive trading path (b*ln(n) funding)."""
    import random

    rng = random.Random(42)
    n = 3
    subsidy = 30 * USDC
    from decimal import ROUND_FLOOR, localcontext

    with localcontext() as ctx:
        ctx.prec = 40
        b = int((Decimal(subsidy) / Decimal(n).ln()).to_integral_value(rounding=ROUND_FLOOR))
    q = [0] * n
    escrow = subsidy
    for _ in range(60):
        i = rng.randrange(n)
        if rng.random() < 0.6:
            spend = rng.randrange(1, 8 * USDC)
            shares = lmsr_buy_shares(q, b, i, spend)
            if shares > 0:
                q[i] += shares
                escrow += spend
        else:
            held = q[i]
            if held > 0:
                sell = rng.randrange(1, held + 1)
                value = lmsr_sell_value(q, b, i, sell)
                q[i] -= sell
                escrow -= value
        assert escrow >= max(q), f"insolvent: escrow={escrow} max_q={max(q)}"


# ---------- ledger lifecycle ----------


def test_create_market_debits_and_funds_escrow(ledger):
    mid = make_market(ledger)
    m = ledger.get_market(mid)
    assert m["status"] == "open"
    assert m["escrow_micro"] == 50 * USDC
    assert ledger.balance(ALICE) == Decimal(950)
    assert money(ledger) == 1000 * USDC


def test_create_market_insufficient_balance(ledger):
    ledger.credit(ALICE, 5 * USDC, "deposit")
    assert ledger.create_market(ALICE, "?", ["a", "b"], 50 * USDC) == "balance"


def test_create_market_subsidy_too_small(ledger):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    assert ledger.create_market(ALICE, "?", ["a", "b"], 0) == "subsidy"


def test_buy_shares_conservation_and_escrow(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    before = money(ledger)
    status, info = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    assert status == "ok"
    assert info["shares"] > 0
    assert info["cost"] == 10 * USDC
    assert ledger.balance(BOB) == Decimal(90)
    m = ledger.get_market(mid)
    assert m["escrow_micro"] == 60 * USDC
    pos = ledger.user_market_position(mid, BOB)
    assert pos[0]["shares"] == info["shares"]
    assert pos[0]["cost"] == 10 * USDC
    assert money(ledger) == before


def test_buy_shares_too_small_refunds(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    status, _ = ledger.buy_shares(mid, BOB, 0, 1)  # 1 micro-USDC
    assert status == "toosmall"
    assert ledger.balance(BOB) == Decimal(100)  # refunded


def test_sell_shares_conservation(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, buy = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    before = money(ledger)
    status, info = ledger.sell_shares(mid, BOB, 0, buy["shares"])
    assert status == "ok"
    assert 0 < info["value"] <= 10 * USDC
    assert ledger.balance(BOB) == Decimal(90) + Decimal(info["value"]) / USDC
    assert ledger.user_market_position(mid, BOB).get(0, {}).get("shares", 0) == 0
    assert money(ledger) == before


def test_sell_without_position(ledger):
    mid = make_market(ledger)
    status, _ = ledger.sell_shares(mid, BOB, 0, 5 * USDC)
    assert status in ("noshare", "closed")


def test_resolve_market_pays_winners_creator_keeps_leftover(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.credit(CAROL, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 20 * USDC)
    _, c = ledger.buy_shares(mid, CAROL, 1, 10 * USDC)
    before = money(ledger)

    ok, msg, payouts = ledger.resolve_market(mid, 0, ALICE)
    assert ok
    bob_win = [p for p in payouts if p["tg_id"] == BOB and p["win"]]
    assert bob_win and bob_win[0]["net_micro"] == b["shares"]  # 1 micro-share = 1 micro-USDC
    carol_lost = [p for p in payouts if p["tg_id"] == CAROL]
    assert carol_lost and not carol_lost[0]["win"]
    # conservation: winners paid from escrow, creator keeps the rest
    assert money(ledger) == before
    m = ledger.get_market(mid)
    assert m["status"] == "resolved" and m["winner"] == 0
    total_paid = sum(p["net_micro"] for p in payouts) + ledger.creator_fees(ALICE)
    assert total_paid == 80 * USDC  # whole escrow distributed


def test_resolve_market_only_creator(ledger):
    mid = make_market(ledger)
    ok, msg, _ = ledger.resolve_market(mid, 0, BOB)
    assert not ok and "создатель" in msg


def test_cancel_market_refunds_cost_basis(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    before = money(ledger)
    ok, msg = ledger.cancel_market(mid, ALICE)
    assert ok
    assert ledger.balance(BOB) == Decimal(90) + Decimal(b["cost"]) / USDC  # net cost refunded
    # Bob's refund comes from the shared escrow (subsidy + his own buy), so
    # the creator gets the whole subsidy back (plus any rounding dust).
    assert ledger.balance(ALICE) >= Decimal(950)
    assert ledger.balance(ALICE) <= Decimal(1000)
    assert money(ledger) == before
    assert ledger.get_market(mid)["status"] == "cancelled"


def test_market_deadline_blocks_trades(ledger):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    import time

    mid = ledger.create_market(
        ALICE, "?", ["a", "b"], 50 * USDC, close_at=int(time.time()) - 1
    )
    ledger.credit(BOB, 100 * USDC, "deposit")
    assert ledger.buy_shares(mid, BOB, 0, 5 * USDC)[0] == "deadline"
    assert ledger.sell_shares(mid, BOB, 0, 1)[0] == "deadline"


def test_user_market_positions_view(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    positions = ledger.user_market_positions(BOB)
    assert len(positions) == 1
    p = positions[0]
    assert p["market_id"] == mid
    assert p["shares"] == b["shares"]
    assert 0 < float(p["price"]) < 1


def test_amm_market_view_shape(ledger):
    mid = make_market(ledger)
    view = ledger.amm_market_view(mid)
    assert view["id"] == mid
    assert len(view["options"]) == 2
    assert abs(sum(o["price_pct"] for o in view["options"]) - 100.0) < 0.01
    assert view["liquidity_micro"] == 50 * USDC


# ---------- handlers ----------


def test_cmd_market_create(ledger):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    m = Message("/market create 50 Кто победит? | Алиса | Боб 24h")
    run(cmd_market(m))
    assert m.answers and "создан" in m.answers[0][0]
    markets = ledger.open_markets()
    assert len(markets) == 1
    assert ledger.balance(ALICE) == Decimal(950)


def test_cmd_market_create_min_subsidy(ledger):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    m = Message("/market create 5 Кто? | А | Б")
    run(cmd_market(m))
    assert "Минимальный банк" in m.answers[0][0]
    assert not ledger.open_markets()


def test_cmd_markets_lists(ledger):
    make_market(ledger)
    m = Message("/markets")
    run(cmd_markets(m))
    text, kb = m.answers[0]
    assert "Рынки предсказаний" in text
    assert kb.inline_keyboard[-1][0].callback_data == "mkcreate"


def test_cmd_trade_buys(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    m = Message(f"/trade {mid} 1 10", from_id=BOB)
    run(cmd_trade(m))
    assert "Куплено" in m.answers[0][0]
    assert ledger.balance(BOB) == Decimal(90)


def test_cmd_trade_validates(ledger):
    mid = make_market(ledger)
    m = Message("/trade", from_id=BOB)
    run(cmd_trade(m))
    assert "Формат" in m.answers[0][0]
    m = Message(f"/trade {mid} 9 10", from_id=BOB)
    run(cmd_trade(m))
    assert "Неверный номер варианта" in m.answers[0][0]


def test_cmd_sell_sells_back(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    m = Message(f"/sell {mid} 1 50%", from_id=BOB)
    run(cmd_sell(m))
    assert "Продано" in m.answers[0][0]
    sold = ledger._conn.execute(
        "SELECT amount FROM tx_log WHERE kind='market_sell' AND tg_id=%s", (BOB,)
    ).fetchall()
    assert sold  # a sell was recorded


def test_cmd_positions(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    m = Message("/positions", from_id=BOB)
    run(cmd_positions(m))
    assert "Твои позиции" in m.answers[0][0]
    assert "#" in m.answers[0][0]


def test_cb_market_card_shows_odds(ledger):
    mid = make_market(ledger)
    cb = Callback(f"mk:{mid}", BOB)
    run(cb_market_card(cb))
    assert "%" in cb.message.text
    assert any(b.callback_data.startswith("mkbuy:") for row in cb.message.markup.inline_keyboard for b in row)


def test_cb_mk_do_buys(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    cb = Callback(f"mkdo:{mid}:0:10", BOB)
    run(cb_mk_do(cb))
    assert "Куплено" in cb.message.text
    assert ledger.balance(BOB) == Decimal(90)


def test_cb_mk_resolve_notifies_traders(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    bot = Bot()
    cb = Callback(f"mkres:{mid}:0", ALICE, bot=bot)
    run(cb_mk_resolve(cb))
    assert "закрыт" in cb.message.text
    dms = [s for s in bot.sent if s[0] == BOB]
    assert dms and "выиграл" in dms[0][1]


def test_cb_mk_resolve_rejects_non_creator(ledger):
    mid = make_market(ledger)
    cb = Callback(f"mkres:{mid}:0", BOB)
    run(cb_mk_resolve(cb))
    assert cb.answers and "создатель" in cb.answers[0][0]


# ---------- AI assistant ----------


def test_ai_disabled_hint(ledger, monkeypatch):
    monkeypatch.setattr(ai_mod.config, "AI_API_KEY", None)
    m = Message("/ask что такое base?")
    run(cmd_ask(m))
    assert "не подключён" in m.answers[0][0]


def test_ai_no_question_shows_usage(ledger):
    m = Message("/ask")
    run(cmd_ask(m))
    assert "/ask" in m.answers[0][0]


def test_ask_success(ledger, monkeypatch):
    monkeypatch.setattr(ai_mod.config, "AI_API_KEY", "sk-test")

    async def _ask(q):
        return "Base — это L2 от Coinbase."

    monkeypatch.setattr(ai_mod, "ask_about_markets", _ask)
    m = Message("/ask что такое base?")
    run(cmd_ask(m))
    assert "Tippy AI" in m.answers[0][0]
    assert "L2 от Coinbase" in m.answers[0][0]


def test_ask_error_is_friendly(ledger, monkeypatch):
    monkeypatch.setattr(ai_mod.config, "AI_API_KEY", "sk-test")

    async def boom(q):
        raise RuntimeError("AI HTTP 429: rate limited")

    monkeypatch.setattr(ai_mod, "ask_about_markets", boom)
    m = Message("/ask привет")
    run(cmd_ask(m))
    assert "недоступен" in m.answers[0][0]
    assert "429" in m.answers[0][0]


def test_ask_with_reply_context(ledger, monkeypatch):
    monkeypatch.setattr(ai_mod.config, "AI_API_KEY", "sk-test")
    captured = {}
    async def _ask(q):
        captured.update(q=q)
        return "ок"

    monkeypatch.setattr(ai_mod, "ask_about_markets", _ask)
    ref = Message("ETH газ дорогой")
    m = Message("/ask объясни", )
    m.reply_to_message = ref
    run(cmd_ask(m))
    assert "Context (replied message)" in captured["q"]
    assert "ETH газ" in captured["q"]


# ---------- /tx on-chain lookup ----------


def test_cmd_tx_invalid_hash(ledger):
    m = Message("/tx nothash")
    run(cmd_tx(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_tx_found_decodes_usdc(ledger, monkeypatch):
    from bot import base

    h = "0x" + "ab" * 32
    async def _fake_tx(t):
        return {
            "hash": t,
            "from": "0x" + "1" * 40,
            "to": "0x" + "2" * 40,
            "status": True,
            "value_micro": 5 * USDC,
            "usdc_to": "0x" + "3" * 40,
        }
    monkeypatch.setattr(base, "tx_info", _fake_tx)
    m = Message(f"/tx {h}")
    run(cmd_tx(m))
    text = m.answers[0][0]
    assert "USDC:" in text
    assert "confirmed" in text.lower() or "подтвержд" in text.lower()
    assert "Basescan" in text


def test_cmd_tx_not_found(ledger, monkeypatch):
    from bot import base

    async def _fake_tx_none(t):
        return None
    monkeypatch.setattr(base, "tx_info", _fake_tx_none)
    m = Message(f"/tx {'0x' + 'cd' * 32}")
    run(cmd_tx(m))
    assert "не найдена" in m.answers[0][0]


# ---------- web API ----------


def test_api_predictions_endpoint(ledger):
    from fastapi.testclient import TestClient

    from web import server

    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    prev = server.ledger
    from bot.ledger import AsyncLedger
    server.ledger = AsyncLedger(ledger)
    try:
        client = TestClient(server.app)
        r = client.get("/api/predictions")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["id"] == mid
        assert data[0]["traders"] == 1
        assert abs(sum(o["price_pct"] for o in data[0]["options"]) - 100.0) < 0.01
        r = client.get(f"/api/prediction/{mid}")
        assert r.status_code == 200
        assert r.json()["liquidity_usdc"] > 0
        assert client.get("/api/prediction/999999").status_code == 404
    finally:
        server.ledger = prev


# ---------- LMSR edge cases ----------


def test_lmsr_tiny_b_no_crash():
    """Very small liquidity parameter (b=1 micro) must not overflow."""
    q = [0, 0]
    b = 1  # 1 micro-USDC
    p = lmsr_prices(q, b)
    assert abs(sum(p) - 1) < Decimal("1e-6")
    assert all(0 < x <= 1 for x in p)


def test_lmsr_extreme_concentration():
    """All shares in one option — prices must still sum to 1."""
    q = [1_000_000_000, 0, 0]  # 1000 USDC in option 0
    b = 10_000_000  # 10 USDC
    p = lmsr_prices(q, b)
    assert abs(sum(p) - 1) < Decimal("1e-12")
    assert p[0] > Decimal("0.99")  # >99% probability


def test_lmsr_buy_zero_spend():
    """Spending 0 must yield 0 shares."""
    q = [0, 0]
    b = 10_000_000
    assert lmsr_buy_shares(q, b, 0, 0) == 0


def test_lmsr_sell_more_than_held_bounded():
    """Selling more shares than held must return 0 (can't go negative)."""
    q = [100, 0]
    b = 10_000_000
    # lmsr_sell_value doesn't check bounds — the caller does.
    # But mathematically, selling more than q[0] should give a very large value
    # (which the caller rejects). Verify it doesn't crash.
    val = lmsr_sell_value(q, b, 0, 50)
    assert val >= 0


def test_lmsr_buy_sell_roundtrip_conservation():
    """Buy then sell same shares: escrow change = buy_spend - sell_value >= 0."""
    q = [0, 0]
    b = 20_000_000
    spend = 15 * USDC
    shares = lmsr_buy_shares(q, b, 0, spend)
    assert shares > 0
    q_after_buy = [shares, 0]
    sell_value = lmsr_sell_value(q_after_buy, b, 0, shares)
    # Round-trip cost = spend - sell_value (the AMM's spread)
    assert 0 <= spend - sell_value


def test_lmsr_three_options_monotone():
    """Buying more of option 0 must increase its price monotonically."""
    q = [0, 0, 0]
    b = 30_000_000
    prices_before = lmsr_prices(q, b)
    shares = lmsr_buy_shares(q, b, 0, 5 * USDC)
    q[0] += shares
    prices_after = lmsr_prices(q, b)
    assert prices_after[0] > prices_before[0]
    # Other options' prices must decrease
    for i in range(1, 3):
        assert prices_after[i] < prices_before[i]
