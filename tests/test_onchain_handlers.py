"""Buy/sell core on-chain flows — mock-based unit tests.

These tests exercise the shared _buy_core/_sell_core orchestration logic
(validation, throttling, wallet balance check, trade-log write) without a
real chain: all RPC/contract helper calls are mocked.
"""

import time
from unittest.mock import AsyncMock, Mock

import pytest

from bot.handlers import _common as common
from bot.handlers import onchain


@pytest.fixture()
def market_row():
    return {
        "id": 7,
        "creator": 1,
        "question": "Будет ли дождь?",
        "options": '["Да", "Нет"]',
        "close_at": int(time.time()) + 3600,
    }


@pytest.fixture()
def market_getter(monkeypatch):
    """Patch common.ledger.get_onchain_market to return a controllable row."""
    state = {"row": None}

    async def _get(mid):
        return state["row"]

    monkeypatch.setattr(common.ledger, "get_onchain_market", _get)
    return state


@pytest.fixture()
def onchain_env(monkeypatch):
    """Isolate _buy_core/_sell_core from the chain: deterministic balances,
    quantities, contract calls, and count submits."""
    calls = {"buys": 0, "sells": 0}

    async def fake_wallet_key(tg_id):
        return ("0x" + "AA" * 32, "0x" + "ab" * 32)

    monkeypatch.setattr(onchain, "_wallet_key", fake_wallet_key)
    monkeypatch.setattr(onchain, "_wallet_usdc_sync", lambda addr: 500_000_000)
    monkeypatch.setattr(onchain, "_q_sync", lambda mid, n: [100_000_000, 100_000_000])
    monkeypatch.setattr(onchain, "_b_sync", lambda mid: 50_000_000)
    monkeypatch.setattr(common, "_throttle", AsyncMock(return_value=None))
    monkeypatch.setattr(onchain.om.Web3, "to_checksum_address", lambda addr: addr)

    async def fake_buy(mid, outcome, shares, spend, key):
        calls["buys"] += 1
        return "0x" + "11" * 32

    async def fake_sell(mid, outcome, shares, min_proceeds, key):
        calls["sells"] += 1
        return "0x" + "22" * 32

    monkeypatch.setattr(onchain.om, "buy", fake_buy)
    monkeypatch.setattr(onchain.om, "sell", fake_sell)
    monkeypatch.setattr(common.ledger, "record_onchain_trade", AsyncMock(return_value=None))
    return calls


@pytest.mark.asyncio
async def test_buy_core_success(onchain_env, market_getter, market_row):
    market_getter["row"] = market_row
    ok, text = await onchain._buy_core(1, 7, 0, 10_000_000, "ru")
    assert ok is True
    assert onchain_env["buys"] == 1
    common.ledger.record_onchain_trade.assert_awaited()


@pytest.mark.asyncio
async def test_buy_core_unknown_market(onchain_env, market_getter):
    market_getter["row"] = None
    ok, _ = await onchain._buy_core(1, 999, 0, 10_000_000, "ru")
    assert ok is False
    assert onchain_env["buys"] == 0


@pytest.mark.asyncio
async def test_buy_core_bad_outcome(onchain_env, market_getter, market_row):
    market_getter["row"] = market_row
    ok, _ = await onchain._buy_core(1, 7, 5, 10_000_000, "ru")
    assert ok is False


@pytest.mark.asyncio
async def test_buy_core_insufficient_funds(onchain_env, market_getter, market_row):
    market_getter["row"] = market_row
    ok, _ = await onchain._buy_core(1, 7, 0, 999_000_000, "ru")
    assert ok is False


@pytest.mark.asyncio
async def test_buy_core_closed_deadline(onchain_env, market_getter, market_row):
    market_row["close_at"] = int(time.time()) - 1
    market_getter["row"] = market_row
    ok, _ = await onchain._buy_core(1, 7, 0, 10_000_000, "ru")
    assert ok is False


@pytest.mark.asyncio
async def test_sell_core_success(onchain_env, market_getter, market_row, monkeypatch):
    market_getter["row"] = market_row
    contract_mock = Mock()
    contract_mock.functions.balanceOf.return_value.call.return_value = 100
    monkeypatch.setattr(onchain.om, "_market_contract", lambda w3: contract_mock)
    monkeypatch.setattr(onchain.om, "_w3", lambda: object())
    ok, _ = await onchain._sell_core(1, 7, 0, 50, "ru")
    assert ok is True
    assert onchain_env["sells"] == 1


@pytest.mark.asyncio
async def test_sell_core_no_shares(onchain_env, market_getter, market_row, monkeypatch):
    market_getter["row"] = market_row
    contract_mock = Mock()
    contract_mock.functions.balanceOf.return_value.call.return_value = 0
    monkeypatch.setattr(onchain.om, "_market_contract", lambda w3: contract_mock)
    monkeypatch.setattr(onchain.om, "_w3", lambda: object())
    ok, _ = await onchain._sell_core(1, 7, 0, 50, "ru")
    assert ok is False
