"""Notification outbox lifecycle: enqueue/dequeue/ack/retry."""

import time


def test_enqueue_dequeue_ack(ledger):
    nid = ledger.enqueue_notification(123, "hello")
    assert nid > 0
    items = ledger.dequeue_notifications()
    assert len(items) == 1
    assert items[0]["chat_id"] == 123
    assert items[0]["text"] == "hello"
    ledger.ack_notification(nid)
    assert ledger.dequeue_notifications() == []


def test_retry_schedules_future(ledger):
    nid = ledger.enqueue_notification(123, "retry me")
    before = time.time()
    ledger.retry_notification(nid, 30)
    items = ledger.dequeue_notifications()
    assert items == []  # not due yet
    next_at = ledger._conn.execute(
        "SELECT next_retry_at FROM notification_outbox WHERE id = %s", (nid,)
    ).fetchone()["next_retry_at"]
    assert next_at >= before + 30


def test_dequeue_only_due(ledger):
    ledger.enqueue_notification(1, "due")
    nid2 = ledger.enqueue_notification(2, "later")
    ledger.retry_notification(nid2, 99999)
    items = ledger.dequeue_notifications()
    assert [i["chat_id"] for i in items] == [1]


def test_ack_fk_no_dangling(ledger):
    nid = ledger.enqueue_notification(5, "gone")
    ledger.ack_notification(nid)
    assert ledger.dequeue_notifications() == []
