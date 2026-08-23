"""End-to-end agent cycle test — full perceive → decide → act → attest.

Mocks the LLM and ledger to test the complete agent loop without
external dependencies.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from agent.decision import MarketDecision
from agent.tools import _agent_markets


@pytest.fixture(autouse=True)
def _clean_state():
    """Clean agent state before and after each test."""
    state = Path("agent/.agent_state.json")
    audit = Path("agent_audit.jsonl")
    attest = Path("agent_attestations.jsonl")
    if state.exists():
        state.unlink()
    yield
    if state.exists():
        state.unlink()
    if audit.exists():
        audit.unlink()
    if attest.exists():
        attest.unlink()
    _agent_markets.clear()


class TestE2EAgentCycle:
    @pytest.mark.asyncio
    async def test_full_cycle_mock(self):
        """Full cycle: news → LLM filter → LLM decide → create_market → place_bet → sell_signal."""
        from agent.main import single_cycle
        from agent import caps

        # Mock news
        mock_news = MagicMock()
        mock_news.title = "Bitcoin breaks $100k"
        mock_news.link = "https://example.com"
        mock_news.source = "test"
        mock_news.relevance = 0.9
        mock_news.to_prompt.return_value = "<untrusted_news_item>Title: Bitcoin breaks $100k\n</untrusted_news_item>"

        # Mock LLM filter → relevant
        filter_response = json.dumps({"relevant": True, "reason": "crypto price event"}).encode()
        # Mock LLM decide → create market + bet
        decide_response = json.dumps({
            "create_market": True,
            "question": "Will BTC stay above $100k for 24h?",
            "options": ["Yes", "No"],
            "hours": 24,
            "bet_outcome": 0,
            "bet_amount_usdc": 5.0,
            "confidence": 0.7,
            "reasoning": "Strong momentum, institutional buying",
        }).encode()

        call_count = 0

        def mock_urlopen(req, timeout=30):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                # Filter call
                resp.read.return_value = json.dumps({
                    "choices": [{"message": {"content": '{"relevant": true, "reason": "crypto"}'}}]
                }).encode()
            else:
                # Decision call
                resp.read.return_value = json.dumps({
                    "choices": [{"message": {"content": decide_response.decode()}}]
                }).encode()
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        # Mock ledger
        mock_ledger = AsyncMock()
        mock_ledger.create_market.return_value = 42
        mock_ledger.buy_shares.return_value = ("ok", {"cost": 5_000_000})
        mock_ledger.balance.return_value = 95_000_000
        mock_ledger.create_paywall.return_value = 99

        with patch("agent.news.fetch_news", return_value=[mock_news]), \
             patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("agent.tools.ledger", mock_ledger), \
             patch("agent.signals.ledger", mock_ledger), \
             patch("bot.ledger.ledger", mock_ledger):
            result = await single_cycle()

        assert result is True
        # Verify market was created
        mock_ledger.create_market.assert_called_once()
        # Verify bet was placed
        mock_ledger.buy_shares.assert_called_once()
        # Verify paywall was created (signal sold)
        mock_ledger.create_paywall.assert_called_once()

        # Verify local audit trail
        audit_file = Path("agent_audit.jsonl")
        assert audit_file.exists()
        entries = [json.loads(l) for l in audit_file.read_text().splitlines() if l.strip()]
        assert len(entries) == 1
        assert entries[0]["market_id"] == 42

        # Verify EAS attestation log
        attest_file = Path("agent_attestations.jsonl")
        assert attest_file.exists()

    @pytest.mark.asyncio
    async def test_oracle_protection_blocks_bet(self):
        """Agent cannot bet on its own markets."""
        from agent.tools import create_market, place_bet, _agent_markets

        mock_ledger = AsyncMock()
        mock_ledger.create_market.return_value = 100

        with patch("agent.tools.ledger", mock_ledger):
            # Create market
            result = await create_market("Test?", ["A", "B"])
            assert result["market_id"] == 100
            assert 100 in _agent_markets

            # Try to bet on own market — should be blocked
            result = await place_bet(100, 0, 5.0)
            assert "error" in result
            assert "Oracle protection" in result["error"]

    @pytest.mark.asyncio
    async def test_caps_prevent_over_spend(self):
        """Agent respects daily cap."""
        from agent.tools import create_market

        # Set state to near daily cap
        from agent.caps import _save_state
        _save_state({
            "daily_spent": 48.0,
            "daily_date": time.strftime("%Y-%m-%d", time.gmtime()),
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })

        result = await create_market("Test?", ["A", "B"], subsidy_usdc=5.0)
        assert "error" in result
        assert "Daily cap" in result["error"]

    @pytest.mark.asyncio
    async def test_circuit_breaker_after_errors(self):
        """Agent enters cooldown after 3 consecutive errors."""
        from agent.caps import record_error, get_status

        for _ in range(3):
            record_error()

        status = get_status()
        assert status["cooldown_active"] is True

    @pytest.mark.asyncio
    async def test_news_filter_rejects_irrelevant(self):
        """Cheap model filters irrelevant news, nothing happens."""
        from agent.main import single_cycle

        mock_news = MagicMock()
        mock_news.title = "Free airdrop giveaway"
        mock_news.relevance = 0.1
        mock_news.to_prompt.return_value = "<untrusted>test</untrusted>"

        # Filter rejects
        filter_resp = json.dumps({"relevant": False, "reason": "spam"}).encode()

        def mock_urlopen(req, timeout=30):
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '{"relevant": false, "reason": "spam"}'}}]
            }).encode()
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("agent.news.fetch_news", return_value=[mock_news]), \
             patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await single_cycle()

        # Nothing happened — no market created
        assert result is False
