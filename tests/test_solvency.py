"""Solvency monitor: reserves vs liabilities checks and alerts."""

from unittest.mock import AsyncMock

import pytest

from bot import solvency


@pytest.fixture(autouse=True)
def _reset_alert_state():
    solvency._last_alert_ts = 0.0
    yield
    solvency._last_alert_ts = 0.0


@pytest.fixture(autouse=True)
def _mock_x402_reads(monkeypatch):
    """Keep the x402 reserve addends hermetic: no RPC, no DB in these tests."""
    monkeypatch.setattr(
        solvency.base, "receive_pool_balance", AsyncMock(return_value=0.0)
    )
    monkeypatch.setattr(
        solvency.ledger, "x402_unswept_credit_total", AsyncMock(return_value=0)
    )


def _mock_owed(liabilities, pending):
    return liabilities + pending


@pytest.mark.asyncio
async def test_solvent_no_alert(monkeypatch):
    # reserves (100.0 USDC float, as base.hot_balance returns) > owed (50+10=60 USDC)
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=100.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_float_reserves_no_false_insolvency(monkeypatch):
    """Regression: base returns float dollars, ledger returns micro ints.
    Old code compared dollars to micro -> permanent false INSOLVENCY."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=100.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_insolvent_sends_alert(monkeypatch):
    # reserves (40.0) < owed (50+10=60), alert must fire
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_called_once()
    chat_id = bot.send_message.call_args[0][0]
    assert chat_id == "-100123"
    text = bot.send_message.call_args[0][1]
    assert "INSOLVENCY DETECTED" in text


@pytest.mark.asyncio
async def test_low_margin_alert(monkeypatch):
    # reserves (52.0) > owed (50), but buffer is only 4% (<5%), low margin alert
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=0))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=52.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args[0][1]
    assert "LOW MARGIN" in text


@pytest.mark.asyncio
async def test_cooldown_prevents_spam(monkeypatch):
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()

    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1

    # Second call within cooldown — must not send again
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_rpc_down_alerts_blind_canary(monkeypatch):
    """Regression: RPC failure must NOT silently skip — a canary that cannot
    see reserves is itself an emergency and must alert the operator."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(side_effect=ConnectionError("RPC down")))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args[0][1]
    assert "SOLVENCY CHECK BLIND" in text


@pytest.mark.asyncio
async def test_x402_pool_counts_toward_reserves(monkeypatch):
    """x402 receive pool backs booked liabilities but sits outside the hot
    wallet: it must count toward reserves or the canary false-alarms."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=0))
    # hot has 50 USDC, pool has 13 USDC -> 63 >= 60 (5% margin), solvent (no alert)
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=50.0))
    monkeypatch.setattr(
        solvency.base, "receive_pool_balance", AsyncMock(return_value=13.0)
    )
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_x402_unswept_derived_counts_toward_reserves(monkeypatch):
    """Credited-but-not-yet-swept x402 invoices are a liability AND the funds
    are still parked in the derived address: count them as reserve."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=0))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=50.0))
    monkeypatch.setattr(
        solvency.ledger, "x402_unswept_credit_total", AsyncMock(return_value=13_000_000)
    )
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_x402_pool_does_not_mask_real_insolvency(monkeypatch):
    """Pool addend helps, but genuine insolvency on the hot side still alerts."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=80_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=0))
    # hot 55 + pool 15 = 70 < 80 -> insolvent
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=55.0))
    monkeypatch.setattr(
        solvency.base, "receive_pool_balance", AsyncMock(return_value=15.0)
    )
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args[0][1]
    assert "INSOLVENCY DETECTED" in text


@pytest.mark.asyncio
async def test_no_alert_chat_id(monkeypatch):
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()
