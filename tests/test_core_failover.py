"""core.get_transaction / get_transaction_receipt failover semantics.

A None from ONE provider is NOT authoritative: another RPC may still hold the
tx / have the block. The refund sweep relies on these functions to conclude
"dropped" ONLY when every provider is sure, and to raise (ambiguous) instead of
double-paying when the state cannot be established.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bot.chain import core

TX = {"hash": "0x" + "ab" * 32, "nonce": 0}


def _provider(get_tx=None, get_receipt=None, name="p"):
    eth = SimpleNamespace(
        get_transaction=get_tx or (lambda h: None),
        get_transaction_receipt=get_receipt or (lambda h: None),
    )
    p = SimpleNamespace(eth=eth, provider=SimpleNamespace(endpoint_uri=name))
    return p


@pytest.fixture(autouse=True)
def _isolated_pool(monkeypatch):
    monkeypatch.setattr(core, "w3", Mock(spec=[]))
    monkeypatch.setattr(core, "_w3_providers", [])
    monkeypatch.setattr(core, "_cb_fail_times", [])
    monkeypatch.setattr(core, "_cb_open_until", 0.0)
    yield


def test_get_transaction_any_provider_has_it_wins(monkeypatch):
    """First provider returns None (evicted/out of sync); a later one has it:
    the tx is pending, must be reported — never 'dropped'."""
    p1 = _provider(get_tx=lambda h: None)
    p2 = _provider(get_tx=lambda h: dict(TX))
    monkeypatch.setattr(core, "w3", p1)
    monkeypatch.setattr(core, "_w3_providers", [p2])
    assert core.get_transaction("0x" + "ab" * 32) == dict(TX)


def test_get_transaction_all_none_is_dropped(monkeypatch):
    p1 = _provider(get_tx=lambda h: None)
    p2 = _provider(get_tx=lambda h: None)
    monkeypatch.setattr(core, "w3", p1)
    monkeypatch.setattr(core, "_w3_providers", [p2])
    assert core.get_transaction("0x" + "ab" * 32) is None


def test_get_transaction_error_plus_none_raises(monkeypatch):
    """One provider errors, the other reports None: the conclusion 'dropped'
    is not safe — raise so the refund sweep keeps the row pending."""
    p1 = _provider(get_tx=lambda h: (_ for _ in ()).throw(ConnectionError("down")))
    p2 = _provider(get_tx=lambda h: None)
    monkeypatch.setattr(core, "w3", p1)
    monkeypatch.setattr(core, "_w3_providers", [p2])
    with pytest.raises(RuntimeError):
        core.get_transaction("0x" + "ab" * 32)


def test_get_transaction_first_missing_second_errors_raises(monkeypatch):
    p1 = _provider(get_tx=lambda h: None)
    p2 = _provider(get_tx=lambda h: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr(core, "w3", p1)
    monkeypatch.setattr(core, "_w3_providers", [p2])
    with pytest.raises(RuntimeError):
        core.get_transaction("0x" + "ab" * 32)


def test_receipt_any_provider_has_it_wins(monkeypatch):
    """A lagging primary missing the block must not hide a mined receipt."""
    rec = {"status": 1, "transactionHash": "0x" + "ab" * 32}
    p1 = _provider(get_receipt=lambda h: None)
    p2 = _provider(get_receipt=lambda h: dict(rec))
    monkeypatch.setattr(core, "w3", p1)
    monkeypatch.setattr(core, "_w3_providers", [p2])
    assert core.get_transaction_receipt("0x" + "ab" * 32) == dict(rec)


def test_receipt_all_none_is_unmined(monkeypatch):
    p1 = _provider(get_receipt=lambda h: None)
    p2 = _provider(get_receipt=lambda h: None)
    monkeypatch.setattr(core, "w3", p1)
    monkeypatch.setattr(core, "_w3_providers", [p2])
    assert core.get_transaction_receipt("0x" + "ab" * 32) is None


def test_receipt_error_plus_none_raises(monkeypatch):
    p1 = _provider(get_receipt=lambda h: (_ for _ in ()).throw(ConnectionError("down")))
    p2 = _provider(get_receipt=lambda h: None)
    monkeypatch.setattr(core, "w3", p1)
    monkeypatch.setattr(core, "_w3_providers", [p2])
    with pytest.raises(RuntimeError):
        core.get_transaction_receipt("0x" + "ab" * 32)
