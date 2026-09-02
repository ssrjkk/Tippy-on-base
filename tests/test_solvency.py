"""Solvency monitor: reserves vs liabilities checks and alerts."""

from unittest.mock import AsyncMock

import pytest

from bot import solvency


@pytest.fixture(autouse=True)
def _reset_alert_state():
    solvency._last_alert_ts = 0.0
    yield
    solvency._last_alert_ts = 0.0


def _mock_owed(liabilities, pending):
    return liabilities + pending


@pytest.mark.asyncio
async def test_solvent_no_alert(monkeypatch):
    # reserves (100) > owed (50+10=60), no alert
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=100_000_000))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_insolvent_sends_alert(monkeypatch):
    # reserves (40) < owed (50+10=60), alert must fire
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40_000_000))
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
    # reserves (52) > owed (50), but buffer is only 4% (<5%), low margin alert
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=0))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=52_000_000))
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
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40_000_000))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()

    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1

    # Second call within cooldown — must not send again
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_rpc_down_skips_check(monkeypatch):
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(side_effect=ConnectionError("RPC down")))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    bot = AsyncMock()
    # Must not raise — RPC failure is swallowed
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_no_alert_chat_id(monkeypatch):
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.ledger, "pending_deposit_total", AsyncMock(return_value=10_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40_000_000))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()
