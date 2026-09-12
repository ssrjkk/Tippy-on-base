"""Ledger domain mixin: LedgerPayMixin (split from bot/ledger.py)."""
import json
import time

from ._base import audit_log


class LedgerPayMixin:
    def credit(self, tg_id: int, amount_micro: int, kind: str, counterparty: str = "", tx_hash: str = "", note: str = "", commit: bool = True) -> None:
        if amount_micro < 0:
            raise ValueError(f"credit amount must be >= 0 (got {amount_micro})")
        with self._lock:
            self.ensure_user(tg_id, None, commit=commit)
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (amount_micro, tg_id),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, note) VALUES (%s, %s, %s, %s, %s, %s)",
                (kind, tg_id, counterparty, amount_micro, tx_hash, note),
            )
            if commit:
                self._conn.commit()
            audit_log.info(json.dumps({"event": "credit", "tg_id": tg_id, "amount_micro": amount_micro, "kind": kind, "counterparty": counterparty, "tx_hash": tx_hash}))



    def credit_x402(self, recipient_tg: int, tx_hash: str, amount_micro: int, sender: str, pay_to: str = "") -> bool:
        """Credit an on-chain x402 payment to a user. Atomic and replay-proof.

        The tx_hash is the PK of x402_payments: a second verification of the
        same transaction returns False (never double-credit). The deposit
        scanner skips these tx hashes, so liabilities stay exact.
        """
        with self._lock:
            self.ensure_user(recipient_tg, None)
            cur = self._conn.execute(
                "INSERT INTO x402_payments (tx_hash, recipient_tg, amount_micro, sender, pay_to) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING",
                (tx_hash, recipient_tg, amount_micro, sender, pay_to or None),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return False
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (amount_micro, recipient_tg),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, note) "
                "VALUES ('x402', %s, %s, %s, %s, 'x402 agent payment')",
                (recipient_tg, sender, amount_micro, tx_hash),
            )
            self._conn.commit()
            return True



    def reserve_x402_auth(self, nonce: str, tg_id: int, amount_micro: int, sender: str, pay_to: str = "") -> bool:
        """Reserve an EIP-3009 authorization nonce (scheme "exact"): the row
        key is `auth:<nonce>` in x402_payments. True = reserved (this caller
        may settle); False = already used (replay). The balance credit only
        lands in finalize_x402_credit, after the on-chain settlement succeeds."""
        if not nonce:
            return False
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO x402_payments (tx_hash, recipient_tg, amount_micro, sender, pay_to) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING",
                (nonce, tg_id, amount_micro, sender, pay_to or None),
            )
            committed = cur.rowcount > 0
            self._conn.commit()
            return committed



    def release_x402_auth(self, nonce: str) -> None:
        """Free a reserved nonce after a failed settlement, so the payer can
        re-sign (the on-chain nonce was never burned)."""
        if not nonce:
            return
        with self._lock:
            self._conn.execute(
                "DELETE FROM x402_payments WHERE tx_hash = %s", (nonce,)
            )
            self._conn.commit()



    def try_book_gas_drip(self, daily_max: int) -> bool:
        """Atomically book one gas drip against the UTC daily budget.

        The check-and-increment is a single conditional UPDATE: two bot
        processes can never both book the last remaining drip (the old
        SELECT-then-UPDATE pattern had exactly that race). False = budget
        exhausted for today."""
        with self._lock:
            day = int(time.time()) // 86400
            cur = self._conn.execute(
                "INSERT INTO gas_drips (day, count) VALUES (%s, 1) "
                "ON CONFLICT (day) DO UPDATE SET count = gas_drips.count + 1 "
                "WHERE gas_drips.count < %s "
                "RETURNING count",
                (day, daily_max),
            )
            booked = cur.fetchone() is not None
            self._conn.commit()
            return booked



    def x402_auth_reservations(self, older_than_seconds: int) -> list[dict]:
        """Reserved EIP-3009 auth rows ('auth:<nonce>') older than the cutoff:
        the reconciliation sweep finalizes or releases them."""
        with self._lock:
            # The cutoff is computed by the DATABASE clock: comparing the
            # DB-written created_at against the app's time.time() breaks on
            # even a 1-second clock skew between the two.
            return self._conn.execute(
                "SELECT tx_hash, recipient_tg, amount_micro, sender, pay_to, created_at "
                "FROM x402_payments WHERE tx_hash LIKE 'auth:%%' "
                "AND created_at + %s <= EXTRACT(EPOCH FROM now())::bigint "
                "ORDER BY created_at",
                (older_than_seconds,),
            ).fetchall()



    def try_book_subsidy(self, amount_micro: int, daily_max_micro: int) -> bool:
        """Atomically book an on-chain market subsidy against the UTC daily
        cap (protects the treasury from market-creation spam). False = the
        cap would be exceeded — the creator must wait until tomorrow."""
        if amount_micro <= 0 or amount_micro > daily_max_micro:
            return False
        with self._lock:
            day = int(time.time()) // 86400
            cur = self._conn.execute(
                "INSERT INTO market_subsidies (day, total_micro) VALUES (%s, %s) "
                "ON CONFLICT (day) DO UPDATE SET total_micro = market_subsidies.total_micro + %s "
                "WHERE market_subsidies.total_micro + %s <= %s "
                "RETURNING total_micro",
                (day, amount_micro, amount_micro, amount_micro, daily_max_micro),
            )
            booked = cur.fetchone() is not None
            self._conn.commit()
            return booked



    def release_subsidy(self, amount_micro: int) -> None:
        """Give back a booked subsidy when the on-chain createMarket tx reverts.

        Best-effort: the same UTC day's running total is decremented (clamped
        at zero) so a failed attempt does not permanently consume the creator's
        daily cap. No-op if the amount can never have been booked.
        """
        if amount_micro <= 0:
            return
        with self._lock:
            day = int(time.time()) // 86400
            try:
                self._conn.execute(
                    "INSERT INTO market_subsidies (day, total_micro) VALUES (%s, %s) "
                    "ON CONFLICT (day) DO UPDATE SET total_micro = "
                    "GREATEST(market_subsidies.total_micro - EXCLUDED.total_micro, 0)",
                    (day, amount_micro),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()



    def finalize_x402_credit(
        self, nonce: str, settlement_tx: str, recipient_tg: int,
        amount_micro: int, sender: str, pay_to: str = "",
    ) -> bool:
        """Atomically convert a reserved EIP-3009 authorization into a settled,
        credited x402 tip: swap the row key to the settlement tx, credit the
        recipient, log it. False = the reservation is gone (replay/race) —
        the caller must NOT credit again."""
        if not nonce or not settlement_tx:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE x402_payments SET tx_hash = %s, recipient_tg = %s, "
                "amount_micro = %s, sender = %s, pay_to = COALESCE(%s, pay_to) "
                "WHERE tx_hash = %s",
                (settlement_tx, recipient_tg, amount_micro, sender, pay_to or None, nonce),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return False
            self.ensure_user(recipient_tg, None, commit=False)
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (amount_micro, recipient_tg),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, note) "
                "VALUES ('x402', %s, %s, %s, %s, 'x402 agent payment (EIP-3009)')",
                (recipient_tg, sender, amount_micro, settlement_tx),
            )
            self._conn.commit()
            return True



    def finalize_x402_paywall(
        self, nonce: str, settlement_tx: str, owner_tg: int, item_id: int,
        amount_micro: int, sender: str, pay_to: str = "",
    ) -> bool:
        """Atomically convert a reserved authorization into a settled paywall
        purchase: swap the row key to the settlement tx, record the purchase,
        credit the item owner. False = reservation gone (replay/race)."""
        if not nonce or not settlement_tx:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE x402_payments SET tx_hash = %s, recipient_tg = %s, "
                "amount_micro = %s, sender = %s, pay_to = COALESCE(%s, pay_to) "
                "WHERE tx_hash = %s",
                (settlement_tx, owner_tg, amount_micro, sender, pay_to or None, nonce),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return False
            self._conn.execute(
                "INSERT INTO paywall_purchases (item_id, buyer_tg, tx_hash, amount_micro) "
                "VALUES (%s, NULL, %s, %s)",
                (item_id, settlement_tx, amount_micro),
            )
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (amount_micro, owner_tg),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, note) "
                "VALUES ('paywall_earn', %s, %s, %s, %s, 'x402 sale (EIP-3009)')",
                (owner_tg, str(item_id), amount_micro, settlement_tx),
            )
            self._conn.commit()
            return True



    def x402_paid(self, tx_hash: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM x402_payments WHERE tx_hash = %s", (tx_hash,)
            ).fetchone()
            return row is not None



    def create_x402_invoice(
        self, invoice_id: str, pay_addr: str, recipient_tg: int, amount_micro: int,
        kind: str, ref_id: str = "", pay_to: str = "",
    ) -> bool:
        """Register a unique per-invoice x402 pay address.

        Returns False if the invoice_id or pay_addr already exists (the
        invoice is deterministic, so a duplicate means a client asking twice
        for the same invoice — the prior row is authoritative)."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO x402_invoices "
                "(invoice_id, pay_addr, recipient_tg, amount_micro, kind, ref_id, pay_to) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (invoice_id) DO NOTHING",
                (invoice_id, pay_addr.lower(), recipient_tg, amount_micro,
                 kind, ref_id or None, pay_to or None),
            )
            self._conn.commit()
            return cur.rowcount > 0



    def x402_invoice_by_addr(self, pay_addr: str) -> dict | None:
        """The invoice bound to a unique pay address, or None.

        This lookup is the whole point of the design: a legacy tx-hash payment
        is only redeemable against the EXACT (recipient, amount, kind) invoice
        it was minted for, so a payment can never be redirected to an attacker
        (only the invoice's owner can present funds sent to its own address)."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM x402_invoices WHERE LOWER(pay_addr) = LOWER(%s)",
                (pay_addr,),
            ).fetchone()



    def mark_x402_invoice_credited(self, invoice_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE x402_invoices SET credited = true WHERE invoice_id = %s",
                (invoice_id,),
            )
            self._conn.commit()



    def unswept_x402_invoices(self) -> list[dict]:
        """Invoices whose unique-derived pay address was paid on-chain but the
        funds are still parked there (sweep target quota exceeded or the sweep
        watcher ran before the transfer confirmed)."""
        with self._lock:
            return self._conn.execute(
                "SELECT invoice_id, pay_addr, recipient_tg, amount_micro, kind, ref_id, pay_to "
                "FROM x402_invoices WHERE credited = true AND swept_at IS NULL "
                "ORDER BY created_at"
            ).fetchall()



    def mark_x402_invoice_swept(self, invoice_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE x402_invoices SET swept_at = EXTRACT(EPOCH FROM now())::bigint "
                "WHERE invoice_id = %s",
                (invoice_id,),
            )
            self._conn.commit()



    def x402_unswept_credit_total(self) -> int:
        """Booked x402 credits whose derived pay address has NOT been swept.

        The USDC backing them is still parked on-chain in the per-invoice
        address (until the sweep consolidates it into the receive pool and then
        the hot wallet). The solvency canary must count this as a reserve:
        those funds back outstanding liabilities but sit in an address the
        reserves line otherwise never reads.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount_micro), 0) AS s FROM x402_invoices "
                "WHERE credited = true AND swept_at IS NULL"
            ).fetchone()
        return int(row["s"])



    def pending_deposit_exists(self, tx_hash: str) -> bool:
        """True if `tx_hash` is already a detected on-chain deposit.

        x402 must reject such tx hashes: reusing a real deposit as an x402
        'payment' would mark it consumed and the deposit scanner would skip
        crediting the real depositor (fund loss / theft).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM pending_deposits WHERE tx_hash = %s", (tx_hash,)
            ).fetchone()
            return row is not None
