"""Ledger domain mixin: LedgerAdminMixin (split from bot/ledger.py)."""
import secrets
import time

from .. import config
from ._base import MICRO


class LedgerAdminMixin:
    def prune_housekeeping(self, retention_seconds: int, x402_payment_retention_seconds: int) -> dict:
        """Bound the growth of the x402/deposit side-tables.

        Nothing money-critical is touched:
        - `x402_invoices` that are credited AND swept are pure audit rows; the
          reconcile path only reads invoices with credited=false.
        - `pending_deposits` that are claimed are consumed; the scan/pool only
          ever reads claimed=0. (Legacy rows got a `created_at` via the ALTER
          default backfill, so they age out from when the column was added.)
        - `x402_payments` is the anti-replay ledger (a paid tx hash must never
          be re-deposited). The deposit scanner only re-scans the newest
          confirm/lookback window on Base (~hours), so hashes older than one
          year can never collide with a scanned deposit again; keeping the
          tail is overkill. The longer retention (365d) is used for these.

        Keep the final tx_log audit trail untouched.
        Returns {'x402_invoices': n, 'pending_deposits': n, 'x402_payments': n}.
        """
        now = int(time.time())
        invoice_cutoff = now - retention_seconds
        with self._lock:
            x = self._conn.execute(
                "DELETE FROM x402_invoices "
                "WHERE credited = true AND swept_at IS NOT NULL AND created_at < %s",
                (invoice_cutoff,),
            ).rowcount
            y = self._conn.execute(
                "DELETE FROM pending_deposits WHERE claimed = 1 AND created_at < %s",
                (now - retention_seconds,),
            ).rowcount
            z = self._conn.execute(
                "DELETE FROM x402_payments WHERE created_at < %s",
                (now - x402_payment_retention_seconds,),
            ).rowcount
            self._conn.commit()
        return {"x402_invoices": x, "pending_deposits": y, "x402_payments": z}



    def get_settings(self, tg_id: int) -> dict:
        # Read-only: no ensure_user() here. The hot path (every /tip, /rain,
        # menu render) calls this to READ preferences; a write-and-commit per
        # read would double the round-trips on the single serialized
        # connection and needlessly allocate user rows. The users row is
        # created on first real write (set_setting / ensure_user).
        with self._lock:
            row = self._conn.execute(
                "SELECT reaction_tips, notify_deposits, lang FROM user_settings WHERE tg_id = %s",
                (tg_id,),
            ).fetchone()
        if row:
            return {
                "reaction_tips": bool(row["reaction_tips"]),
                "notify_deposits": bool(row["notify_deposits"]),
                "lang": row["lang"] or "ru",
            }
        return {"reaction_tips": True, "notify_deposits": True, "lang": "ru"}



    def set_setting(self, tg_id: int, key: str, value: bool | str) -> None:
        ALLOWED_SETTING_COLUMNS = {"lang", "reaction_tips", "notify_deposits"}
        if key not in ALLOWED_SETTING_COLUMNS:
            raise ValueError(f"unknown setting: {key}")
        if key == "lang" and value not in ("ru", "en", "zh"):
            raise ValueError(f"unknown language: {value}")
        with self._lock:
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "INSERT INTO user_settings (tg_id) VALUES (%s) ON CONFLICT (tg_id) DO NOTHING",
                (tg_id,)
            )
            param = (1 if value else 0) if isinstance(value, bool) else value
            self._conn.execute(
                f"UPDATE user_settings SET {key} = %s WHERE tg_id = %s",
                (param, tg_id),
            )
            self._conn.commit()



    def get_wallet(self, tg_id: int) -> dict | None:
        """Encrypted wallet row for a user, or None if not created yet."""
        with self._lock:
            return self._conn.execute(
                "SELECT tg_id, address, key_enc, seed_enc FROM user_wallets WHERE tg_id = %s",
                (tg_id,),
            ).fetchone()



    def wallet_address(self, tg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT address FROM user_wallets WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return row["address"] if row else None



    def tg_id_of_wallet_address(self, address: str) -> int | None:
        """The tg_id owning a CUSTODIAL in-bot wallet with this address
        (user_wallets). Complements tg_id_of_address (external linked
        wallets). Basename tipping resolves through both."""
        with self._lock:
            row = self._conn.execute(
                "SELECT tg_id FROM user_wallets WHERE LOWER(address) = LOWER(%s) "
                "AND active = true LIMIT 1",
                (address,),
            ).fetchone()
        return int(row["tg_id"]) if row else None



    def wallet_address_exists(self, address: str) -> bool:
        """True if another user already attached this wallet address."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM user_wallets WHERE address = %s", (address,)
            ).fetchone()
        return row is not None



    def save_wallet(self, tg_id: int, address: str, key_enc: str, seed_enc: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_wallets (tg_id, address, key_enc, seed_enc, slot, active) "
                "VALUES (%s, %s, %s, %s, 1, true) "
                "ON CONFLICT (tg_id, slot) DO UPDATE SET address = EXCLUDED.address, "
                "key_enc = EXCLUDED.key_enc, seed_enc = EXCLUDED.seed_enc",
                (tg_id, address, key_enc, seed_enc),
            )
            self._conn.commit()



    def get_wallets(self, tg_id: int) -> list[dict]:
        """All wallets for a user, ordered by slot."""
        with self._lock:
            return list(self._conn.execute(
                "SELECT id, tg_id, address, key_enc, seed_enc, slot, active "
                "FROM user_wallets WHERE tg_id = %s ORDER BY slot",
                (tg_id,),
            ).fetchall())



    def get_active_wallet(self, tg_id: int) -> dict | None:
        """The active wallet for a user (active=true), or None."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, tg_id, address, key_enc, seed_enc, slot, active "
                "FROM user_wallets WHERE tg_id = %s AND active = true",
                (tg_id,),
            ).fetchone()



    def set_active_wallet(self, tg_id: int, wallet_id: int) -> bool:
        """Set a specific wallet as active for the user. Returns False if not found."""
        with self._lock:
            # Verify wallet belongs to user
            row = self._conn.execute(
                "SELECT id FROM user_wallets WHERE id = %s AND tg_id = %s",
                (wallet_id, tg_id),
            ).fetchone()
            if not row:
                return False
            # Deactivate all, activate the selected one
            self._conn.execute(
                "UPDATE user_wallets SET active = false WHERE tg_id = %s",
                (tg_id,),
            )
            self._conn.execute(
                "UPDATE user_wallets SET active = true WHERE id = %s",
                (wallet_id,),
            )
            self._conn.commit()
            return True



    def create_wallet_slot(self, tg_id: int) -> tuple[dict | None, str | None]:
        """Create a new wallet in the next available slot.

        Returns (wallet_row_or_None, error_key_or_None).
        Error keys: 'limit_reached', 'db_error'.
        """
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM user_wallets WHERE tg_id = %s",
                (tg_id,),
            ).fetchone()["cnt"]
            if count >= config.MAX_WALLETS_PER_USER:
                return None, "wallet_limit_reached"
            # Find next available slot
            rows = self._conn.execute(
                "SELECT slot FROM user_wallets WHERE tg_id = %s ORDER BY slot",
                (tg_id,),
            ).fetchall()
            used_slots = {r["slot"] for r in rows}
            next_slot = 1
            while next_slot in used_slots:
                next_slot += 1
            # Deactivate all existing wallets (new wallet becomes active)
            self._conn.execute(
                "UPDATE user_wallets SET active = false WHERE tg_id = %s",
                (tg_id,),
            )
            # Create wallet
            from .. import wallets as _wallets
            address, key, seed = _wallets.new_wallet()
            key_enc = _wallets.encrypt(key)
            seed_enc = _wallets.encrypt(seed)
            self._conn.execute(
                "INSERT INTO user_wallets (tg_id, address, key_enc, seed_enc, slot, active) "
                "VALUES (%s, %s, %s, %s, %s, true)",
                (tg_id, address, key_enc, seed_enc, next_slot),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id, tg_id, address, key_enc, seed_enc, slot, active FROM user_wallets "
                "WHERE tg_id = %s AND slot = %s",
                (tg_id, next_slot),
            ).fetchone()
            return row, None



    def rain(self, chat_id: int, sender_id: int, amount_micro: int, count: int) -> tuple[bool, str, list[int]]:
        """Split `amount_micro` equally among `count` random active members of a
        chat (from recent indexed messages). Pure transfers: nothing is created
        or lost (the split is exact; the remainder stays with the sender).

        Returns (ok, message, recipient_ids). Money conservation holds by
        construction: only `share * count` is ever debited.
        """
        if count < 1 or amount_micro < count:
            return False, "Сумма должна делиться минимум по 1 микро-юниту на человека.", []
        with self._lock:
            pool = self._conn.execute(
                "SELECT tg_id FROM message_authors WHERE chat_id = %s AND tg_id != %s "
                "GROUP BY tg_id ORDER BY MAX(created_at) DESC LIMIT %s",
                (chat_id, sender_id, 200),
            ).fetchall()
        candidates = [int(r["tg_id"]) for r in pool]
        if len(candidates) < count:
            return False, "В этом чате пока мало активных участников.", []
        chosen = secrets.SystemRandom().sample(candidates, count)
        share = amount_micro // count
        total = share * count
        with self._lock:
            self.ensure_user(sender_id, None, commit=False)
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (total, sender_id, total),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return False, "Недостаточно баланса. Пополни: /deposit", []
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('tip', %s, %s, %s, 'rain')",
                (sender_id, ",".join(map(str, chosen)), total),
            )
            for to_id in chosen:
                self.ensure_user(to_id, None, commit=False)
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (share, to_id),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('tip', %s, %s, %s, 'rain')",
                    (to_id, str(sender_id), share),
                )
            self._conn.commit()
        return True, f"🌧️ Разбросано {share * count / MICRO:g} USDC: {count} × {share / MICRO:g} USDC", chosen



    def last_block(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT block FROM last_block WHERE id = 1").fetchone()
        return row["block"] if row else 0



    def set_last_block(self, block: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO last_block (id, block) VALUES (1, %s) "
                "ON CONFLICT (id) DO UPDATE SET block = EXCLUDED.block",
                (block,)
            )
            self._conn.commit()



    def rollback(self) -> None:
        """Drop any open read transaction so the shared connection never pins
        table locks (web request middleware calls this after every request)."""
        with self._lock:
            self._conn.rollback()



    def close(self) -> None:
        with self._lock:
            self._conn.close()
