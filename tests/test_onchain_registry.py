"""On-chain market registry: metadata, trade log, resolution lifecycle.

The registry is off-chain bookkeeping only (labels + winner DMs); real
holdings live in ERC-1155. These tests pin the invariants the bot relies on:
idempotent registry writes, per-outcome aggregation for winner notifications,
and the deadline/overdue queries driving the onchain watcher.
"""

import os
import time

import pytest

from bot.ledger import Ledger

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5432/tipbot_test"
)

CREATOR, BUYER_A, BUYER_B = 7101, 7102, 7103


@pytest.fixture()
def ledger():
    """One Ledger per test, always closed: an unclosed connection idles in
    transaction and blocks the next Ledger()'s schema DDL with
    LockNotAvailable (ACCESS EXCLUSIVE vs a held ACCESS SHARE)."""
    ledger = Ledger(TEST_DB_URL)
    ledger._conn.execute("TRUNCATE onchain_markets, onchain_trades RESTART IDENTITY")
    ledger._conn.commit()
    yield ledger
    ledger.close()


def test_registry_save_is_idempotent(ledger):
    ledger.save_onchain_market(1, CREATOR, "Who wins?", ["Alice", "Bob"], int(time.time()) + 3600)
    ledger.save_onchain_market(1, CREATOR, "Who wins?", ["Alice", "Bob"], int(time.time()) + 3600)

    m = ledger.get_onchain_market(1)
    assert m["creator"] == CREATOR
    assert m["resolved_outcome"] is None
    assert m["cancelled_flag"] == 0
    assert len(ledger.list_onchain_markets(10)) == 1  # ON CONFLICT DO NOTHING


def test_deadline_and_overdue_queries(ledger):
    now = int(time.time())
    ledger.save_onchain_market(1, CREATOR, "open", ["A", "B"], now + 3600)
    ledger.save_onchain_market(2, CREATOR, "closed-unresolved", ["A", "B"], now - 7200)
    ledger.save_onchain_market(3, BUYER_A, "closed-resolved", ["A", "B"], now - 7200)
    ledger.save_onchain_market(4, CREATOR, "closed-cancelled", ["A", "B"], now - 7200)
    ledger.save_onchain_market(5, CREATOR, "closed-notified", ["A", "B"], now - 7200)
    ledger.set_onchain_resolved(3, 0)
    ledger.mark_onchain_cancelled(4)
    ledger.mark_onchain_deadline_notified(5)
    ledger._conn.execute("UPDATE onchain_markets SET deadline_notified = 1 WHERE id = 3")
    ledger._conn.commit()

    past = {m["id"] for m in ledger.onchain_markets_past_deadline()}
    assert 1 not in past, "open market must not be in the deadline list"
    assert 2 in past and 5 not in past and 3 not in past and 4 not in past

    # Overdue = unresolved, not cancelled, close_at + grace <= now.
    # Margins of 200s keep the boundary assertions stable across second ticks.
    overdue_now = {m["id"] for m in ledger.onchain_markets_overdue(7000)}  # 2h ago + <2h
    assert overdue_now == {2, 5}, "resolved/cancelled markets must never be overdue"
    not_yet = {m["id"] for m in ledger.onchain_markets_overdue(7400)}  # window not reached
    assert not_yet == set()


def test_trades_aggregate_per_outcome(ledger):
    ledger.record_onchain_trade(1, BUYER_A, 0, 3_000_000, "0x" + "aa" * 32)
    ledger.record_onchain_trade(1, BUYER_A, 0, 2_000_000, "0x" + "bb" * 32)
    ledger.record_onchain_trade(1, BUYER_B, 1, 1_000_000, "0x" + "cc" * 32)

    outcome0 = {r["tg_id"]: int(r["shares"]) for r in ledger.onchain_trades_for_outcome(1, 0)}
    outcome1 = {r["tg_id"]: int(r["shares"]) for r in ledger.onchain_trades_for_outcome(1, 1)}
    assert outcome0 == {BUYER_A: 5_000_000}
    assert outcome1 == {BUYER_B: 1_000_000}
    # Non-winning outcome and unknown market stay isolated.
    assert ledger.onchain_trades_for_outcome(1, 7) == []
    assert ledger.onchain_trades_for_outcome(999, 0) == []


def test_lifecycle_flags_independent(ledger):
    ledger.save_onchain_market(9, CREATOR, "market", ["A", "B"], int(time.time()))
    ledger.mark_onchain_cancelled(9)
    assert ledger.get_onchain_market(9)["cancelled_flag"] == 1
    assert ledger.get_onchain_market(9)["resolved_outcome"] is None
    ledger.set_onchain_resolved(9, 1)
    assert ledger.get_onchain_market(9)["resolved_outcome"] == 1
    # Cancelled market must never appear in the deadline ping list again.
    assert ledger.onchain_markets_past_deadline() == []
