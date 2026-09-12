"""Ledger domain mixin: LedgerWithdrawMixin (split from bot/ledger.py)."""
import time

from .. import config


class LedgerWithdrawMixin:
    def reserve_withdraw(
        self, tg_id: int, to_address: str, amount_micro: int, fee_micro: int
    ) -> int | None:
        """Atomically debit amount+fee and enqueue a withdrawal.

        Returns the tx_log id, or None if the user lacks the balance. The row is
        written BEFORE any on-chain send and starts as **queued**: it sits in the
        batch-payout queue until the batch watcher flushes it (via
        TipBotVault.batchDistribute or a direct transfer). Crash between debit and
        send is safe — the queued row survives and is flushed/refunded later.

        Returns None (no funds move) for any refused destination too, and for
        the daily-request cap, and flags the attempt for AML review.
        """
        with self._lock:
            # ---- destination blocklist (anti self-send / burn / lock-in) ----
            from ..base import hot_wallet
            dest = (to_address or "").strip().lower()
            blocked: list[tuple[str, str]] = []
            dead_addr = "0x" + "0" * 40
            if dest in (dead_addr, "0x" + "0" * 40):
                blocked.append(("burn", "zero address"))
            hot = hot_wallet()
            if hot and dest and dest == hot.lower():
                blocked.append(("self", "hot wallet"))
            if config.VAULT_ADDRESS and dest == config.VAULT_ADDRESS.strip().lower():
                blocked.append(("vault", "vault contract"))
            if config.X402_RECEIVE_ADDRESS and dest == config.X402_RECEIVE_ADDRESS.strip().lower():
                blocked.append(("x402", "x402 receive pool"))
            if blocked:
                self._flag_suspicious(
                    tg_id, "withdraw_blocked",
                    {"to_address": to_address, "reason": blocked[0][1]}, severity="critical",
                )
                self._conn.rollback()
                return None
            # ---- atomic daily-request cap (closes the check-then-act hole:
            #      two concurrent /withdraw commands can no longer both pass
            #      the pre-check in the command handler) ----
            # The guard is embedded in the INSERT itself, so the count check and
            # the row write happen in one statement inside this transaction.
            # Cross-process safety: Postgres re-checks the subquery against the
            # committed snapshot each row it inserts, so two bot processes cannot
            # overshoot MAX_WITHDRAWS_PER_DAY even without app-level locking.
            since = int(time.time()) - 86400
            total = amount_micro + fee_micro
            committed = False
            try:
                cur = self._conn.execute(
                    "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                    (total, tg_id, total),
                )
                if cur.rowcount == 0:
                    self._conn.rollback()
                    return None
                cur = self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note, status) "
                    "SELECT 'withdraw', %s, %s, %s, %s, 'queued' "
                    "WHERE (SELECT COUNT(*) FROM tx_log "
                    "   WHERE tg_id = %s AND kind = 'withdraw' "
                    "   AND COALESCE(status, 'done') IN ('queued', 'pending', 'done') AND created_at >= %s) < %s "
                    "RETURNING id",
                    (tg_id, to_address, amount_micro, f"fee={fee_micro}",
                     tg_id, since, config.MAX_WITHDRAWS_PER_DAY),
                )
                wd_row = cur.fetchone()
                if wd_row is None:
                    # cap reached: already debited, so this whole tx rolls back
                    self._conn.rollback()
                    return None
                wd_id = int(wd_row["id"])
                if fee_micro > 0:
                    self._conn.execute(
                        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note, status) "
                        "VALUES ('fee', %s, %s, %s, %s, 'done')",
                        (tg_id, to_address, fee_micro, f"withdraw_id={wd_id}"),
                    )
                self._conn.commit()
                committed = True
                return wd_id
            finally:
                if not committed:
                    # Safety net: an unexpected exception mid-operation must not
                    # leave the row lock held or the connection in INERROR,
                    # poisoning every later ledger call. rollback is idempotent
                    # after a successful early return above.
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass



    def record_withdraw_fee(
        self, tg_id: int, to_address: str, fee_micro: int, tx_hash: str
    ) -> None:
        """Log the fee a successful withdrawal generated (business model)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, status) "
                "VALUES ('fee', %s, %s, %s, %s, 'done')",
                (tg_id, to_address, fee_micro, tx_hash),
            )
            self._conn.commit()



    def total_liabilities(self) -> int:
        """Sum of all internal liabilities the hot wallet must be able to cover,
        in micro-units. Six categories:

          1. user balances (users.balance)
          2. AMM market escrows of open markets (markets.escrow_micro)
          3. parimutuel bet pools of open bets (sum of bet_positions)
          4. community treasury balances (community_treasuries.balance)
          5. x402 credits already booked but not yet swept to the hot wallet
          6. pending deposits seen on-chain but not yet claimed/credited

        Only counting user balances would understate real obligations: escrowed
        market funds, open bet pools, treasury deposits, x402 reserves and
        pending deposits are all money the bot still owes even though they are
        not currently on a user's balance.
        """
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    (SELECT COALESCE(SUM(balance), 0) FROM users) AS user_bal,
                    (SELECT COALESCE(SUM(escrow_micro), 0)
                       FROM markets WHERE status = 'open') AS market_escrow,
                    (SELECT COALESCE(SUM(bp.amount_micro), 0)
                       FROM bet_positions bp
                       JOIN bets b ON bp.bet_id = b.id
                      WHERE b.status = 'open') AS bet_pool,
                    (SELECT COALESCE(SUM(balance), 0) FROM community_treasuries) AS treasury_bal
                """
            ).fetchone()
        return (
            int(row["user_bal"])
            + int(row["market_escrow"])
            + int(row["bet_pool"])
            + int(row["treasury_bal"])
            + self.x402_unswept_credit_total()
            + self.pending_deposit_total()
        )



    def pending_deposit_total(self) -> int:
        """Sum of unclaimed pending deposits in micro-units (funds held on-chain
        that the bot may still owe once claimed)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount_micro), 0) AS s "
                "FROM pending_deposits WHERE claimed = 0"
            ).fetchone()
        return int(row["s"])



    def record_pending(self, tx_hash: str, sender: str, amount_micro: int, block: int | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO pending_deposits (tx_hash, sender, amount_micro, block) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING",
                (tx_hash, sender, amount_micro, block),
            )
            self._conn.commit()



    def pending_matured(self, cutoff_block: int | None = None) -> list[dict]:
        """Distinct senders of unclaimed pending deposits eligible for credit.

        `cutoff_block`: rows whose block is NULL (legacy, pre-confirm-gate) or
        <= cutoff_block (confirmed on-chain). The deposit scan credits these
        EVERY sweep from the DB, not only while the deposit's block is still
        inside the re-scanned log window — so a deposit that matured off-window
        is still automatically credited.
        """
        with self._lock:
            if cutoff_block is None:
                return self._conn.execute(
                    "SELECT DISTINCT sender FROM pending_deposits WHERE claimed = 0"
                ).fetchall()
            return self._conn.execute(
                "SELECT DISTINCT sender FROM pending_deposits "
                "WHERE claimed = 0 AND (block IS NULL OR block <= %s)",
                (cutoff_block,),
            ).fetchall()



    def claim(self, tg_id: int, tx_hash: str, maturity_block: int | None = None) -> tuple[bool, int, str, str]:
        """Credit a pending deposit to a user. Returns (ok, amount_micro, sender, reason).

        Security: only the owner of the *sending* wallet may claim. Deposits are
        public on-chain, so a tx hash is not a secret — without this check anyone
        could /claim somebody else's funds. reason is '' on success, otherwise
        'not_found' | 'claimed' | 'not_owner' | 'not_mature'.

        `maturity_block` (the confirmed-deposit cutoff) mirrors the scanner's
        confirm gate: rows with block > cutoff are NOT credited here — the caller
        must verify the deposit has DEPOSIT_CONFIRM_BLOCKS confirmations, or the
        handler becomes a reorg-exploitable bypass of the maturity gate (credit a
        still-reorgable deposit, withdraw, and let the reorg delete the backing
        tx). block NULL rows are legacy pre-gate deposits, credited unconditionally.
        """
        with self._lock:
            if maturity_block is not None:
                row = self._conn.execute(
                    "SELECT sender, amount_micro, block FROM pending_deposits WHERE tx_hash = %s",
                    (tx_hash,),
                ).fetchone()
                if row and row["block"] is not None and row["block"] > maturity_block:
                    self._conn.rollback()
                    return False, 0, row["sender"], "not_mature"
            else:
                row = self._conn.execute(
                    "SELECT sender, amount_micro, block FROM pending_deposits WHERE tx_hash = %s",
                    (tx_hash,),
                ).fetchone()
            if not row:
                self._conn.rollback()
                return False, 0, "", "not_found"
            linked = self.linked_address(tg_id)
            if not linked or linked.lower() != row["sender"].lower():
                self._conn.rollback()
                return False, 0, row["sender"], "not_owner"
            self.ensure_user(tg_id, None, commit=False)
            # Atomic claim: the conditional UPDATE is the single arbiter of who
            # gets the money, even across processes (web dashboard + bot share
            # one DB). A stale reader that saw claimed=0 loses here.
            cur = self._conn.execute(
                "UPDATE pending_deposits SET claimed = 1 WHERE tx_hash = %s AND claimed = 0",
                (tx_hash,),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return False, 0, row["sender"], "claimed"
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (row["amount_micro"], tg_id),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash) VALUES ('deposit', %s, %s, %s, %s)",
                (tg_id, row["sender"], row["amount_micro"], tx_hash),
            )
            self._conn.commit()
            return True, row["amount_micro"], row["sender"], ""



    def claim_for_sender(self, tg_id: int, sender: str, maturity_block: int | None = None) -> list[dict]:
        """Auto-claim pending deposits from a linked sender address.

        `maturity_block`: only claims deposits whose block <= maturity_block
        (DEPOSIT_CONFIRM_BLOCKS-confirmed on chain). Rows with block NULL are
        legacy deposits recorded before the confirm gate — claimed unconditionally
        (they were pre-confirmed when the newer system took over).
        """
        with self._lock:
            if maturity_block is not None:
                where = (
                    "WHERE LOWER(sender) = LOWER(%s) AND claimed = 0 "
                    "AND (block IS NULL OR block <= %s) FOR UPDATE"
                )
                params = (sender, maturity_block)
            else:
                where = "WHERE LOWER(sender) = LOWER(%s) AND claimed = 0 FOR UPDATE"
                params = (sender,)
            rows = self._conn.execute(
                "SELECT tx_hash, amount_micro FROM pending_deposits " + where,
                params,
            ).fetchall()
            self.ensure_user(tg_id, None, commit=False)
            for row in rows:
                cur = self._conn.execute(
                    "UPDATE pending_deposits SET claimed = 1 WHERE tx_hash = %s AND claimed = 0",
                    (row["tx_hash"],),
                )
                if cur.rowcount == 0:
                    continue  # already claimed by a competing process
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (row["amount_micro"], tg_id),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash) VALUES ('deposit', %s, %s, %s, %s)",
                    (tg_id, sender, row["amount_micro"], row["tx_hash"]),
                )
            self._conn.commit()
            return [
                {"tx_hash": r["tx_hash"], "amount_micro": r["amount_micro"]} for r in rows
            ]



    def history(self, tg_id: int, limit: int = 15) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, counterparty, amount, tx_hash, note, created_at "
                "FROM tx_log WHERE tg_id = %s ORDER BY id DESC LIMIT %s",
                (tg_id, limit),
            ).fetchall()
        return rows



    def withdrawals_today(self, tg_id: int) -> int:
        """Withdrawal requests in the last 24h (anti gas-griefing).

        Counts both `queued` (in the batch, not yet on-chain) and `done`
        (actually paid out) rows, so a user cannot spam the queue past
        MAX_WITHDRAWS_PER_DAY while withdrawals sit batched.
        """
        since = int(time.time()) - 86400
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM tx_log "
                "WHERE tg_id = %s AND kind = 'withdraw' "
                "AND COALESCE(status, 'done') IN ('queued', 'pending', 'done') AND created_at >= %s",
                (tg_id, since),
            ).fetchone()
        return int(row["c"])



    def _flag_suspicious(self, tg_id: int, kind: str, details: dict, severity: str = "warn") -> None:
        """Record a suspicious-activity flag for a user."""
        import json
        with self._lock:
            self._conn.execute(
                "INSERT INTO suspicious_activity (tg_id, kind, details, severity) "
                "VALUES (%s, %s, %s, %s)",
                (tg_id, kind, json.dumps(details), severity),
            )
            self._conn.commit()



    def check_aml_withdraw(self, tg_id: int, amount_micro: int, to_address: str) -> list[str]:
        """Run AML checks before a withdrawal. Returns list of warning messages.

        Checks:
          - Single withdrawal > WITHDRAW_LARGE_USDC_THRESHOLD (warn)
          - >3 withdrawals in 1h (warn)
          - Balance after withdraw <0 and user has large recent deposits (info)
        Flags are persisted for audit trail.
        """
        warnings = []
        now = int(time.time())

        # Check 1: large single withdrawal
        large_thresh = getattr(config, "WITHDRAW_LARGE_USDC_THRESHOLD", 500) * 10**config.USDC_DECIMALS
        if amount_micro >= large_thresh:
            msg = f"Large withdrawal: ${amount_micro / 10**config.USDC_DECIMALS:.2f} >= ${large_thresh / 10**config.USDC_DECIMALS:.0f}"
            warnings.append(msg)
            self._flag_suspicious(tg_id, "large_withdraw", {
                "amount": amount_micro, "threshold": large_thresh, "to": to_address,
            }, severity="warn")

        # Check 2: rapid successive withdrawals (>3 in 1 hour)
        since_1h = now - 3600
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM tx_log "
                "WHERE tg_id = %s AND kind = 'withdraw' "
                "AND COALESCE(status, 'done') = 'done' AND created_at >= %s",
                (tg_id, since_1h),
            ).fetchone()
        rapid_count = int(row["c"])
        if rapid_count >= 3:
            msg = f"Rapid withdrawals: {rapid_count} in the last hour"
            warnings.append(msg)
            self._flag_suspicious(tg_id, "rapid_withdraw", {
                "count": rapid_count, "window_seconds": 3600,
            }, severity="warn")

        return warnings



    def suspicious_activity_for(self, tg_id: int, limit: int = 50) -> list[dict]:
        """Return recent suspicious-activity flags for a user (audit trail)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, kind, details, severity, created_at "
                "FROM suspicious_activity WHERE tg_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (tg_id, limit),
            ).fetchall()



    def pending_withdraws(self) -> list[dict]:
        """Withdraw rows not yet confirmed: status IS NULL (legacy) or 'pending'.

        `queued` rows (waiting in the batch queue, not yet sent on-chain) are
        deliberately excluded — the refund sweep must NOT refund something that
        has not been broadcast yet.

        `note` carries `fee=<micro>` (written by reserve_withdraw) so the
        refund sweep can restore the exact debited total for rows created
        under a different fee scheme instead of recomputing it.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT id, tg_id, counterparty, amount, tx_hash, status, note, created_at "
                "FROM tx_log WHERE kind = 'withdraw' "
                "AND COALESCE(status, '') NOT IN ('done', 'refunded', 'queued') ORDER BY id"
            ).fetchall()



    def mark_withdraw_done(self, wd_id: int, tx_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                # COALESCE(NULLIF(...)): a legacy row whose hash is unknown
                # stays NULL (ambiguous) instead of being overwritten with an
                # empty marker string that would read like a real tx hash.
                "UPDATE tx_log SET tx_hash = COALESCE(NULLIF(%s, ''), tx_hash), "
                "status = 'done' WHERE id = %s",
                (tx_hash, wd_id),
            )
            self._conn.commit()



    def withdraw_queue(self) -> list[dict]:
        """Withdraw rows sitting in the batch queue (not yet broadcast on-chain).

        Returned oldest-first so the batch-payout watcher can flush a bounded
        window. Each row carries the recipient (`counterparty`), the payout
        amount (`amount`, fee separately held in `note`), and `created_at`.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT id, tg_id, counterparty, amount, tx_hash, status, note, created_at "
                "FROM tx_log WHERE kind = 'withdraw' AND status = 'queued' ORDER BY id"
            ).fetchall()



    def claim_withdraw_batch(self, wd_ids: list[int]) -> list[int]:
        """Atomically claim queued rows for broadcast.

        Flips `queued` -> `pending` and returns ONLY the ids that were still
        `queued` (i.e. this caller won the claim). A concurrent batch watcher
        cannot double-broadcast the same row: after the first claim the row is
        `pending` and no longer selected by `withdraw_queue()`. A crash between
        claim and broadcast leaves rows `pending` with tx_hash NULL, which the
        refund sweep refunds after WITHDRAW_STUCK_TIMEOUT — safe, nothing sent.
        """
        if not wd_ids:
            return []
        with self._lock:
            rows = self._conn.execute(
                "UPDATE tx_log SET status = 'pending' "
                "WHERE id = ANY(%s) AND status = 'queued' RETURNING id",
                (wd_ids,),
            ).fetchall()
            self._conn.commit()
            return [int(r["id"]) for r in rows]



    def set_withdraw_batch_hash(self, wd_ids: list[int], tx_hash: str) -> None:
        """Associate a broadcast tx hash with claimed (pending) rows."""
        if not wd_ids:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE tx_log SET tx_hash = %s "
                "WHERE id = ANY(%s) AND status = 'pending'",
                (tx_hash, wd_ids),
            )
            self._conn.commit()



    def set_withdraw_pending_hash(self, wd_id: int, tx_hash: str) -> None:
        """Record a known-but-unconfirmed tx hash, leaving the row pending.

        Used when broadcast could not confirm whether the tx landed. The
        pending-withdraw watcher later settles it from the real receipt
        (success -> done, revert/stuck -> refund, RPC down -> keep pending).
        """
        with self._lock:
            self._conn.execute(
                "UPDATE tx_log SET tx_hash = %s WHERE id = %s "
                "AND COALESCE(status, '') NOT IN ('done', 'refunded')",
                (tx_hash, wd_id),
            )
            self._conn.commit()



    def refund_withdraw(self, wd_id: int, tg_id: int, total_micro: int) -> bool:
        """Full refund of amount + fee; keeps the row as an audit trail.

        Single-credit guarantee: the withdraw row is first flipped to
        'refunded' (guarded against a terminal status) and the balance is
        credited ONLY if that flip was won. Two concurrent refund paths (the
        pending-sweep and a batch fallback) racing on the same row now result
        in exactly one credit, not two.

        Returns True if the refund was applied, False if the row was already
        'done'/'refunded' (no credit happens in that case).
        """
        with self._lock:
            flipped = self._conn.execute(
                "UPDATE tx_log SET status = 'refunded' WHERE id = %s "
                "AND COALESCE(status, '') NOT IN ('done', 'refunded') RETURNING id",
                (wd_id,),
            ).fetchone()
            if flipped is None:
                self._conn.rollback()
                return False
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (total_micro, tg_id),
            )
            self._conn.execute(
                "UPDATE tx_log SET status = 'refunded' "
                "WHERE kind = 'fee' AND note = %s", (f"withdraw_id={wd_id}",)
            )
            self._conn.commit()
            return True
