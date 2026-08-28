"""On-chain market handler tests: validation, access control, disabled state.

The /oc* layer is unit-tested with OUTCOME_MARKET_ADDRESS unset or pointed at
a dummy address and an empty registry — every path exercised here stops at
ledger reads and never touches an RPC. Contract math itself is covered by the
forge suite and the EVM parity tests; the registry by test_onchain_registry.
"""

import pytest
from test_handlers import Message  # tests/ is not a package: flat import

from bot import handlers
from bot.handlers import (
    cb_oc_resolve,
    cmd_oc,
    cmd_oc_buy,
    cmd_oc_create,
    cmd_oc_resolve,
    cmd_oc_sell,
)

ALICE, BOB = 2101, 2102


@pytest.fixture()
def oc_on(monkeypatch):
    """Enable the on-chain layer with a dummy address (no RPC is reached —
    every tested path stops at registry reads)."""
    monkeypatch.setattr(handlers._common.config, "OUTCOME_MARKET_ADDRESS",
                        "0x" + "11" * 20)


@pytest.fixture()
def oc_off(monkeypatch):
    monkeypatch.setattr(handlers._common.config, "OUTCOME_MARKET_ADDRESS", None)


@pytest.mark.asyncio
async def test_oc_disabled_without_contract(oc_off, ledger):
    m = Message(text="/oc", from_id=ALICE)
    await cmd_oc(m)
    assert "не задеплоен" in m.answers[0][0] or "not deployed" in m.answers[0][0].lower()


@pytest.mark.asyncio
async def test_oc_buy_disabled_without_contract(oc_off, ledger):
    m = Message(text="/oc_buy 1 1 5", from_id=ALICE)
    await cmd_oc_buy(m)
    assert "не задеплоен" in m.answers[0][0] or "not deployed" in m.answers[0][0].lower()


@pytest.mark.asyncio
async def test_oc_buy_format_errors(oc_on, ledger):
    for text in ("/oc_buy", "/oc_buy 1 1", "/oc_buy abc 1 5", "/oc_buy 1 1 -5",
                 "/oc_buy 1 1", "/oc_buy 99999999999999999999 1 5"):
        m = Message(text=text, from_id=ALICE)
        await cmd_oc_buy(m)
        assert m.answers, f"no answer for {text!r}"


@pytest.mark.asyncio
async def test_oc_buy_unknown_market(oc_on, ledger):
    m = Message(text="/oc_buy 777 1 5", from_id=ALICE)
    await cmd_oc_buy(m)
    # Market 777 is not in the registry — refused before any wallet/RPC work.
    assert "не найден" in m.answers[0][0] or "not found" in m.answers[0][0].lower()


@pytest.mark.asyncio
async def test_oc_sell_unknown_market(oc_on, ledger):
    m = Message(text="/oc_sell 777 1", from_id=ALICE)
    await cmd_oc_sell(m)
    assert "не найден" in m.answers[0][0] or "not found" in m.answers[0][0].lower()


@pytest.mark.asyncio
async def test_oc_create_format_errors(oc_on, ledger):
    for text in ("/oc_create", "/oc_create 5", "/oc_create 5 Вопрос",
                 "/oc_create 5 Вопрос | Только один",
                 "/oc_create 2 Вопрос | А | Б",  # subsidy below minimum
                 "/oc_create 99999 Вопрос | А | Б"):  # subsidy above maximum
        m = Message(text=text, from_id=ALICE)
        await cmd_oc_create(m)
        assert m.answers, f"no answer for {text!r}"
        assert "⛓️ Рынок" not in m.answers[0][0] and "Market" not in m.answers[0][0][:20]


@pytest.mark.asyncio
async def test_oc_resolve_denied_for_non_creator(oc_on, ledger):
    ledger.save_onchain_market(5, BOB, "Whose?", ["A", "B"], 0)
    m = Message(text="/oc_resolve 5 1", from_id=ALICE)  # ALICE != creator BOB
    await cmd_oc_resolve(m)
    assert "создатель" in m.answers[0][0] or "creator" in m.answers[0][0].lower()


@pytest.mark.asyncio
async def test_oc_resolve_unknown_market(oc_on, ledger):
    m = Message(text="/oc_resolve 777 1", from_id=ALICE)
    await cmd_oc_resolve(m)
    assert "не найден" in m.answers[0][0] or "not found" in m.answers[0][0].lower()


@pytest.mark.asyncio
async def test_oc_resolve_reports_chain_failure(oc_on, ledger, monkeypatch):
    """Creator passes the ownership gate; the resolve then fails at the chain
    layer — the error is reported to the creator, never swallowed."""
    from bot import onchain_market as om

    async def fake_info(mid):
        return {"resolved": False, "cancelled": False, "disputed": False,
                "winning_outcome": 0, "num_outcomes": 2}

    async def fail_resolve(mid, outcome, key):
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(om, "get_market_info", fake_info)
    monkeypatch.setattr(om, "oracle_resolve", fail_resolve)
    monkeypatch.setattr(om, "owner_resolve", fail_resolve)

    ledger.save_onchain_market(5, ALICE, "Whose?", ["A", "B"], 0)
    m = Message(text="/oc_resolve 5 1", from_id=ALICE)
    await cmd_oc_resolve(m)
    assert m.answers, "creator must get an explicit result"
    text = m.answers[0][0]
    assert text.startswith(("✅", "❌")), "resolve must end in an explicit ok/fail message"
    assert "connection dropped" in text


@pytest.mark.asyncio
async def test_oc_resolve_disputed_gets_friendly_message(oc_on, ledger, monkeypatch):
    """After the owner disputes, the oracle may not re-post: the creator gets
    the dedicated explanation, not a raw revert string."""
    from bot import onchain_market as om

    async def fake_info(mid):
        return {"resolved": False, "cancelled": False, "disputed": True,
                "winning_outcome": 0, "num_outcomes": 2}

    async def revert_disputed(mid, outcome, key):
        raise RuntimeError("MarketDisputed()")

    monkeypatch.setattr(om, "get_market_info", fake_info)
    monkeypatch.setattr(om, "oracle_resolve", revert_disputed)
    # empty ORACLE_PRIVATE_KEY in tests -> the flow falls back to the owner
    # path; mock it too so the unit test never touches a live RPC.
    monkeypatch.setattr(om, "owner_resolve", revert_disputed)

    ledger.save_onchain_market(7, ALICE, "Whose?", ["A", "B"], 0)
    m = Message(text="/oc_resolve 7 1", from_id=ALICE)
    await cmd_oc_resolve(m)
    assert m.answers and "ownerResolve" in m.answers[0][0]


@pytest.mark.asyncio
async def test_oc_resolve_callback_ownership_check(oc_on, ledger):
    """Callback data is client-controlled: a non-creator must not be able to
    resolve someone else's market through ocr: callbacks."""
    ledger.save_onchain_market(6, BOB, "Whose?", ["A", "B"], 0)

    class CB:
        def __init__(self, from_id, data):
            self.from_user = type("U", (), {"id": from_id})()
            self.data = data
            self.answered = []

        async def answer(self, text=None, show_alert=False):
            self.answered.append((text, show_alert))

    cb = CB(ALICE, "ocr:6:1")  # ALICE is not the creator BOB
    await cb_oc_resolve(cb)
    assert cb.answered and (
        "создатель" in (cb.answered[0][0] or "")
        or "creator" in (cb.answered[0][0] or "").lower()
    )
