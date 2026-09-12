"""Ledger domain mixin: LedgerPaywallMixin (split from bot/ledger.py)."""
import time

from .. import config


class LedgerPaywallMixin:
    def create_paywall(self, owner_tg: int, title: str, price_micro: int, content: str) -> int | None:
        """Register a paid content item. The owner earns price_micro per purchase.

        Returns None when the per-user item cap is reached (anti-spam).
        """
        self.ensure_user(owner_tg, None)
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM paywall_items WHERE owner_tg = %s",
                (owner_tg,),
            ).fetchone()
            if int(row["c"]) >= config.PAYWALL_MAX_ITEMS_PER_USER:
                return None
            cur = self._conn.execute(
                "INSERT INTO paywall_items (owner_tg, title, price_micro, content) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (owner_tg, title, price_micro, content),
            )
            self._conn.commit()
            return int(cur.fetchone()["id"])



    def paywall_item(self, item_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM paywall_items WHERE id = %s", (item_id,)
            ).fetchone()



    def paywall_items_list(self) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM paywall_items ORDER BY id DESC"
            ).fetchall()



    def paywall_purchased(self, item_id: int, buyer_tg: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM paywall_purchases WHERE item_id = %s AND buyer_tg = %s",
                (item_id, buyer_tg),
            ).fetchone()
            return row is not None



    def buy_paywall(self, buyer_tg: int, item_id: int) -> str:
        """Buy a paywall item from the internal balance.

        Returns 'ok' (debited, credited to the owner), 'dup' (already bought —
        the caller re-shows the content) or 'insufficient'.
        """
        with self._lock:
            item = self._conn.execute(
                "SELECT owner_tg, price_micro FROM paywall_items WHERE id = %s", (item_id,)
            ).fetchone()
            if item is None:
                return "missing"
            if buyer_tg == int(item["owner_tg"]):
                return "self"  # an owner cannot buy (and re-sell) their own post
            if self._conn.execute(
                "SELECT 1 FROM paywall_purchases WHERE item_id = %s AND buyer_tg = %s",
                (item_id, buyer_tg),
            ).fetchone():
                return "dup"
            price = int(item["price_micro"])
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (price, buyer_tg, price),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return "insufficient"
            # The insert must SUCCEED for the owner credit below to happen: on
            # a concurrent duplicate (two processes racing past the pre-check)
            # DO NOTHING would silently skip the row while the rest of the
            # transaction still commits — debiting the buyer twice and
            # crediting the owner twice (money creation). Abort instead.
            cur = self._conn.execute(
                "INSERT INTO paywall_purchases (item_id, buyer_tg, tx_hash, amount_micro) "
                "VALUES (%s, %s, NULL, %s) ON CONFLICT DO NOTHING RETURNING id",
                (item_id, buyer_tg, price),
            )
            if cur.fetchone() is None:
                self._conn.rollback()
                return "dup"
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (price, int(item["owner_tg"])),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('paywall', %s, %s, %s, 'платный контент')",
                (buyer_tg, str(item_id), -price),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('paywall_earn', %s, %s, %s, 'продажа контента')",
                (int(item["owner_tg"]), str(item_id), price),
            )
            self._conn.commit()
            return "ok"



    def x402_paywall_purchase(
        self, owner_tg: int, item_id: int, tx_hash: str, amount_micro: int, sender: str, pay_to: str = ""
    ) -> str:
        """Credit an x402 payment for a paywall item. Atomic and replay-proof.

        Returns 'ok', or 'replay' when this tx was already processed (either
        as a tip or as this purchase). The tx hash stays the PK of
        x402_payments, so the deposit scanner can never double-credit it.
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO x402_payments (tx_hash, recipient_tg, amount_micro, sender, pay_to) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING",
                (tx_hash, owner_tg, amount_micro, sender, pay_to or None),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return "replay"
            if self._conn.execute(
                "SELECT 1 FROM paywall_purchases WHERE item_id = %s AND tx_hash = %s",
                (item_id, tx_hash),
            ).fetchone():
                self._conn.rollback()
                return "replay"
            self._conn.execute(
                "INSERT INTO paywall_purchases (item_id, buyer_tg, tx_hash, amount_micro) "
                "VALUES (%s, NULL, %s, %s)",
                (item_id, tx_hash, amount_micro),
            )
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (amount_micro, owner_tg),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, note) "
                "VALUES ('paywall_earn', %s, %s, %s, %s, 'продажа контента')",
                (owner_tg, str(item_id), amount_micro, tx_hash),
            )
            self._conn.commit()
            return "ok"



    def set_paywall_channel(
        self, chat_id: int, owner_tg: int, price_micro: int, period_days: int = 30
    ) -> bool:
        """Register a channel whose access costs price_micro per period_days.

        Returns False when the per-user channel cap is reached (anti-spam).
        """
        self.ensure_user(owner_tg, None)
        with self._lock:
            if self._conn.execute(
                "SELECT 1 FROM paywall_channels WHERE chat_id = %s", (chat_id,)
            ).fetchone() is None:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM paywall_channels WHERE owner_tg = %s",
                    (owner_tg,),
                ).fetchone()
                if int(row["c"]) >= config.PAYWALL_MAX_CHANNELS_PER_USER:
                    return False
            self._conn.execute(
                "INSERT INTO paywall_channels (chat_id, owner_tg, price_micro, period_days) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (chat_id) DO UPDATE SET "
                "owner_tg = EXCLUDED.owner_tg, price_micro = EXCLUDED.price_micro, "
                "period_days = EXCLUDED.period_days",
                (chat_id, owner_tg, price_micro, period_days),
            )
            self._conn.commit()
            return True



    def disable_paywall_channel(self, chat_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM paywall_channels WHERE chat_id = %s", (chat_id,))
            self._conn.commit()



    def paywall_channel(self, chat_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM paywall_channels WHERE chat_id = %s", (chat_id,)
            ).fetchone()



    def paywall_channels_list(self) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM paywall_channels ORDER BY created_at DESC"
            ).fetchall()



    def channel_subscription(self, chat_id: int, tg_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM paywall_subscriptions WHERE chat_id = %s AND tg_id = %s",
                (chat_id, tg_id),
            ).fetchone()



    def subscribe_channel(self, chat_id: int, tg_id: int) -> str:
        """Buy (or extend) access to a paid channel from the internal balance.

        Returns 'ok', 'missing' (channel not for sale) or 'insufficient'.
        An active subscription is extended from its current expiry.
        """
        with self._lock:
            ch = self._conn.execute(
                "SELECT owner_tg, price_micro, period_days FROM paywall_channels WHERE chat_id = %s",
                (chat_id,),
            ).fetchone()
            if ch is None:
                self._conn.rollback()
                return "missing"
            if tg_id == int(ch["owner_tg"]):
                self._conn.rollback()
                return "self"  # the owner already has access; no self-purchase
            price = int(ch["price_micro"])
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (price, tg_id, price),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return "insufficient"
            # Extend ATOMICALLY on the current committed row: GREATEST(existing,
            # now) + period, computed by Postgres inside the upsert. Two
            # concurrent cross-process renewals (bot + web share one DB) each
            # pay AND each add their own period on top of the other's commit; a
            # read-then-write window would otherwise double-charge a fresh
            # subscription for a single extension.
            now = time.time()
            period = int(ch["period_days"]) * 86400
            self._conn.execute(
                "INSERT INTO paywall_subscriptions (chat_id, tg_id, expires_at) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (chat_id, tg_id) DO UPDATE SET expires_at = "
                "GREATEST(COALESCE(paywall_subscriptions.expires_at, 0), %s) + %s",
                (chat_id, tg_id, int(now) + period, int(now), period),
            )
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (price, int(ch["owner_tg"])),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('channel_pay', %s, %s, %s, 'подписка на канал')",
                (tg_id, str(chat_id), -price),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('channel_earn', %s, %s, %s, 'продажа доступа')",
                (int(ch["owner_tg"]), str(chat_id), price),
            )
            self._conn.commit()
            return "ok"



    def active_channel_subscriptions(self) -> list[dict]:
        """All subscriptions, so the watcher can kick expired ones."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM paywall_subscriptions ORDER BY expires_at ASC"
            ).fetchall()



    def expire_channel_subscription(self, chat_id: int, tg_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM paywall_subscriptions WHERE chat_id = %s AND tg_id = %s",
                (chat_id, tg_id),
            )
            self._conn.commit()
