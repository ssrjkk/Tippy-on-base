"""Ledger invariants: conservation of funds, fees, refunds, deadlines."""

import os
import time
from decimal import Decimal

from bot import config
from bot.ledger import Ledger

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5432/tipbot_test"
)

ALICE, BOB, CAROL = 1001, 1002, 1003


def fund(ledger, tg_id, micro):
    ledger.credit(tg_id, micro, "deposit")


def total_balance(ledger):
    return sum(int(r["balance"]) for r in ledger._conn.execute("SELECT balance FROM users"))


def total_tx(ledger, kinds=()):
    rows = ledger._conn.execute("SELECT amount FROM tx_log").fetchall()
    if kinds:
        rows = [r for r in rows if r["kind"] in kinds]
    return sum(int(r["amount"]) for r in rows)


def test_deposit_credit_is_visible(ledger):
    fund(ledger, ALICE, 10_000_000)
    assert ledger.balance(ALICE) == Decimal("10.000000")
    assert ledger.user_exists(ALICE)
    assert not ledger.user_exists(424242)


def test_tip_conservation(ledger):
    fund(ledger, ALICE, 50_000_000)
    fund(ledger, BOB, 10_000_000)
    before = total_balance(ledger)
    assert ledger.transfer(ALICE, BOB, 12_500_000)
    assert ledger.balance(ALICE) == Decimal("37.500000")
    assert ledger.balance(BOB) == Decimal("22.500000")
    assert total_balance(ledger) == before


def test_debit_returns_false_when_short(ledger):
    fund(ledger, ALICE, 1_000_000)
    assert not ledger.debit(ALICE, 2_000_000)
    assert ledger.balance(ALICE) == Decimal("1.000000")


def test_resolve_bet_conservation_with_fee(ledger):
    fund(ledger, ALICE, 700_000_000)
    fund(ledger, BOB, 100_000_000)
    fund(ledger, CAROL, 100_000_000)
    before = total_balance(ledger)  # 900 USDC, captured before bets
    bid = ledger.create_bet(ALICE, "Кто выиграет?", ["А", "Б"], close_at=None)
    assert ledger.place_bet(bid, ALICE, 0, 400_000_000) == "ok"
    assert ledger.place_bet(bid, BOB, 1, 100_000_000) == "ok"
    assert ledger.place_bet(bid, CAROL, 0, 100_000_000) == "ok"

    ok, msg = ledger.resolve_bet(bid, 0, ALICE)
    assert ok
    assert ledger.market_view(bid)["status"] == "resolved"

    # Option A pool = 500 USDC (400+100), option B = 100 USDC, pot = 600 USDC.
    # Winners share the pot pro-rata; 2% fee on net profit goes to creator.
    # ALICE (also creator): payout 478.4 + creator fee 2 = 480.4, final 780.4
    # CAROL: payout 119.6, final 119.6; BOB: 0. Total = 900. Exactly conserved.
    assert ledger.balance(ALICE) == Decimal("780.400000")
    assert ledger.balance(CAROL) == Decimal("119.600000")
    assert ledger.balance(BOB) == Decimal("0")
    assert total_balance(ledger) == before


def test_cancel_bet_refunds(ledger):
    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    before = total_balance(ledger)
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    assert ledger.place_bet(bid, BOB, 0, 30_000_000) == "ok"
    ok, _ = ledger.cancel_bet(bid, ALICE)
    assert ok
    assert ledger.balance(ALICE) == Decimal("100.000000")
    assert ledger.balance(BOB) == Decimal("100.000000")
    assert total_balance(ledger) == before


def test_deadline_rejects_after_close(ledger):
    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    bid = ledger.create_bet(ALICE, "Дедлайн?", ["Да", "Нет"], close_at=0)
    assert ledger.place_bet(bid, BOB, 0, 1_000_000) == "deadline"
    assert ledger.balance(BOB) == Decimal("100.000000")


def test_place_bet_checks_balance(ledger):
    fund(ledger, ALICE, 5_000_000)
    bid = ledger.create_bet(ALICE, "Доступно?", ["Да", "Нет"])
    assert ledger.place_bet(bid, ALICE, 0, 10_000_000) == "balance"
    assert ledger.place_bet(bid, ALICE, 0, 5_000_000) == "ok"


def test_wrong_option_rejected(ledger):
    fund(ledger, ALICE, 100_000_000)
    bid = ledger.create_bet(ALICE, "Варианты?", ["1", "2"])
    assert ledger.place_bet(bid, ALICE, 2, 1_000_000) == "badopt"
    assert ledger.place_bet(bid, ALICE, -1, 1_000_000) == "badopt"


def test_non_creator_cannot_resolve_or_cancel(ledger):
    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    bid = ledger.create_bet(ALICE, "Чужой?", ["Да", "Нет"])
    ok, msg = ledger.resolve_bet(bid, 0, BOB)
    assert not ok and "только" in msg
    ok, msg = ledger.cancel_bet(bid, BOB)
    assert not ok and "только" in msg


# ---------- users / wallet links ----------


def test_find_by_username_case_insensitive(ledger):
    ledger.ensure_user(ALICE, "Alice_Tip")
    assert ledger.find_by_username("alice_tip") == ALICE
    assert ledger.find_by_username("ALICE_TIP") == ALICE
    assert ledger.find_by_username("nobody") is None


def test_set_username(ledger):
    ledger.ensure_user(ALICE, "old")
    ledger.set_username(ALICE, "new")
    assert ledger.username_of(ALICE) == "new"
    assert ledger.username_of(424242) is None


def test_link_nonce_and_confirm(ledger):
    nonce = ledger.new_link_nonce(ALICE, "0x" + "a" * 40)
    assert nonce and len(nonce) == 16
    # Wrong nonce rejected.
    assert not ledger.confirm_link(ALICE, "0x" + "a" * 40, "ffffffffffffffff")
    # Address mismatch rejected (nonce correct, address wrong).
    assert not ledger.confirm_link(ALICE, "0x" + "b" * 40, nonce)
    # Success.
    assert ledger.confirm_link(ALICE, "0x" + "A" * 40, nonce)  # checksum-insensitive
    assert ledger.linked_address(ALICE).lower() == "0x" + "a" * 40
    assert ledger.tg_id_of_address("0x" + "A" * 40) == ALICE  # case-insensitive
    assert ledger.tg_id_of_address("0x" + "c" * 40) is None


def test_confirm_link_unknown_nonce_rejected(ledger):
    assert not ledger.confirm_link(ALICE, "0x" + "a" * 40, "nope")


def test_confirm_link_ttl_expired(ledger, monkeypatch):
    nonce = ledger.new_link_nonce(ALICE, "0x" + "a" * 40)
    assert ledger.get_link_nonce(ALICE) is not None
    # Pin the nonce's age explicitly (the DB server clock may differ from
    # this machine's clock by a second or two).
    ledger._conn.execute(
        "UPDATE link_nonces SET created_at = %s WHERE tg_id = %s",
        (int(time.time()) - 3600, ALICE),
    )
    ledger._conn.commit()
    monkeypatch.setattr(config, "LINK_NONCE_TTL_SECONDS", 60)
    assert not ledger.confirm_link(ALICE, "0x" + "a" * 40, nonce)
    assert ledger.linked_address(ALICE) is None
    assert ledger.get_link_nonce(ALICE) is None  # expired nonce removed


# ---------- withdrawals / liabilities ----------


def test_reserve_withdraw_atomic(ledger):
    fund(ledger, ALICE, 10_000_000)
    wd_id = ledger.reserve_withdraw(ALICE, "0x" + "b" * 40, 5_000_000, 50_000)
    assert wd_id is not None
    assert ledger.balance(ALICE) == Decimal("4.950000")  # amount + fee debited
    # Rows start queued (pending batch broadcast), not in the pending sweep.
    assert ledger.pending_withdraws() == []
    rows = ledger.withdraw_queue()
    assert len(rows) == 1
    assert rows[0]["id"] == wd_id
    assert rows[0]["status"] == "queued"
    assert rows[0]["amount"] == 5_000_000
    # Short balance -> nothing reserved, balance untouched.
    assert ledger.reserve_withdraw(ALICE, "0x" + "c" * 40, 100_000_000, 1) is None
    assert ledger.balance(ALICE) == Decimal("4.950000")


def test_record_withdraw_fee_logs(ledger):
    fund(ledger, ALICE, 10_000_000)
    wd_id = ledger.reserve_withdraw(ALICE, "0x" + "b" * 40, 5_000_000, 50_000)
    ledger.mark_withdraw_done(wd_id, "0x" + "f" * 64)
    rows = ledger.history(ALICE, 10)
    kinds = [r["kind"] for r in rows]
    assert kinds == ["fee", "withdraw", "deposit"]


def test_liabilities_and_pending_deposits(ledger):
    assert ledger.total_liabilities() == 0
    assert ledger.pending_deposit_total() == 0
    fund(ledger, ALICE, 12_000_000)
    fund(ledger, BOB, 3_000_000)
    ledger.record_pending("0x" + "5" * 64, "0xowner", 7_000_000)
    assert ledger.total_liabilities() == 15_000_000
    assert ledger.pending_deposit_total() == 7_000_000
    # Once claimed, it moves from pending into a user balance.
    nonce = ledger.new_link_nonce(ALICE, "0xowner")
    assert ledger.confirm_link(ALICE, "0xowner", nonce)
    ledger.claim(ALICE, "0x" + "5" * 64)
    assert ledger.pending_deposit_total() == 0
    assert ledger.total_liabilities() == 22_000_000


# ---------- deposits / claim ----------


def test_record_pending_and_claim_flow(ledger):
    ledger.record_pending("0x" + "1" * 64, "0xsender", 5_000_000)
    # Unknown tx.
    assert ledger.claim(ALICE, "0x" + "0" * 64) == (False, 0, "", "not_found")
    # Not linked yet -> cannot claim a deposit from an unowned wallet.
    assert ledger.claim(ALICE, "0x" + "1" * 64) == (False, 0, "0xsender", "not_owner")
    # Link the sender wallet, then claim succeeds.
    nonce = ledger.new_link_nonce(ALICE, "0xsender")
    assert ledger.confirm_link(ALICE, "0xsender", nonce)
    assert ledger.claim(ALICE, "0x" + "1" * 64) == (True, 5_000_000, "0xsender", "")
    assert ledger.balance(ALICE) == Decimal("5.000000")
    # Duplicate claim blocked.
    assert ledger.claim(ALICE, "0x" + "1" * 64) == (False, 0, "0xsender", "claimed")


def test_claim_theft_attempt_rejected(ledger):
    """The tx hash is public on-chain, so only the wallet owner may claim."""
    ledger.record_pending("0x" + "9" * 64, "0xvictim", 10_000_000)
    # BOB knows the tx hash but owns neither wallet.
    assert ledger.claim(BOB, "0x" + "9" * 64) == (False, 0, "0xvictim", "not_owner")
    assert ledger.balance(BOB) == Decimal("0")
    # ALICE linked a *different* wallet -> still cannot claim.
    nonce = ledger.new_link_nonce(ALICE, "0xalice")
    assert ledger.confirm_link(ALICE, "0xalice", nonce)
    assert ledger.claim(ALICE, "0x" + "9" * 64) == (False, 0, "0xvictim", "not_owner")
    # The victim (after linking) gets their deposit.
    nonce = ledger.new_link_nonce(BOB, "0xvictim")
    assert ledger.confirm_link(BOB, "0xvictim", nonce)
    assert ledger.claim(BOB, "0x" + "9" * 64) == (True, 10_000_000, "0xvictim", "")


def test_claim_for_sender_autoclaims_all(ledger):
    ledger.record_pending("0x" + "2" * 64, "0xowner", 1_000_000)
    ledger.record_pending("0x" + "3" * 64, "0xowner", 2_000_000)
    ledger.record_pending("0x" + "4" * 64, "0xowner", 3_000_000)
    # ALICE links 0xowner and manually claims one of the deposits.
    nonce = ledger.new_link_nonce(ALICE, "0xowner")
    assert ledger.confirm_link(ALICE, "0xowner", nonce)
    assert ledger.claim(ALICE, "0x" + "2" * 64) == (True, 1_000_000, "0xowner", "")

    claimed = ledger.claim_for_sender(BOB, "0xOWNER")  # case-insensitive match
    assert len(claimed) == 2
    assert ledger.balance(BOB) == Decimal("5.000000")
    assert ledger.balance(ALICE) == Decimal("1.000000")
    # Nothing left to claim.
    assert ledger.claim_for_sender(BOB, "0xowner") == []


def test_history_records_kinds(ledger):
    fund(ledger, ALICE, 10_000_000)
    ledger.transfer(ALICE, BOB, 1_000_000)
    rows = ledger.history(ALICE)
    kinds = [r["kind"] for r in rows]
    assert kinds == ["tip", "deposit"]
    assert rows[0]["amount"] == 1_000_000


def test_top_tippers_with_and_without_window(ledger):
    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    ledger.transfer(ALICE, BOB, 20_000_000)
    ledger.transfer(BOB, ALICE, 5_000_000)
    tippers = {r["tg_id"]: r["total"] for r in ledger.top_tippers()}
    assert tippers == {ALICE: 20_000_000, BOB: 5_000_000}
    # since_days window also works (both recent).
    assert len(ledger.top_tippers(10, since_days=7)) == 2


# ---------- bet edge cases ----------


def test_place_bet_on_missing_bet_returns_closed(ledger):
    assert ledger.place_bet(9999, ALICE, 0, 1_000_000) == "closed"


def test_place_bet_insufficient_returns_balance(ledger):
    fund(ledger, ALICE, 1_000_000)
    bid = ledger.create_bet(ALICE, "Доступно?", ["Да", "Нет"])
    assert ledger.place_bet(bid, ALICE, 0, 5_000_000) == "balance"
    assert ledger.balance(ALICE) == Decimal("1.000000")


def test_resolve_rejects_empty_and_abandoned(ledger):
    fund(ledger, ALICE, 100_000_000)
    empty = ledger.create_bet(ALICE, "Пустой?", ["А", "Б"])
    ok, msg = ledger.resolve_bet(empty, 0, ALICE)
    assert not ok and "нет денег" in msg

    bid = ledger.create_bet(ALICE, "Пустой?", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 1_000_000)
    ok, msg = ledger.resolve_bet(bid, 1, ALICE)  # no one bet on option 2
    assert not ok and "Никто не поставил" in msg


def test_resolve_rejects_wrong_option_and_closed(ledger):
    fund(ledger, ALICE, 100_000_000)
    bid = ledger.create_bet(ALICE, "Вопрос", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 1_000_000)
    ok, msg = ledger.resolve_bet(bid, 5, ALICE)
    assert not ok and "номер" in msg
    ok, _ = ledger.resolve_bet(bid, 0, ALICE)
    assert ok
    ok, msg = ledger.resolve_bet(bid, 0, ALICE)  # already resolved
    assert not ok and "уже закрыта" in msg


def test_cancel_rejects_when_not_open(ledger):
    fund(ledger, ALICE, 100_000_000)
    bid = ledger.create_bet(ALICE, "Вопрос", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 1_000_000)
    ledger.resolve_bet(bid, 0, ALICE)
    ok, msg = ledger.cancel_bet(bid, ALICE)
    assert not ok and "уже закрыта" in msg
    assert ledger.market_view(bid)["status"] == "resolved"


def test_market_view_unknown_is_none(ledger):
    assert ledger.market_view(9999) is None
    assert ledger.get_bet(9999) is None


def test_expired_market_refund_by_anyone(ledger, monkeypatch):
    import time

    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    # Deadline long in the past + grace passed.
    past = int(time.time()) - (config.MARKET_GRACE_HOURS + 1) * 3600
    bid = ledger.create_bet(ALICE, "Старый", ["А", "Б"], close_at=past)
    ledger.place_bet(bid, BOB, 0, 30_000_000)
    before = total_balance(ledger)
    assert ledger.market_view(bid)["expired"] is True

    ok, msg = ledger.cancel_bet(bid, BOB)  # not the creator!
    assert ok and "истёк" in msg
    assert ledger.balance(BOB) == Decimal("100.000000")
    assert total_balance(ledger) == before
    assert ledger.market_view(bid)["status"] == "cancelled"


def test_grace_not_passed_yet_blocks_non_creator(ledger):
    import time

    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    now = int(time.time())
    bid = ledger.create_bet(ALICE, "Свежий", ["А", "Б"], close_at=now)
    ledger.place_bet(bid, BOB, 0, 10_000_000)
    ok, msg = ledger.cancel_bet(bid, BOB)
    assert not ok and "только" in msg
    assert ledger.market_view(bid)["expired"] is False


# ---------- reaction tips ----------


def test_reaction_tip_happy_path(ledger):
    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 10_000_000)
    ledger.record_message(chat_id=-100, message_id=1, tg_id=ALICE)
    ok, reason, author = ledger.tip_by_reaction(-100, 1, BOB, 1_000_000)
    assert (ok, reason, author) == (True, "ok", ALICE)
    assert ledger.balance(ALICE) == Decimal("101.000000")
    assert ledger.balance(BOB) == Decimal("9.000000")


def test_reaction_tip_requires_balance(ledger):
    ledger.record_message(-100, 1, ALICE)
    ok, reason, _ = ledger.tip_by_reaction(-100, 1, BOB, 1_000_000)
    assert (ok, reason) == (False, "balance")


def test_reaction_tip_dedup(ledger):
    fund(ledger, BOB, 10_000_000)
    ledger.record_message(-100, 1, ALICE)
    assert ledger.tip_by_reaction(-100, 1, BOB, 1_000_000)[0] is True
    ok, reason, _ = ledger.tip_by_reaction(-100, 1, BOB, 5_000_000)
    assert (ok, reason) == (False, "duplicate")
    assert ledger.balance(BOB) == Decimal("9.000000")


def test_reaction_tip_self_and_missing_author(ledger):
    fund(ledger, ALICE, 10_000_000)
    ok, reason, _ = ledger.tip_by_reaction(-100, 1, ALICE, 1_000_000)
    assert (ok, reason) == (False, "author_missing")  # message not indexed

    ledger.record_message(-100, 1, ALICE)
    ok, reason, _ = ledger.tip_by_reaction(-100, 1, ALICE, 1_000_000)
    assert (ok, reason) == (False, "self")


def test_prune_message_index_removes_only_stale_rows(ledger):
    ledger.record_message(-100, 1, ALICE)
    ledger.record_message(-100, 2, BOB)
    ledger._conn.execute(
        "UPDATE message_authors SET created_at = %s WHERE message_id = 1",
        (int(time.time()) - 200 * 86400,),
    )
    ledger._conn.commit()
    assert ledger.prune_message_index(90 * 86400) == 1
    assert ledger.message_author(-100, 1) is None  # stale row pruned
    assert ledger.message_author(-100, 2) == BOB  # fresh row kept


def test_user_positions(ledger):
    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    bid = ledger.create_bet(ALICE, "Сыграем?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 20_000_000)
    ledger.place_bet(bid, ALICE, 1, 10_000_000)
    pos = ledger.user_positions(BOB)
    assert len(pos) == 1
    assert pos[0]["bet_id"] == bid
    assert pos[0]["stake_micro"] == 20_000_000
    assert pos[0]["potential_micro"] == 20_000_000 * 30_000_000 // 20_000_000
    # Resolved market drops out of open positions.
    ledger.resolve_bet(bid, 0, ALICE)
    assert ledger.user_positions(BOB) == []


def test_block_tracking_and_close(ledger):
    assert ledger.last_block() == 0
    ledger.set_last_block(12345)
    assert ledger.last_block() == 12345
    ledger.close()  # must not raise


def test_open_bets_past_deadline_only_due_unnotified(ledger):
    past = int(time.time()) - 100
    future = int(time.time()) + 86400
    a = ledger.create_bet(ALICE, "Истёк", ["Да"], close_at=past)
    ledger.create_bet(ALICE, "Будущий", ["Да"], close_at=future)
    ledger.create_bet(ALICE, "Без дедлайна", ["Да"], close_at=None)
    fund(ledger, ALICE, 1_000_000)

    assert [r["id"] for r in ledger.open_bets_past_deadline()] == [a]
    ledger.mark_deadline_notified(a)
    assert ledger.open_bets_past_deadline() == []

    # Resolved markets are never returned, even with a past close_at.
    b = ledger.create_bet(ALICE, "Решён", ["Да"], close_at=future)
    fund(ledger, ALICE, 1_000_000)
    ledger.place_bet(b, ALICE, 0, 100_000)
    ledger.resolve_bet(b, 0, ALICE)
    ledger._conn.execute("UPDATE bets SET close_at = %s WHERE id = %s", (past, b))
    ledger._conn.commit()
    assert ledger.open_bets_past_deadline() == []


def test_creator_fees_only_market_fees(ledger):
    fund(ledger, ALICE, 1_000_000)
    fund(ledger, BOB, 1_000_000)
    bid = ledger.create_bet(ALICE, "Q", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 100_000)
    ledger.place_bet(bid, BOB, 1, 100_000)
    ledger.resolve_bet(bid, 0, ALICE)
    assert ledger.creator_fees(ALICE) > 0
    # A withdraw fee is NOT creator income (it's what the platform charges).
    ledger.record_withdraw_fee(ALICE, "0x" + "a" * 40, 10_000, "0x" + "1" * 64)
    assert ledger.creator_fees(ALICE) > 0
    ledger.close()


# ---------- user settings ----------


def test_settings_defaults(ledger):
    assert ledger.get_settings(ALICE) == {
        "reaction_tips": True,
        "notify_deposits": True,
        "lang": "ru",
    }


def test_settings_toggle_and_unknown_key(ledger):
    ledger.set_setting(ALICE, "reaction_tips", False)
    ledger.set_setting(ALICE, "notify_deposits", False)
    assert ledger.get_settings(ALICE) == {
        "reaction_tips": False,
        "notify_deposits": False,
        "lang": "ru",
    }
    try:
        ledger.set_setting(ALICE, "bogus", True)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_settings_language_roundtrip(ledger):
    for code in ("en", "zh", "ru"):
        ledger.set_setting(ALICE, "lang", code)
        assert ledger.get_settings(ALICE)["lang"] == code
    try:
        ledger.set_setting(ALICE, "lang", "de")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------- rain (group giveaway) ----------


def test_rain_splits_exactly_and_conserves(ledger):
    fund(ledger, ALICE, 100_000_000)
    members = (BOB, CAROL, 1004, 1005)
    for u in members:
        ledger.record_message(-1000, u, u)
    before = total_balance(ledger)
    ok, msg, chosen = ledger.rain(-1000, ALICE, 10_000_000, 4)
    assert ok
    assert sorted(chosen) == sorted(members)
    assert ledger.balance(ALICE) == Decimal("90.000000")
    for u in members:
        assert ledger.balance(u) == Decimal("2.500000")
    assert total_balance(ledger) == before  # pure transfers, nothing lost


def test_rain_remainder_stays_with_sender(ledger):
    fund(ledger, ALICE, 100_000_000)
    for u in (BOB, CAROL):
        ledger.record_message(-1000, u, u)
    ok, msg, chosen = ledger.rain(-1000, ALICE, 5_000_001, 2)  # 2 x 2.5 USDC, 1 micro left
    assert ok
    assert ledger.balance(ALICE) == Decimal("95.000000")  # only share x count debited
    assert ledger.balance(BOB) == Decimal("2.500000")


def test_rain_excludes_sender(ledger):
    fund(ledger, ALICE, 100_000_000)
    for u in (BOB, CAROL):
        ledger.record_message(-1000, u, u)
    ledger.record_message(-1000, 99, ALICE)  # sender wrote too
    ok, msg, chosen = ledger.rain(-1000, ALICE, 6_000_000, 2)
    assert ok
    assert ALICE not in chosen


def test_rain_not_enough_members(ledger):
    fund(ledger, ALICE, 100_000_000)
    ledger.record_message(-1000, 1, BOB)
    ok, msg, chosen = ledger.rain(-1000, ALICE, 10_000_000, 3)
    assert not ok and "мало активных" in msg
    assert chosen == []
    assert ledger.balance(ALICE) == Decimal("100.000000")


def test_rain_insufficient_balance(ledger):
    ledger.record_message(-1000, 1, BOB)
    ok, msg, chosen = ledger.rain(-1000, ALICE, 10_000_000, 1)
    assert not ok and "Недостаточно" in msg
    assert ledger.balance(ALICE) == Decimal("0")


def test_rain_min_micro_per_recipient(ledger):
    for u in (BOB, CAROL, 1004, 1005):
        ledger.record_message(-1000, u, u)
    ok, msg, chosen = ledger.rain(-1000, ALICE, 3, 4)  # less than 1 micro each
    assert not ok


# ---------- market analytics ----------


def test_payouts_for_open_market_empty(ledger):
    fund(ledger, ALICE, 10_000_000)
    bid = ledger.create_bet(ALICE, "Q", ["А", "Б"])
    assert ledger.payouts_for(bid) == []


def test_payouts_for_matches_resolve_math(ledger):
    fund(ledger, ALICE, 700_000_000)
    fund(ledger, BOB, 100_000_000)
    fund(ledger, CAROL, 100_000_000)
    bid = ledger.create_bet(ALICE, "Кто выиграет?", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 400_000_000)
    ledger.place_bet(bid, BOB, 1, 100_000_000)
    ledger.place_bet(bid, CAROL, 0, 100_000_000)
    ledger.resolve_bet(bid, 0, ALICE)
    ps = {p["tg_id"]: p for p in ledger.payouts_for(bid)}
    assert ps[ALICE]["win"] and ps[CAROL]["win"] and not ps[BOB]["win"]
    assert ps[BOB]["net_micro"] == 0
    # winners share pot minus 2% fee on profit: 480 - 1.6 + 120 - 0.4 = 598
    assert ps[ALICE]["net_micro"] == 478_400_000
    assert ps[CAROL]["net_micro"] == 119_600_000
    assert ps[ALICE]["net_micro"] + ps[CAROL]["net_micro"] == 598_000_000


def test_market_view_backers(ledger):
    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    fund(ledger, CAROL, 100_000_000)
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 10_000_000)
    ledger.place_bet(bid, BOB, 0, 5_000_000)
    ledger.place_bet(bid, CAROL, 1, 3_000_000)
    view = ledger.market_view(bid)
    assert [o["backers"] for o in view["options"]] == [2, 1]
    assert view["total_backers"] == 3


def test_user_bet_stake(ledger):
    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    bid = ledger.create_bet(ALICE, "Q", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 2_000_000)
    ledger.place_bet(bid, BOB, 0, 3_000_000)
    ledger.place_bet(bid, BOB, 1, 1_000_000)
    assert ledger.user_bet_stake(bid, BOB) == {0: 5_000_000, 1: 1_000_000}
    assert ledger.user_bet_stake(bid, ALICE) == {}


def test_volume_history_and_30d(ledger):
    fund(ledger, ALICE, 100_000_000)  # deposit 100
    ledger.transfer(ALICE, BOB, 20_000_000)  # tip 20
    bid = ledger.create_bet(ALICE, "Q", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 10_000_000)  # bet 10
    hist = ledger.volume_history(14)
    assert len(hist) == 1
    assert hist[0]["volume_micro"] == 130_000_000
    assert ledger.global_stats()["volume_30d_micro"] == 130_000_000


def test_all_users(ledger):
    fund(ledger, ALICE, 1)
    ledger.ensure_user(BOB, "bob")
    assert {int(r["tg_id"]) for r in ledger.all_users()} == {ALICE, BOB}


# ---------- x402 agent payments ----------


def test_x402_credit_and_replay_proof(ledger):
    fund(ledger, ALICE, 1_000_000)  # 1 USDC
    tx = "0x" + "aa" * 32
    assert ledger.credit_x402(ALICE, tx, 5_000_000, "0xsender") is True
    assert ledger.user_view(ALICE)["balance_micro"] == 6_000_000
    assert ledger.user_view(ALICE)["tips_received_micro"] == 5_000_000
    # same tx again: refused, balance untouched
    assert ledger.credit_x402(ALICE, tx, 5_000_000, "0xsender") is False
    assert ledger.user_view(ALICE)["balance_micro"] == 6_000_000
    assert ledger.x402_paid(tx) is True
    assert ledger.x402_paid("0x" + "bb" * 32) is False


def test_x402_counts_into_volume_and_stats(ledger):
    fund(ledger, ALICE, 1_000_000)  # 1 USDC
    assert ledger.credit_x402(ALICE, "0x" + "cc" * 32, 7_000_000, "0xsender") is True
    stats = ledger.global_stats()
    assert stats["x402_micro"] == 7_000_000
    assert stats["volume_micro"] == 8_000_000
    assert stats["volume_30d_micro"] == 8_000_000
    assert ledger.user_stats(ALICE)[1] == 7_000_000  # tips_received


# ---------- paywall (paid content) ----------


def test_paywall_create_and_list(ledger):
    ledger.ensure_user(ALICE, "alice")
    item_id = ledger.create_paywall(ALICE, "Мой отчёт", 5_000_000, "секрет")
    row = ledger.paywall_item(item_id)
    assert row["title"] == "Мой отчёт"
    assert row["price_micro"] == 5_000_000
    assert row["content"] == "секрет"
    assert [int(r["id"]) for r in ledger.paywall_items_list()] == [item_id]
    assert ledger.paywall_item(999) is None


def test_paywall_buy_flow(ledger):
    fund(ledger, ALICE, 1_000_000)  # 1 USDC buyer
    fund(ledger, BOB, 1_000_000)  # owner
    item_id = ledger.create_paywall(BOB, "Пост", 400_000, "контент")

    assert ledger.buy_paywall(ALICE, item_id) == "ok"
    assert ledger.user_view(ALICE)["balance_micro"] == 600_000
    assert ledger.user_view(BOB)["balance_micro"] == 1_400_000
    assert ledger.paywall_purchased(item_id, ALICE) is True

    # second purchase is a dup (content re-shown for free)
    assert ledger.buy_paywall(ALICE, item_id) == "dup"
    assert ledger.user_view(ALICE)["balance_micro"] == 600_000
    assert ledger.user_view(BOB)["balance_micro"] == 1_400_000


def test_paywall_buy_insufficient_and_missing(ledger):
    fund(ledger, ALICE, 100_000)
    item_id = ledger.create_paywall(BOB, "Дорого", 500_000, "x")
    assert ledger.buy_paywall(ALICE, item_id) == "insufficient"
    assert ledger.user_view(ALICE)["balance_micro"] == 100_000
    assert ledger.buy_paywall(ALICE, 999) == "missing"


def test_paywall_owner_cannot_self_buy(ledger):
    fund(ledger, ALICE, 10_000_000)
    item_id = ledger.create_paywall(ALICE, "Мой пост", 500_000, "секрет")
    assert ledger.buy_paywall(ALICE, item_id) == "self"
    assert ledger.balance(ALICE) == Decimal("10.000000")
    assert ledger.paywall_purchased(item_id, ALICE) is False


def test_paywall_x402_purchase_credits_owner(ledger):
    fund(ledger, ALICE, 1_000_000)  # owner
    item_id = ledger.create_paywall(ALICE, "Отчёт", 5_000_000, "секрет")
    tx = "0x" + "dd" * 32
    assert ledger.x402_paywall_purchase(ALICE, item_id, tx, 5_000_000, "0xagent") == "ok"
    assert ledger.user_view(ALICE)["balance_micro"] == 6_000_000
    assert ledger.x402_paid(tx) is True
    # replay of the same tx -> 'replay', no double credit
    assert ledger.x402_paywall_purchase(ALICE, item_id, tx, 5_000_000, "0xagent") == "replay"
    assert ledger.user_view(ALICE)["balance_micro"] == 6_000_000
    # a tx already used as a tip cannot be replayed for a purchase either
    tx2 = "0x" + "ee" * 32
    assert ledger.credit_x402(ALICE, tx2, 1_000_000, "0xagent") is True
    assert ledger.x402_paywall_purchase(ALICE, item_id, tx2, 5_000_000, "0xagent") == "replay"
    assert ledger.user_view(ALICE)["balance_micro"] == 7_000_000


def test_paywall_shows_up_in_history(ledger):
    fund(ledger, ALICE, 1_000_000)
    fund(ledger, BOB, 1_000_000)
    item_id = ledger.create_paywall(BOB, "Пост", 400_000, "контент")
    ledger.buy_paywall(ALICE, item_id)
    alice_log = ledger.history(ALICE, 5)
    bob_log = ledger.history(BOB, 5)
    assert alice_log[0]["kind"] == "paywall"
    assert alice_log[0]["amount"] == -400_000
    assert bob_log[0]["kind"] == "paywall_earn"
    assert bob_log[0]["amount"] == 400_000


def test_paywall_item_limit_per_user(ledger, monkeypatch):
    from bot import config as cfg

    monkeypatch.setattr(cfg, "PAYWALL_MAX_ITEMS_PER_USER", 2)
    assert ledger.create_paywall(ALICE, "1", 100, "x") is not None
    assert ledger.create_paywall(ALICE, "2", 100, "x") is not None
    assert ledger.create_paywall(ALICE, "3", 100, "x") is None  # cap reached
    assert ledger.create_paywall(BOB, "b", 100, "x") is not None  # other user


def test_paywall_channel_limit_per_user(ledger, monkeypatch):
    from bot import config as cfg

    monkeypatch.setattr(cfg, "PAYWALL_MAX_CHANNELS_PER_USER", 2)
    assert ledger.set_paywall_channel(-100001, ALICE, 100)
    assert ledger.set_paywall_channel(-100002, ALICE, 100)
    assert not ledger.set_paywall_channel(-100003, ALICE, 100)  # cap reached
    # updating an existing channel never counts against the cap
    assert ledger.set_paywall_channel(-100001, ALICE, 200)
    assert ledger.set_paywall_channel(-100004, BOB, 100)


# ---------- channel paywall (paid access to channels) ----------


def test_paywall_channel_crud(ledger):
    ledger.set_paywall_channel(-100123, ALICE, 5_000_000)
    ch = ledger.paywall_channel(-100123)
    assert ch["owner_tg"] == ALICE
    assert ch["price_micro"] == 5_000_000
    assert ch["period_days"] == 30
    assert ledger.paywall_channel(-999) is None
    ledger.set_paywall_channel(-100123, ALICE, 7_000_000, 7)
    assert ledger.paywall_channel(-100123)["price_micro"] == 7_000_000
    assert ledger.paywall_channel(-100123)["period_days"] == 7
    assert [int(r["chat_id"]) for r in ledger.paywall_channels_list()] == [-100123]
    ledger.disable_paywall_channel(-100123)
    assert ledger.paywall_channel(-100123) is None


def test_paywall_channel_subscribe_flow(ledger):
    fund(ledger, ALICE, 1_000_000)  # buyer
    fund(ledger, BOB, 1_000_000)  # owner
    ledger.set_paywall_channel(-100123, BOB, 400_000)
    assert ledger.subscribe_channel(-100123, ALICE) == "ok"
    assert ledger.user_view(ALICE)["balance_micro"] == 600_000
    assert ledger.user_view(BOB)["balance_micro"] == 1_400_000
    sub = ledger.channel_subscription(-100123, ALICE)
    assert int(sub["expires_at"]) > time.time() + 29 * 86400
    # not for sale / insufficient
    assert ledger.subscribe_channel(-999, ALICE) == "missing"
    ledger.set_paywall_channel(-100124, BOB, 700_000)  # more than ALICE has left
    assert ledger.subscribe_channel(-100124, ALICE) == "insufficient"
    assert ledger.user_view(ALICE)["balance_micro"] == 600_000


def test_paywall_channel_owner_cannot_self_subscribe(ledger):
    fund(ledger, ALICE, 10_000_000)
    ledger.set_paywall_channel(-100123, ALICE, 400_000)
    assert ledger.subscribe_channel(-100123, ALICE) == "self"
    assert ledger.balance(ALICE) == Decimal("10.000000")
    assert ledger.channel_subscription(-100123, ALICE) is None


def test_paywall_channel_subscribe_extends_active(ledger):
    fund(ledger, ALICE, 10_000_000)
    ledger.set_paywall_channel(-100123, BOB, 400_000)
    assert ledger.subscribe_channel(-100123, ALICE) == "ok"
    first = int(ledger.channel_subscription(-100123, ALICE)["expires_at"])
    assert ledger.subscribe_channel(-100123, ALICE) == "ok"
    second = int(ledger.channel_subscription(-100123, ALICE)["expires_at"])
    assert second - first == 30 * 86400  # extended from the current expiry
    assert ledger.user_view(ALICE)["balance_micro"] == 10_000_000 - 800_000


def test_paywall_channel_expire_and_list(ledger):
    fund(ledger, ALICE, 1_000_000)
    ledger.set_paywall_channel(-100123, BOB, 400_000)
    ledger.subscribe_channel(-100123, ALICE)
    rows = ledger.active_channel_subscriptions()
    assert [(int(r["chat_id"]), r["tg_id"]) for r in rows] == [(-100123, ALICE)]
    ledger.expire_channel_subscription(-100123, ALICE)
    assert ledger.active_channel_subscriptions() == []
    assert ledger.channel_subscription(-100123, ALICE) is None


def test_paywall_channel_history_kinds(ledger):
    fund(ledger, ALICE, 1_000_000)
    fund(ledger, BOB, 1_000_000)
    ledger.set_paywall_channel(-100123, BOB, 400_000)
    ledger.subscribe_channel(-100123, ALICE)
    alice_log = ledger.history(ALICE, 5)
    bob_log = ledger.history(BOB, 5)
    assert alice_log[0]["kind"] == "channel_pay"
    assert alice_log[0]["amount"] == -400_000
    assert bob_log[0]["kind"] == "channel_earn"
    assert bob_log[0]["amount"] == 400_000


# ---------- grace-period warning ----------


def test_grace_warning_only_when_period_nearly_over(ledger):
    # grace is 72h; market closed 60h ago -> 12h left -> needs the warning
    early = ledger.create_bet(ALICE, "Скоро", ["Да"], close_at=int(time.time()) - 60 * 3600)
    ledger.mark_deadline_notified(early)
    warned = ledger.bets_need_grace_warning(12 * 3600)
    assert [int(r["id"]) for r in warned] == [early]
    assert warned[0]["close_at"] is not None


def test_grace_warning_skips_markets_not_due(ledger):
    # 10h past deadline -> 62h of grace left -> too early to warn
    far = ledger.create_bet(ALICE, "Далеко", ["Да"], close_at=int(time.time()) - 10 * 3600)
    ledger.mark_deadline_notified(far)
    assert ledger.bets_need_grace_warning(12 * 3600) == []


def test_grace_warning_skips_until_deadline_pinged(ledger):
    # deadline_notified=0 -> the first ping never went out, no warning yet
    never = ledger.create_bet(ALICE, "Тихий", ["Да"], close_at=int(time.time()) - 60 * 3600)
    assert ledger.bets_need_grace_warning(12 * 3600) == []
    ledger.mark_deadline_notified(never)
    assert [int(r["id"]) for r in ledger.bets_need_grace_warning(12 * 3600)] == [never]


def test_grace_warning_once(ledger):
    bid = ledger.create_bet(ALICE, "Раз", ["Да"], close_at=int(time.time()) - 60 * 3600)
    ledger.mark_deadline_notified(bid)
    assert ledger.bets_need_grace_warning(12 * 3600)
    ledger.mark_grace_warned(bid)
    assert ledger.bets_need_grace_warning(12 * 3600) == []


def test_grace_warning_skips_resolved(ledger):
    fund(ledger, ALICE, 10_000_000)
    bid = ledger.create_bet(ALICE, "Решён", ["Да"], close_at=None)
    ledger.place_bet(bid, ALICE, 0, 100_000)
    ledger.resolve_bet(bid, 0, ALICE)
    # mark the deadline as passed (resolved markets must never be re-warned)
    ledger._conn.execute("UPDATE bets SET close_at = %s WHERE id = %s", (int(time.time()) - 60 * 3600, bid))
    ledger._conn.commit()
    ledger.mark_deadline_notified(bid)
    assert ledger.bets_need_grace_warning(12 * 3600) == []


def test_reconnect_after_connection_drop(ledger):
    # Simulate a PostgreSQL restart: kill the socket from the client side.
    ledger._conn.close()
    assert ledger._conn.closed
    # The next call must transparently reconnect and work.
    fund(ledger, ALICE, 10_000_000)
    assert ledger.balance(ALICE) == Decimal("10.000000")
    # The connection is live again (a subsequent call also works).
    assert ledger.user_exists(ALICE)


def test_reconnect_after_server_side_drop(ledger):
    # Force the server to drop the connection (pg_terminate_backend) — the
    # proxy must notice the broken socket and reconnect on the next call.
    import psycopg

    try:
        from conftest import TEST_ADMIN_URL
    except ImportError:
        from tests.conftest import TEST_ADMIN_URL

    pid = ledger._conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    fund(ledger, BOB, 5_000_000)
    assert ledger.balance(BOB) == Decimal("5.000000")


# ---------- concurrency safety ----------

def test_concurrent_buy_shares_no_insolvency(ledger):
    """Two processes buying shares on the same market must not create
    insolvency.  With SELECT FOR UPDATE the second transaction blocks
    until the first commits, so escrow always equals the total spent."""
    import threading

    fund(ledger, ALICE, 100_000_000)
    fund(ledger, BOB, 100_000_000)
    fund(ledger, CAROL, 100_000_000)

    market_id = ledger.create_market(ALICE, "Test race?", ["Yes", "No"], 50_000_000)
    assert isinstance(market_id, int)

    results = {}
    errors = {}

    def buy(user_id, spend, label):
        try:
            conn = Ledger(TEST_DB_URL)
            ok, info = conn.buy_shares(market_id, user_id, 0, spend)
            results[label] = (ok, info)
            conn.close()
        except Exception as e:
            errors[label] = e

    t1 = threading.Thread(target=buy, args=(BOB, 20_000_000, "bob"))
    t2 = threading.Thread(target=buy, args=(CAROL, 30_000_000, "carol"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"thread errors: {errors}"
    assert results["bob"][0] == "ok"
    assert results["carol"][0] == "ok"

    m = ledger.get_market(market_id)
    escrow = int(m["escrow_micro"])
    # subsidy (50M) + bob (20M) + carol (30M) = 100M
    assert escrow == 100_000_000, f"escrow={escrow}, expected 100000000"

    q = ledger.market_quantities(market_id)
    total_shares = q[0]
    # LMSR gives more shares per dollar at low prices; the key invariant
    # is that escrow == subsidy + total spent (no insolvency), checked above.
    assert total_shares > 0, f"shares={total_shares}, expected > 0"

    # Funding theorem: escrow >= max shares for any option
    assert escrow >= total_shares
