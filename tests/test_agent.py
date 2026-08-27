"""Tests for the agent package — caps, tools, news, decision, EAS.

Runs against the test database like the other test files.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# caps.py
# ---------------------------------------------------------------------------

class TestCaps:
    def setup_method(self):
        self.state_file = Path("agent/.agent_state.json")
        if self.state_file.exists():
            self.state_file.unlink()

    def test_check_action_within_limits(self):
        from agent.caps import check_action
        result = check_action(1.0)
        assert result is None  # allowed

    def test_check_action_daily_cap(self):
        from agent.caps import _save_state, check_action
        _save_state({
            "daily_spent": 49.0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        result = check_action(2.0)  # would exceed $50
        assert result is not None
        assert "Daily cap" in result

    def test_check_action_per_tx_cap(self):
        from agent.caps import check_action
        result = check_action(100.0)  # > $10 per-tx
        assert result is not None
        assert "Per-tx cap" in result

    def test_check_action_rate_limit(self):
        from agent.caps import _save_state, check_action
        _save_state({
            "daily_spent": 0.0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 20,
            "hour_ts": int(time.time()) // 3600,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        result = check_action(1.0)
        assert result is not None
        assert "Rate limit" in result

    def test_record_action_updates_state(self):
        from agent.caps import _load_state, record_action
        record_action(5.0)
        state = _load_state()
        assert state["daily_spent"] == 5.0
        assert state["actions_this_hour"] == 1
        assert state["consecutive_errors"] == 0

    def test_record_error_triggers_breaker(self):
        from agent.caps import _load_state, record_error
        for _ in range(3):
            record_error()
        state = _load_state()
        assert state["cooldown_until"] > time.time()

    def test_get_status(self):
        from agent.caps import get_status
        s = get_status()
        assert "daily_spent_usdc" in s
        assert "cooldown_active" in s

    def teardown_method(self):
        if self.state_file.exists():
            self.state_file.unlink()


# ---------------------------------------------------------------------------
# news.py
# ---------------------------------------------------------------------------

class TestNews:
    def test_score_relevance(self):
        from agent.news import _score_relevance
        high = _score_relevance("Bitcoin ETF approved by SEC", "")
        low = _score_relevance("Free airdrop giveaway moon 100x", "")
        assert high > low

    def test_news_item_to_prompt(self):
        from agent.news import NewsItem
        item = NewsItem(
            title="ETH hits $5000",
            link="https://example.com",
            published="",
            source="test",
            relevance=0.8,
        )
        prompt = item.to_prompt()
        assert "<untrusted_news_item>" in prompt
        assert "ETH hits $5000" in prompt
        assert "</untrusted_news_item>" in prompt

    def test_dedup(self):
        from agent.news import _load_seen, _save_seen
        seen = _load_seen()
        initial_len = len(seen)
        seen.add("test_hash_123")
        _save_seen(seen)
        seen2 = _load_seen()
        assert "test_hash_123" in seen2
        # Cleanup
        seen2.discard("test_hash_123")
        _save_seen(seen2)


# ---------------------------------------------------------------------------
# tools.py (mocked ledger)
# ---------------------------------------------------------------------------

class TestTools:
    @pytest.mark.asyncio
    async def test_create_market_blocked_by_cap(self):
        from agent.caps import _save_state
        _save_state({
            "daily_spent": 49.0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        from agent.tools import create_market
        result = await create_market("Test?", ["Yes", "No"], hours=24, subsidy_usdc=2.0)
        assert "error" in result
        # Cleanup
        Path("agent/.agent_state.json").unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_place_bet_blocked_by_cap(self):
        from agent.caps import _save_state
        _save_state({
            "daily_spent": 0.0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        from agent.tools import place_bet
        result = await place_bet(1, 0, 100.0)  # > $10 per-tx
        assert "error" in result
        Path("agent/.agent_state.json").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# signals.py
# ---------------------------------------------------------------------------

class TestSignals:
    @pytest.mark.asyncio
    async def test_sell_signal_blocked_by_cap(self):
        from agent.caps import _save_state
        _save_state({
            "daily_spent": 0.0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 20,
            "hour_ts": int(time.time()) // 3600,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        from agent.signals import sell_signal
        result = await sell_signal(1, "analysis", price_usdc=1.0)
        assert "error" in result
        Path("agent/.agent_state.json").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# decision.py (mocked LLM)
# ---------------------------------------------------------------------------

class TestDecision:
    def test_decide_returns_none_when_no_create(self):
        from agent.decision import _call_llm
        with patch("agent.decision.os.environ.get", return_value=""):
            result = _call_llm(["test news"], 100.0)
            assert result.get("create_market") is False

    def test_decide_validates_output(self):
        from agent.decision import decide
        with patch("agent.decision._call_llm") as mock:
            mock.return_value = {
                "create_market": True,
                "question": "Will ETH hit $5000?",
                "options": ["Yes", "No"],
                "hours": 24,
                "bet_outcome": 0,
                "bet_amount_usdc": 5.0,
                "confidence": 0.7,
                "reasoning": "Bullish trend",
            }
            result = decide(["<untrusted>test</untrusted>"], 100.0)
            assert result is not None
            assert result.question == "Will ETH hit $5000?"
            assert result.options == ["Yes", "No"]
            assert result.bet_amount_usdc == 5.0

    def test_decide_enforces_caps(self):
        from agent.decision import decide
        with patch("agent.decision._call_llm") as mock:
            mock.return_value = {
                "create_market": True,
                "question": "Test?",
                "options": ["A", "B"],
                "hours": 24,
                "bet_outcome": 0,
                "bet_amount_usdc": 100.0,  # > $10 cap
                "confidence": 0.9,
                "reasoning": "test",
            }
            result = decide(["test"], 100.0)
            assert result is not None
            assert result.bet_amount_usdc == 10.0  # capped to PER_TX_CAP_USDC


# ---------------------------------------------------------------------------
# eas.py
# ---------------------------------------------------------------------------

class TestEAS:
    def test_attestation_data_encode(self):
        from agent.eas import AttestationData
        data = AttestationData(
            action_type="place_bet",
            market_id=42,
            amount_micro=5_000_000,
            confidence=75,
            reasoning="Bullish momentum",
        )
        encoded = data.encode_data()
        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

    def test_reasoning_hash(self):
        from agent.eas import AttestationData
        data = AttestationData(
            action_type="create_market",
            market_id=1,
            amount_micro=10_000_000,
            confidence=80,
            reasoning="Test reasoning",
        )
        h = data.reasoning_hash
        assert isinstance(h, bytes)
        assert len(h) == 32

    def test_attest_action_local_fallback(self):
        from agent.eas import AttestationData, attest_action
        data = AttestationData(
            action_type="create_market",
            market_id=99,
            amount_micro=10_000_000,
            confidence=50,
            reasoning="Fallback test",
        )
        # Without AGENT_EAS_KEY, should log locally
        result = attest_action(data)
        assert result is None  # local fallback
        # Check local log was created
        log_file = Path("agent_attestations.jsonl")
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) > 0
        last = json.loads(lines[-1])
        assert last["market_id"] == 99
        # Cleanup
        log_file.unlink(missing_ok=True)
