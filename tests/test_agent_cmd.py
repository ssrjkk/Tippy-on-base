"""Tests for the /agent operator command (admin-only visibility)."""

import asyncio

import pytest

from bot import i18n
from bot.handlers import cmd_agent
from bot.handlers.agent import _audit_tail

run = asyncio.run

ALICE = 2001
ADMIN = 999


# ---------- mocks ----------

class User:
    def __init__(self, id, username=None):
        self.id = id
        self.username = username


class Message:
    def __init__(self, text="/agent", from_id=ALICE):
        self.text = text
        self.from_user = User(from_id)
        self.answers = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append(text)


@pytest.fixture()
def admin_cfg(monkeypatch):
    from bot import config as bot_config
    from bot.handlers import _common as common
    monkeypatch.setattr(common.config, "ADMIN_TG_ID", ADMIN)
    monkeypatch.setattr(bot_config, "ADMIN_TG_ID", ADMIN)


def _status(spent=1.5, actions=3, errors=0, cooldown=False):
    return {
        "daily_spent_usdc": spent,
        "daily_cap_usdc": 50.0,
        "actions_this_hour": actions,
        "max_actions_per_hour": 20,
        "consecutive_errors": errors,
        "cooldown_active": cooldown,
    }


def test_agent_rejects_non_admin(admin_cfg, monkeypatch):
    m = Message(from_id=ALICE)
    run(cmd_agent(m))
    assert "администратор" in m.answers[0] or "admin" in m.answers[0]


def test_agent_rejects_when_no_admin_configured(monkeypatch):
    from bot.handlers import _common as common
    monkeypatch.setattr(common.config, "ADMIN_TG_ID", None)
    m = Message(from_id=ADMIN)
    run(cmd_agent(m))
    assert m.answers  # answered with the admin-only message, no crash


def test_agent_disabled_message(admin_cfg, monkeypatch):
    from agent import config as agent_config
    monkeypatch.setattr(agent_config, "AGENT_TG_ID", 0)
    m = Message(from_id=ADMIN)
    run(cmd_agent(m))
    assert "AGENT_TG_ID=0" in m.answers[0]


def test_agent_shows_status(admin_cfg, monkeypatch):
    from agent import caps, config as agent_config
    monkeypatch.setattr(agent_config, "AGENT_TG_ID", 42)
    monkeypatch.setattr(caps, "get_status", lambda: _status(spent=7.25))
    m = Message(from_id=ADMIN)
    run(cmd_agent(m))
    text = m.answers[0]
    assert "42" in text          # agent tg_id in the header
    assert "7.25" in text        # daily spend


def test_agent_shows_circuit_breaker(admin_cfg, monkeypatch):
    from agent import caps, config as agent_config
    monkeypatch.setattr(agent_config, "AGENT_TG_ID", 42)
    monkeypatch.setattr(caps, "get_status", lambda: _status(errors=3, cooldown=True))
    m = Message(from_id=ADMIN)
    run(cmd_agent(m))
    assert "🔴" in m.answers[0]


def test_agent_shows_audit_tail(admin_cfg, monkeypatch, tmp_path):
    from agent import caps, config as agent_config
    import agent.config as ac

    audit = tmp_path / "agent_audit.jsonl"
    audit.write_text(
        '{"market_id": 1, "question": "Will BTC pump?", "bet_amount_usdc": 2.0, "confidence": 0.8}\n'
        '{"market_id": 2, "question": "ETH ATH by Friday?", "bet_amount_usdc": 3.5, "confidence": 0.6}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_config, "AGENT_TG_ID", 42)
    monkeypatch.setattr(ac, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(caps, "get_status", lambda: _status())
    m = Message(from_id=ADMIN)
    run(cmd_agent(m))
    text = m.answers[0]
    assert "#2" in text          # newest entry first
    assert "3.50" in text


def test_audit_tail_handles_missing_file(tmp_path):
    import agent.config as ac
    # _audit_tail reads the module-level STATE_DIR via a local import.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(ac, "STATE_DIR", str(tmp_path))
    try:
        assert _audit_tail() == []
    finally:
        monkey.undo()


def test_agent_never_crashes_on_caps_failure(admin_cfg, monkeypatch):
    from agent import caps, config as agent_config

    def _boom():
        raise RuntimeError("state corrupted")

    monkeypatch.setattr(agent_config, "AGENT_TG_ID", 42)
    monkeypatch.setattr(caps, "get_status", _boom)
    m = Message(from_id=ADMIN)
    run(cmd_agent(m))
    assert m.answers  # error surfaced as text, not an exception
