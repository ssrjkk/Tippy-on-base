"""Tests for the /basename command (on-chain identity via Basenames).

Chain lookups are monkeypatched at bot.base (the module the handler reaches
through handlers._common.base); ledger calls hit the hermetic test database.
"""

import asyncio

import pytest

from bot import base as bot_base
from bot import tip_targets
from bot.handlers import cb_basename, cmd_basename
from bot.handlers.basename import _clean

run = asyncio.run

ALICE = 2001


# ---------- mocks (same shape as test_handlers; no cross-test import) ----------

class User:
    def __init__(self, id, username=None):
        self.id = id
        self.username = username


class Bot:
    def __init__(self):
        self.sent = []
        self.username = "base_tipbot"


class Message:
    def __init__(self, text="", from_id=ALICE, username="alice"):
        self.text = text
        self.from_user = User(from_id, username)
        self.bot = Bot()
        self.answers = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))


class Msg:
    """CallbackQuery.message stand-in."""
    def __init__(self):
        self.edited = []

    async def edit_text(self, text=None, **kw):
        self.edited.append(text)


class Callback:
    def __init__(self, data, from_id=ALICE):
        self.data = data
        self.from_user = User(from_id)
        self.message = Msg()
        self.answered = False

    async def answer(self, text=None, **kw):
        self.answered = True


@pytest.fixture()
def no_reverse(monkeypatch):
    """Default: the user's wallet reverse-resolves to no basename."""
    monkeypatch.setattr(tip_targets, "display_name_for", lambda tg_id: _async(None))
    monkeypatch.setattr(bot_base, "basename_available_sync", lambda name: None)
    monkeypatch.setattr(bot_base, "resolve_basename_sync", lambda name: None)


def _async(value):
    async def _inner(*a, **kw):
        return value
    return _inner()


# ---------- input hygiene ----------

def test_clean_strips_at_and_case():
    assert _clean("@MyName.base.eth") == "myname.base.eth"
    assert _clean("  bob.base.eth\n") == "bob.base.eth"


# ---------- /basename (no args): show my name ----------

def test_basename_shows_my_name(monkeypatch, ledger, no_reverse):
    monkeypatch.setattr(tip_targets, "display_name_for", lambda tg_id: _async("alice.base.eth"))
    m = Message("/basename")
    run(cmd_basename(m))
    text = m.answers[0][0]
    assert "alice." in text  # _esc truncates long tokens with "…"
    assert "basename_mine" not in text


def test_basename_none_when_no_name(ledger, no_reverse):
    m = Message("/basename")
    run(cmd_basename(m))
    text = m.answers[0][0]
    assert "basenames.app" in text  # hint to register


# ---------- /basename <name>: availability ----------

def test_basename_invalid_format(ledger, no_reverse):
    m = Message("/basename notaname")
    run(cmd_basename(m))
    assert "myname.base.eth" in m.answers[0][0] or "/basename" in m.answers[0][0]


def test_basename_free(monkeypatch, ledger, no_reverse):
    monkeypatch.setattr(bot_base, "basename_available_sync", lambda name: True)
    m = Message("/basename freename.base.eth")
    run(cmd_basename(m))
    text = m.answers[0][0]
    assert "freena" in text  # _esc truncates long tokens with "…"
    assert "✅" in text


def test_basename_taken_other_owner(monkeypatch, ledger, no_reverse):
    monkeypatch.setattr(bot_base, "basename_available_sync", lambda name: False)
    monkeypatch.setattr(bot_base, "resolve_basename_sync", lambda name: "0x" + "99" * 20)
    m = Message("/basename taken.base.eth")
    run(cmd_basename(m))
    text = m.answers[0][0]
    assert "❌" in text
    assert "0x9999" in text  # owner address (truncated by _esc)


def test_basename_owned_by_linked_wallet(monkeypatch, ledger, no_reverse):
    # ALICE links an external wallet; the name resolves to that address.
    from bot import ledger as ledger_mod
    addr = "0x" + "44" * 20
    nonce = ledger_mod.ledger.new_link_nonce(2001, addr)
    ledger_mod.ledger.confirm_link(2001, addr, nonce)
    monkeypatch.setattr(bot_base, "basename_available_sync", lambda name: False)
    monkeypatch.setattr(bot_base, "resolve_basename_sync", lambda name: addr)
    m = Message("/basename mine.base.eth", from_id=2001)
    run(cmd_basename(m))
    text = m.answers[0][0]
    assert "mine.b" in text  # _esc truncates long tokens with "…"
    assert "❌" not in text


def test_basename_rpc_error(monkeypatch, ledger, no_reverse):
    # Resolver returns None for availability → RPC problem, not "free".
    monkeypatch.setattr(bot_base, "basename_available_sync", lambda name: None)
    m = Message("/basename glitch.base.eth")
    run(cmd_basename(m))
    assert "RPC" in m.answers[0][0] or "недоступен" in m.answers[0][0]


# ---------- menu button (callback) ----------

def test_basename_menu_button_with_name(monkeypatch, ledger, no_reverse):
    monkeypatch.setattr(tip_targets, "display_name_for", lambda tg_id: _async("alice.base.eth"))
    cb = Callback('basename')
    run(cb_basename(cb))
    assert "alice." in cb.message.edited[0]
    assert cb.answered


def test_basename_menu_button_without_name(ledger, no_reverse):
    cb = Callback('basename')
    run(cb_basename(cb))
    assert "basenames.app" in cb.message.edited[0]
    assert cb.answered
