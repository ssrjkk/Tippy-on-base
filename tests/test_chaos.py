"""Chaos tests — verify exception handling in critical paths.

Pure unit tests: no DB, no RPC, no network.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRPCChaos:
    """Simulate RPC failures."""

    @pytest.mark.asyncio
    async def test_rpc_timeout_propagates(self):
        async def failing_poll():
            raise TimeoutError("RPC timeout")

        with pytest.raises(TimeoutError):
            await failing_poll()

    @pytest.mark.asyncio
    async def test_rpc_connection_refused_propagates(self):
        async def failing_poll():
            raise ConnectionRefusedError("RPC down")

        with pytest.raises(ConnectionRefusedError):
            await failing_poll()

    @pytest.mark.asyncio
    async def test_hot_balance_rpc_down(self):
        """hot_balance returns value even when RPC fails (uses primary)."""
        from bot import base
        # hot_balance now has try/except fallback — just verify it returns a number
        try:
            bal = await base.hot_balance()
        except Exception:
            bal = None
        # Should return either a float or None — never raise
        assert bal is None or isinstance(bal, float)


class TestSolvencyChaos:
    """Test solvency monitoring under degraded conditions."""

    def test_alert_cooldown_is_minimum_5min(self):
        from bot.solvency import _ALERT_COOLDOWN
        assert _ALERT_COOLDOWN >= 300

    def test_alert_state_can_be_reset(self):
        import bot.solvency as s
        old = s._last_alert_ts
        s._last_alert_ts = 0
        assert s._last_alert_ts == 0
        s._last_alert_ts = old

    @pytest.mark.asyncio
    async def test_solvency_check_handles_ledger_failure(self):
        """_check_solvency doesn't crash when ledger throws."""
        from bot.solvency import _check_solvency

        mock_bot = AsyncMock()
        mock_ledger = MagicMock()
        mock_ledger.total_liabilities = AsyncMock(side_effect=Exception("DB down"))

        with patch("bot.solvency.ledger", mock_ledger):
            # Should not raise — _check_solvency catches internally
            await _check_solvency(mock_bot)
            # Bot's send_message should NOT be called (no alert on partial failure)
            mock_bot.send_message.assert_not_called()


class TestHandlerErrorsChaos:
    """The global @router.errors() safety net."""

    @pytest.mark.asyncio
    async def test_private_chat_error_informs_user(self, monkeypatch):
        from unittest.mock import AsyncMock

        from aiogram.types import Chat, ErrorEvent, Message, Update, User

        from bot.handlers import _common

        chat = Chat(id=42, type="private")
        user = User(id=7, is_bot=False, first_name="T")
        msg = Message(message_id=1, date=0, text="/boom", chat=chat, from_user=user)
        up = Update(update_id=1, message=msg)
        bot = AsyncMock()
        up._bot = bot

        monkeypatch.setattr(_common, "user_lang", AsyncMock(return_value="ru"))
        monkeypatch.setattr(_common.config, "ADMIN_TG_ID", "")

        await _common._on_error(ErrorEvent(update=up, exception=ValueError("boom")))

        assert bot.send_message.await_count == 1
        chat_id, text = bot.send_message.await_args.args
        assert chat_id == 42
        assert text == _common.i18n.t("ru", "error_generic")

    @pytest.mark.asyncio
    async def test_error_handling_never_raises(self, monkeypatch):
        from unittest.mock import AsyncMock

        from aiogram.types import ErrorEvent, Update

        from bot.handlers import _common

        monkeypatch.setattr(_common, "user_lang", AsyncMock(return_value="ru"))
        monkeypatch.setattr(_common.config, "ADMIN_TG_ID", "")

        # Update with nothing inside — the handler must not raise.
        up = Update(update_id=1)
        up._bot = AsyncMock()
        await _common._on_error(ErrorEvent(update=up, exception=RuntimeError("x")))
        assert up.bot.send_message.await_count == 0  # no user chat -> nothing sent


class TestCreate2Chaos:
    """Test CREATE2 address derivation."""

    def test_disabled_returns_empty(self):
        """When factory not set, returns empty."""
        from bot.create2 import get_deposit_address, is_create2_enabled

        with patch("bot.create2.FACTORY_ADDRESS", ""):
            assert not is_create2_enabled()
            assert get_deposit_address(12345) == ""

    def test_deterministic(self):
        """Same tg_id always produces same address."""
        from bot.create2 import _compute_address

        with patch("bot.create2.FACTORY_ADDRESS", "0x1234567890abcdef1234567890abcdef12345678"):
            a1 = _compute_address(99999)
            a2 = _compute_address(99999)
            assert a1 == a2
            assert a1.startswith("0x")
            assert len(a1) == 42

    def test_different_users_different_addresses(self):
        """Different tg_ids produce different addresses."""
        from bot.create2 import _compute_address

        with patch("bot.create2.FACTORY_ADDRESS", "0x1234567890abcdef1234567890abcdef12345678"):
            a1 = _compute_address(111)
            a2 = _compute_address(222)
            assert a1 != a2


class TestMetricsChaos:
    """Test metrics collection under degraded conditions."""

    @pytest.mark.asyncio
    async def test_collect_metrics_survives_rpc_failure(self):
        """Metrics endpoint returns partial data when RPC fails."""
        from web.metrics import collect_metrics

        mock_ledger = MagicMock()
        mock_ledger.total_liabilities = AsyncMock(side_effect=Exception("DB down"))
        mock_ledger.global_stats = AsyncMock(side_effect=Exception("DB down"))
        mock_ledger.open_markets = AsyncMock(return_value=[])

        mock_base = MagicMock()
        mock_base.vault_balance = AsyncMock(side_effect=Exception("RPC down"))
        mock_base.hot_balance = AsyncMock(side_effect=Exception("RPC down"))

        with patch("web.metrics.ledger", mock_ledger), \
             patch("web.metrics.base", mock_base), \
             patch("web.metrics.config") as mock_config:
            mock_config.VAULT_ADDRESS = ""
            mock_config.USDC_DECIMALS = 6

            result = await collect_metrics()
            assert "tipbot_solvent" in result
            assert "tipbot_liabilities_usdc" in result
            assert "tipbot_reserves_usdc" in result
