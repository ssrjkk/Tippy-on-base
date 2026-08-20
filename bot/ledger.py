"""PostgreSQL ledger: internal USDC balances, wallet links, history.

Tips move instantly inside this ledger (no gas, no 12s wait).
Deposits credit here; withdrawals debit here and send USDC on-chain.
"""

import json
import secrets
import threading
import time
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

import psycopg
from psycopg.rows import dict_row

from . import config

MICRO = 10**config.USDC_DECIMALS


class ReconnectingConn:
    """psycopg proxy that transparently reconnects after a server restart.

    PostgreSQL (docker restart, failover, idle timeout) kills pooled
    connections; a dead connection would make every later ledger call raise
    forever. This wrapper detects closed/broken connections and reconnects
    before the next statement.

    Reconnect/retry happens only while the connection is IDLE (no open
    transaction): retrying mid-transaction could drop an earlier statement of
    the same transaction (e.g. the debit in reserve_withdraw). Mid-transaction
    failures propagate to the caller, whose watchers/handlers already refund
    and retry from a clean state.
    """

    def __init__(self, database: str) -> None:
        self._database = database
        self._conn = psycopg.connect(
            database, row_factory=dict_row, connect_timeout=15
        )

    def _connect(self) -> None:
        self._conn = psycopg.connect(
            self._database, row_factory=dict_row, connect_timeout=15
        )

    def _ensure(self) -> None:
        if self._conn.closed or self._conn.broken:
            try:
                self._conn.close()
            except Exception:
                pass
            self._connect()

    @property
    def closed(self) -> bool:
        return self._conn.closed

    @property
    def broken(self) -> bool:
        return self._conn.broken

    def execute(self, query, params=None, **kwargs):
        self._ensure()
        try:
            return self._conn.execute(query, params, **kwargs)
        except psycopg.OperationalError:
            # Retry once only on a dead connection with nothing to roll back
            # (no open transaction). Other operational errors propagate.
            if self._conn.broken or self._conn.closed:
                self._connect()
                return self._conn.execute(query, params, **kwargs)
            if self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                raise
            self._connect()
            return self._conn.execute(query, params, **kwargs)

    def commit(self) -> None:
        self._ensure()
        try:
            self._conn.commit()
        except psycopg.OperationalError:
            # The server rolled back our uncommitted transaction when the
            # connection dropped; there is nothing left to commit.
            pass

    def rollback(self) -> None:
        self._ensure()
        try:
            self._conn.rollback()
        except psycopg.OperationalError:
            pass

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class Ledger:
    def __init__(self, database: str = config.DATABASE_URL) -> None:
        # One shared connection, serialized by an RLock (the bot and the web
        # dashboard run in the same process). PostgreSQL handles the actual
        # concurrency; the lock keeps statement ordering deterministic.
        self._lock = threading.RLock()
        self._conn = ReconnectingConn(database)
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    tg_id       BIGINT PRIMARY KEY,
                    username    TEXT,
                    balance     BIGINT NOT NULL DEFAULT 0,  -- USDC micro-units (1e6)
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS tx_log (
                    id        BIGSERIAL PRIMARY KEY,
                    kind      TEXT NOT NULL,             -- deposit | tip | withdraw
                    tg_id     BIGINT NOT NULL,
                    counterparty TEXT,
                    amount    BIGINT NOT NULL,           -- micro-units
                    tx_hash   TEXT,
                    note      TEXT,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS pending_deposits (
                    tx_hash      TEXT PRIMARY KEY,
                    sender       TEXT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    claimed      BIGINT NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS link_nonces (
                    tg_id       BIGINT PRIMARY KEY,
                    address     TEXT NOT NULL,
                    nonce       TEXT NOT NULL,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS wallet_links (
                    tg_id     BIGINT PRIMARY KEY,
                    address   TEXT NOT NULL UNIQUE,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS bets (
                    id          BIGSERIAL PRIMARY KEY,
                    creator     BIGINT NOT NULL,
                    question    TEXT NOT NULL,
                    options     TEXT NOT NULL,                -- JSON array
                    status      TEXT NOT NULL DEFAULT 'open', -- open | resolved | cancelled
                    winner      BIGINT,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS bet_positions (
                    id           BIGSERIAL PRIMARY KEY,
                    bet_id       BIGINT NOT NULL,
                    tg_id        BIGINT NOT NULL,
                    option_idx   BIGINT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE INDEX IF NOT EXISTS idx_bet_positions_bet ON bet_positions (bet_id);
                CREATE TABLE IF NOT EXISTS last_block (
                    id    BIGINT PRIMARY KEY CHECK (id = 1),
                    block BIGINT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS message_authors (
                    chat_id    BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    tg_id      BIGINT NOT NULL,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS reaction_tips (
                    chat_id    BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    tg_id      BIGINT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    PRIMARY KEY (chat_id, message_id, tg_id)
                );
                CREATE INDEX IF NOT EXISTS idx_reaction_tips_tg ON reaction_tips (tg_id);
                CREATE TABLE IF NOT EXISTS user_settings (
                    tg_id         BIGINT PRIMARY KEY,
                    reaction_tips BIGINT NOT NULL DEFAULT 1,  -- allow emoji-reaction tips
                    notify_deposits BIGINT NOT NULL DEFAULT 1 -- DM on credited deposit
                );
                CREATE TABLE IF NOT EXISTS user_wallets (
                    tg_id      BIGINT PRIMARY KEY,
                    address    TEXT NOT NULL UNIQUE,
                    key_enc    TEXT NOT NULL,
                    seed_enc   TEXT NOT NULL,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS x402_payments (
                    tx_hash      TEXT PRIMARY KEY,
                    recipient_tg BIGINT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    sender       TEXT NOT NULL,
                    created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS paywall_items (
                    id          BIGSERIAL PRIMARY KEY,
                    owner_tg    BIGINT NOT NULL,
                    title       TEXT NOT NULL,
                    price_micro BIGINT NOT NULL,
                    content     TEXT NOT NULL,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS paywall_purchases (
                    id          BIGSERIAL PRIMARY KEY,
                    item_id     BIGINT NOT NULL,
                    buyer_tg    BIGINT,
                    tx_hash     TEXT,
                    amount_micro BIGINT NOT NULL,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    UNIQUE (item_id, buyer_tg),
                    UNIQUE (item_id, tx_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_paywall_purchases_item ON paywall_purchases (item_id);
                CREATE TABLE IF NOT EXISTS paywall_channels (
                    chat_id     BIGINT PRIMARY KEY,
                    owner_tg    BIGINT NOT NULL,
                    price_micro BIGINT NOT NULL,
                    period_days BIGINT NOT NULL DEFAULT 30,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS paywall_subscriptions (
                    chat_id    BIGINT NOT NULL,
                    tg_id      BIGINT NOT NULL,
                    expires_at BIGINT NOT NULL,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    PRIMARY KEY (chat_id, tg_id)
                );
                """
            )
            # Schema DDL needs ACCESS EXCLUSIVE locks, which a concurrent
            # process holding read locks (e.g. the web dashboard) can block
            # indefinitely. Fail fast instead of hanging forever.
            self._conn.execute("SET lock_timeout = '15s'")
            self._conn.execute("ALTER TABLE bets ADD COLUMN IF NOT EXISTS close_at BIGINT")
            self._conn.execute("ALTER TABLE tx_log ADD COLUMN IF NOT EXISTS status TEXT")
            self._conn.execute(
                "ALTER TABLE bets ADD COLUMN IF NOT EXISTS deadline_notified BIGINT NOT NULL DEFAULT 0"
            )
            self._conn.execute(
                "ALTER TABLE bets ADD COLUMN IF NOT EXISTS grace_warned BIGINT NOT NULL DEFAULT 0"
            )
            self._conn.commit()

    # ---------- users ----------

    def ensure_user(self, tg_id: int, username: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (tg_id, username) VALUES (%s, %s) ON CONFLICT (tg_id) DO NOTHING",
                (tg_id, username),
            )
            self._conn.execute(
                "UPDATE users SET username = %s WHERE tg_id = %s AND %s::text IS NOT NULL",
                (username, tg_id, username),
            )
            self._conn.commit()

    def user_exists(self, tg_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return row is not None

    def all_users(self) -> list[dict]:
        with self._lock:
            return self._conn.execute("SELECT tg_id FROM users").fetchall()

    def find_by_username(self, username: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tg_id FROM users WHERE LOWER(username) = LOWER(%s)",
                (username,),
            ).fetchone()
        return row["tg_id"] if row else None

    def username_of(self, tg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT username FROM users WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return row["username"] if row else None

    def balance(self, tg_id: int) -> Decimal:
        with self._lock:
            row = self._conn.execute(
                "SELECT balance FROM users WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return Decimal(row["balance"] if row else 0) / Decimal(MICRO)

    def set_username(self, tg_id: int, username: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET username = %s WHERE tg_id = %s", (username, tg_id)
            )
            self._conn.commit()

    # ---------- wallet linking ----------

    def new_link_nonce(self, tg_id: int, address: str) -> str:
        with self._lock:
            self.ensure_user(tg_id, None)
            nonce = secrets.token_hex(8)
            self._conn.execute(
                "INSERT INTO link_nonces (tg_id, address, nonce, created_at) "
                "VALUES (%s, %s, %s, (EXTRACT(EPOCH FROM now())::bigint)) "
                "ON CONFLICT (tg_id) DO UPDATE SET "
                "address = EXCLUDED.address, nonce = EXCLUDED.nonce, "
                "created_at = EXCLUDED.created_at",
                (tg_id, address, nonce),
            )
            self._conn.commit()
        return nonce

    def get_link_nonce(self, tg_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT address, nonce, created_at FROM link_nonces WHERE tg_id = %s",
                (tg_id,),
            ).fetchone()

    def confirm_link(self, tg_id: int, address: str, nonce: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT address, created_at FROM link_nonces WHERE tg_id = %s AND nonce = %s",
                (tg_id, nonce),
            ).fetchone()
            if not row or row["address"].lower() != address.lower():
                return False
            if int(time.time()) - int(row["created_at"]) > config.LINK_NONCE_TTL_SECONDS:
                self._conn.execute("DELETE FROM link_nonces WHERE tg_id = %s", (tg_id,))
                self._conn.commit()
                return False
            self._conn.execute(
                "INSERT INTO wallet_links (tg_id, address) VALUES (%s, %s) "
                "ON CONFLICT (tg_id) DO UPDATE SET address = EXCLUDED.address",
                (tg_id, address),
            )
            self._conn.execute(
                "DELETE FROM link_nonces WHERE tg_id = %s", (tg_id,)
            )
            self._conn.commit()
            return True

    def linked_address(self, tg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT address FROM wallet_links WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return row["address"] if row else None

    def tg_id_of_address(self, address: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tg_id FROM wallet_links WHERE LOWER(address) = LOWER(%s)",
                (address,),
            ).fetchone()
        return row["tg_id"] if row else None

    # ---------- balances / transfers ----------

    def credit(self, tg_id: int, amount_micro: int, kind: str, counterparty: str = "", tx_hash: str = "", note: str = "") -> None:
        with self._lock:
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (amount_micro, tg_id),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, note) VALUES (%s, %s, %s, %s, %s, %s)",
                (kind, tg_id, counterparty, amount_micro, tx_hash, note),
            )
            self._conn.commit()

    def credit_x402(self, recipient_tg: int, tx_hash: str, amount_micro: int, sender: str) -> bool:
        """Credit an on-chain x402 payment to a user. Atomic and replay-proof.

        The tx_hash is the PK of x402_payments: a second verification of the
        same transaction returns False (never double-credit). The deposit
        scanner skips these tx hashes, so liabilities stay exact.
        """
        with self._lock:
            self.ensure_user(recipient_tg, None)
            cur = self._conn.execute(
                "INSERT INTO x402_payments (tx_hash, recipient_tg, amount_micro, sender) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING",
                (tx_hash, recipient_tg, amount_micro, sender),
            )
            if cur.rowcount == 0:
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

    def x402_paid(self, tx_hash: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM x402_payments WHERE tx_hash = %s", (tx_hash,)
            ).fetchone()
            return row is not None

    # ---------- paywall (paid content) ----------

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
                return "insufficient"
            self._conn.execute(
                "INSERT INTO paywall_purchases (item_id, buyer_tg, tx_hash, amount_micro) "
                "VALUES (%s, %s, NULL, %s)",
                (item_id, buyer_tg, price),
            )
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
        self, owner_tg: int, item_id: int, tx_hash: str, amount_micro: int, sender: str
    ) -> str:
        """Credit an x402 payment for a paywall item. Atomic and replay-proof.

        Returns 'ok', or 'replay' when this tx was already processed (either
        as a tip or as this purchase). The tx hash stays the PK of
        x402_payments, so the deposit scanner can never double-credit it.
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO x402_payments (tx_hash, recipient_tg, amount_micro, sender) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING",
                (tx_hash, owner_tg, amount_micro, sender),
            )
            if cur.rowcount == 0:
                return "replay"
            if self._conn.execute(
                "SELECT 1 FROM paywall_purchases WHERE item_id = %s AND tx_hash = %s",
                (item_id, tx_hash),
            ).fetchone():
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

    # ---------- channel paywall (paid access to channels) ----------

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
                return "missing"
            if tg_id == int(ch["owner_tg"]):
                return "self"  # the owner already has access; no self-purchase
            price = int(ch["price_micro"])
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (price, tg_id, price),
            )
            if cur.rowcount == 0:
                return "insufficient"
            row = self._conn.execute(
                "SELECT expires_at FROM paywall_subscriptions WHERE chat_id = %s AND tg_id = %s",
                (chat_id, tg_id),
            ).fetchone()
            now = time.time()
            base_ts = int(row["expires_at"]) if row and int(row["expires_at"]) > now else int(now)
            expires = base_ts + int(ch["period_days"]) * 86400
            self._conn.execute(
                "INSERT INTO paywall_subscriptions (chat_id, tg_id, expires_at) "
                "VALUES (%s, %s, %s) ON CONFLICT (chat_id, tg_id) DO UPDATE SET "
                "expires_at = EXCLUDED.expires_at",
                (chat_id, tg_id, expires),
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

    def transfer(self, from_id: int, to_id: int, amount_micro: int) -> bool:
        """Move funds between users. Returns False if sender lacks balance."""
        with self._lock:
            self.ensure_user(to_id, None)
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (amount_micro, from_id, amount_micro),
            )
            if cur.rowcount == 0:
                return False
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (amount_micro, to_id),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount) VALUES ('tip', %s, %s, %s)",
                (from_id, str(to_id), amount_micro),
            )
            self._conn.commit()
            return True

    def debit(self, tg_id: int, amount_micro: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (amount_micro, tg_id, amount_micro),
            )
            return cur.rowcount > 0

    def reserve_withdraw(
        self, tg_id: int, to_address: str, amount_micro: int, fee_micro: int
    ) -> int | None:
        """Atomically debit amount+fee and reserve a 'pending' withdrawal row.

        Returns the tx_log id, or None if the user lacks the balance. The row is
        written BEFORE any on-chain send, so the withdraw watcher can refund it if
        the process crashes between debit and send (tx_hash stays NULL).
        """
        with self._lock:
            total = amount_micro + fee_micro
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (total, tg_id, total),
            )
            if cur.rowcount == 0:
                return None
            cur = self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note, status) "
                "VALUES ('withdraw', %s, %s, %s, %s, 'pending') RETURNING id",
                (tg_id, to_address, amount_micro, f"fee={fee_micro}"),
            )
            self._conn.commit()
            return int(cur.fetchone()["id"])

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
        """Sum of all internal user balances in micro-units (what the hot wallet
        must be able to cover)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(balance), 0) AS s FROM users"
            ).fetchone()
        return int(row["s"])

    def pending_deposit_total(self) -> int:
        """Sum of unclaimed pending deposits in micro-units (funds held on-chain
        that the bot may still owe once claimed)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount_micro), 0) AS s "
                "FROM pending_deposits WHERE claimed = 0"
            ).fetchone()
        return int(row["s"])

    # ---------- deposits ----------

    def record_pending(self, tx_hash: str, sender: str, amount_micro: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO pending_deposits (tx_hash, sender, amount_micro) "
                "VALUES (%s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING",
                (tx_hash, sender, amount_micro),
            )
            self._conn.commit()

    def claim(self, tg_id: int, tx_hash: str) -> tuple[bool, int, str, str]:
        """Credit a pending deposit to a user. Returns (ok, amount_micro, sender, reason).

        Security: only the owner of the *sending* wallet may claim. Deposits are
        public on-chain, so a tx hash is not a secret — without this check anyone
        could /claim somebody else's funds. reason is '' on success, otherwise
        'not_found' | 'claimed' | 'not_owner'.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT sender, amount_micro, claimed FROM pending_deposits WHERE tx_hash = %s",
                (tx_hash,),
            ).fetchone()
            if not row:
                return False, 0, "", "not_found"
            if row["claimed"]:
                return False, 0, row["sender"], "claimed"
            linked = self.linked_address(tg_id)
            if not linked or linked.lower() != row["sender"].lower():
                return False, 0, row["sender"], "not_owner"
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "UPDATE pending_deposits SET claimed = 1 WHERE tx_hash = %s", (tx_hash,)
            )
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

    def claim_for_sender(self, tg_id: int, sender: str) -> list[dict]:
        """Auto-claim ALL pending deposits from a linked sender address."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT tx_hash, amount_micro FROM pending_deposits "
                "WHERE LOWER(sender) = LOWER(%s) AND claimed = 0",
                (sender,),
            ).fetchall()
            self.ensure_user(tg_id, None)
            for row in rows:
                self._conn.execute(
                    "UPDATE pending_deposits SET claimed = 1 WHERE tx_hash = %s",
                    (row["tx_hash"],),
                )
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

    # ---------- stats ----------

    def history(self, tg_id: int, limit: int = 15) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, counterparty, amount, tx_hash, note, created_at "
                "FROM tx_log WHERE tg_id = %s ORDER BY id DESC LIMIT %s",
                (tg_id, limit),
            ).fetchall()
        return rows

    def withdrawals_today(self, tg_id: int) -> int:
        """Successful withdrawals in the last 24h (anti gas-griefing)."""
        since = int(time.time()) - 86400
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM tx_log "
                "WHERE tg_id = %s AND kind = 'withdraw' "
                "AND COALESCE(status, 'done') = 'done' AND created_at >= %s",
                (tg_id, since),
            ).fetchone()
        return int(row["c"])

    def pending_withdraws(self) -> list[dict]:
        """Withdraw rows not yet confirmed: status IS NULL (legacy), 'pending'."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, tg_id, counterparty, amount, tx_hash, status, created_at "
                "FROM tx_log WHERE kind = 'withdraw' "
                "AND COALESCE(status, '') NOT IN ('done', 'refunded') ORDER BY id"
            ).fetchall()

    def mark_withdraw_done(self, wd_id: int, tx_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tx_log SET tx_hash = %s, status = 'done' WHERE id = %s",
                (tx_hash, wd_id),
            )
            self._conn.commit()

    def refund_withdraw(self, wd_id: int, tg_id: int, total_micro: int) -> None:
        """Full refund of amount + fee; keeps the row as an audit trail."""
        with self._lock:
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (total_micro, tg_id),
            )
            self._conn.execute(
                "UPDATE tx_log SET status = 'refunded' WHERE id = %s", (wd_id,)
            )
            self._conn.commit()

    def top_tippers(self, limit: int = 10, since_days: int | None = None) -> list[dict]:
        since = None
        if since_days:
            since = int(datetime.now(UTC).timestamp()) - since_days * 86400
        with self._lock:
            if since:
                rows = self._conn.execute(
                    "SELECT tg_id, SUM(amount) AS total FROM tx_log "
                    "WHERE kind = 'tip' AND created_at >= %s "
                    "GROUP BY tg_id ORDER BY total DESC LIMIT %s",
                    (since, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT tg_id, SUM(amount) AS total FROM tx_log "
                    "WHERE kind = 'tip' GROUP BY tg_id ORDER BY total DESC LIMIT %s",
                    (limit,),
                ).fetchall()
        return rows

    # ---------- bets (prediction markets, off-chain) ----------

    def create_bet(self, creator_tg_id: int, question: str, options: list[str], close_at: int | None = None) -> int:
        with self._lock:
            self.ensure_user(creator_tg_id, None)
            cur = self._conn.execute(
                "INSERT INTO bets (creator, question, options, close_at) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (creator_tg_id, question, json.dumps(options), close_at),
            )
            self._conn.commit()
            return int(cur.fetchone()["id"])

    def get_bet(self, bet_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM bets WHERE id = %s", (bet_id,)
            ).fetchone()

    def open_bets(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM bets WHERE status = 'open' ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()

    def open_bets_past_deadline(self) -> list[dict]:
        """Open markets whose deadline passed and whose creator wasn't yet
        asked to resolve. Without resolution, backers' money is stuck until
        the grace-refund path, so the watcher pings the creator once."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, creator, question FROM bets "
                "WHERE status = 'open' AND close_at IS NOT NULL "
                "AND close_at <= %s AND deadline_notified = 0",
                (int(time.time()),),
            ).fetchall()

    def mark_deadline_notified(self, bet_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE bets SET deadline_notified = 1 WHERE id = %s", (bet_id,)
            )
            self._conn.commit()

    def bets_need_grace_warning(self, warn_before: int) -> list[dict]:
        """Open markets whose grace period ends within `warn_before` seconds.
        The deadline ping already went out; this is the second, final nudge
        before anyone can refund the market and the creator loses the fee."""
        grace = config.MARKET_GRACE_HOURS * 3600
        with self._lock:
            return self._conn.execute(
                "SELECT id, creator, question, close_at FROM bets "
                "WHERE status = 'open' AND close_at IS NOT NULL "
                "AND deadline_notified = 1 AND grace_warned = 0 "
                "AND %s >= close_at + %s - %s",
                (int(time.time()), grace, warn_before),
            ).fetchall()

    def mark_grace_warned(self, bet_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE bets SET grace_warned = 1 WHERE id = %s", (bet_id,)
            )
            self._conn.commit()

    def bets_by_status(self, status: str, limit: int = 20) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM bets WHERE status = %s ORDER BY id DESC LIMIT %s",
                (status, limit),
            ).fetchall()

    def bet_totals(self, bet_id: int) -> dict[int, int]:
        """option_idx -> total stake in micro-units."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT option_idx, SUM(amount_micro) AS total FROM bet_positions "
                "WHERE bet_id = %s GROUP BY option_idx",
                (bet_id,),
            ).fetchall()
        return {int(r["option_idx"]): int(r["total"]) for r in rows}

    def _bet_positions(self, bet_id: int) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT tg_id, option_idx, SUM(amount_micro) AS amount "
                "FROM bet_positions WHERE bet_id = %s "
                "GROUP BY tg_id, option_idx",
                (bet_id,),
            ).fetchall()

    def place_bet(self, bet_id: int, tg_id: int, option_idx: int, amount_micro: int) -> str:
        """Returns 'ok' | 'closed' | 'deadline' | 'badopt' | 'balance'."""
        with self._lock:
            bet = self.get_bet(bet_id)
            if not bet or bet["status"] != "open":
                return "closed"
            if bet["close_at"] is not None and int(time.time()) > bet["close_at"]:
                return "deadline"
            options = json.loads(bet["options"])
            if option_idx < 0 or option_idx >= len(options):
                return "badopt"
            if not self.debit(tg_id, amount_micro):
                return "balance"
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "INSERT INTO bet_positions (bet_id, tg_id, option_idx, amount_micro) VALUES (%s, %s, %s, %s)",
                (bet_id, tg_id, option_idx, amount_micro),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) VALUES ('bet', %s, %s, %s, %s)",
                (tg_id, str(bet_id), amount_micro, options[option_idx]),
            )
            self._conn.commit()
            return "ok"

    def resolve_bet(self, bet_id: int, winning_idx: int, resolver_id: int) -> tuple[bool, str]:
        """Parimutuel: winners share the whole pot; 2% fee on net profit.

        Money conservation is exact: winners get net payouts, the market
        creator receives all fees + floor dust, so nothing is created or lost.
        """
        with self._lock:
            bet = self.get_bet(bet_id)
            if not bet or bet["status"] != "open":
                return False, "Ставка не найдена или уже закрыта."
            if bet["creator"] != resolver_id:
                return False, "Закрыть может только создатель ставки."
            options = json.loads(bet["options"])
            if winning_idx < 0 or winning_idx >= len(options):
                return False, "Неверный номер варианта."

            positions = self._bet_positions(bet_id)
            total_pot = sum(int(p["amount"]) for p in positions)
            if total_pot <= 0:
                return False, "В ставке пока нет денег — закрыть нечего."

            winners = [p for p in positions if int(p["option_idx"]) == winning_idx]
            if not winners:
                return False, "Никто не поставил на этот вариант."

            win_stake = sum(int(p["amount"]) for p in winners)
            gross_sum = 0
            fee_sum = 0
            payouts: list[tuple[int, int]] = []
            for p in winners:
                amt = int(p["amount"])
                gross = amt * total_pot // win_stake
                gross_sum += gross
                profit = gross - amt
                fee = 0
                if profit > 0:
                    fee = (Decimal(profit) * config.WIN_FEE_PCT).to_integral_value(rounding=ROUND_CEILING)
                payouts.append((int(p["tg_id"]), gross - int(fee)))
                fee_sum += int(fee)

            remainder = total_pot - gross_sum  # >= 0 floor dust
            creator_income = fee_sum + remainder

            for tg_id, net in payouts:
                self.credit(
                    tg_id,
                    net,
                    "bet_win",
                    counterparty=str(bet_id),
                    note=bet["question"],
                )
            if creator_income > 0:
                self.credit(
                    bet["creator"],
                    creator_income,
                    "fee",
                    counterparty=str(bet_id),
                    note="market fees",
                )
            self._conn.execute(
                "UPDATE bets SET status = 'resolved', winner = %s WHERE id = %s",
                (winning_idx, bet_id),
            )
            self._conn.commit()
            return True, f"Победил вариант {winning_idx + 1} — {options[winning_idx]}"

    def is_expired(self, bet: dict) -> bool:
        """Deadline passed and grace period elapsed: anyone may refund."""
        if bet["close_at"] is None:
            return False
        grace = config.MARKET_GRACE_HOURS * 3600
        return int(time.time()) > bet["close_at"] + grace

    def cancel_bet(self, bet_id: int, resolver_id: int) -> tuple[bool, str]:
        """Refund all backers. Creator always; anyone once grace passed."""
        with self._lock:
            bet = self.get_bet(bet_id)
            if not bet or bet["status"] != "open":
                return False, "Ставка не найдена или уже закрыта."
            if bet["creator"] != resolver_id and not self.is_expired(bet):
                return False, "Отменить может только создатель ставки (или после дедлайна + grace)."
            refunded_by_creator = bet["creator"] == resolver_id
            for p in self._bet_positions(bet_id):
                self.credit(
                    int(p["tg_id"]),
                    int(p["amount"]),
                    "bet_cancel",
                    counterparty=str(bet_id),
                    note=bet["question"],
                )
            self._conn.execute(
                "UPDATE bets SET status = 'cancelled' WHERE id = %s", (bet_id,)
            )
            self._conn.commit()
            if refunded_by_creator:
                return True, "Ставка отменена, деньги возвращены."
            return True, "Рынок истёк — деньги возвращены всем участникам."

    # ---------- reaction tips ----------

    def record_message(self, chat_id: int, message_id: int, tg_id: int) -> None:
        """Index message -> author so reactions can tip the author."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO message_authors (chat_id, message_id, tg_id) "
                "VALUES (%s, %s, %s) ON CONFLICT (chat_id, message_id) DO NOTHING",
                (chat_id, message_id, tg_id),
            )
            self._conn.commit()

    def message_author(self, chat_id: int, message_id: int) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tg_id FROM message_authors WHERE chat_id = %s AND message_id = %s",
                (chat_id, message_id),
            ).fetchone()
        return int(row["tg_id"]) if row else None

    def tip_by_reaction(
        self, chat_id: int, message_id: int, reactor_id: int, amount_micro: int
    ) -> tuple[bool, str, int | None]:
        """Reaction tip: one per user per message. Returns (ok, reason, author_id)."""
        author = self.message_author(chat_id, message_id)
        if author is None:
            return False, "author_missing", None
        if author == reactor_id:
            return False, "self", author
        with self._lock:
            dup = self._conn.execute(
                "SELECT 1 FROM reaction_tips WHERE chat_id = %s AND message_id = %s AND tg_id = %s",
                (chat_id, message_id, reactor_id),
            ).fetchone()
            if dup:
                return False, "duplicate", author
            if not self.debit(reactor_id, amount_micro):
                return False, "balance", author
            self.credit(author, amount_micro, "tip", counterparty=str(reactor_id), note="reaction")
            self._conn.execute(
                "INSERT INTO reaction_tips (chat_id, message_id, tg_id, amount_micro) VALUES (%s, %s, %s, %s)",
                (chat_id, message_id, reactor_id, amount_micro),
            )
            self._conn.commit()
            return True, "ok", author

    def prune_message_index(self, older_than_seconds: int) -> int:
        """Drop message-author index rows older than N seconds.

        The index only exists so reaction tips/rain can resolve recent
        messages; without pruning it grows forever in active groups. Rows
        newer than the retention window are always kept (Telegram keeps
        reactions for ~90 days anyway). Returns the number of rows removed.
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM message_authors WHERE created_at < %s",
                (int(time.time()) - older_than_seconds,),
            )
            self._conn.commit()
            return cur.rowcount

    # ---------- user settings ----------

    def get_settings(self, tg_id: int) -> dict:
        with self._lock:
            self.ensure_user(tg_id, None)
            row = self._conn.execute(
                "SELECT reaction_tips, notify_deposits FROM user_settings WHERE tg_id = %s",
                (tg_id,),
            ).fetchone()
        if row:
            return {
                "reaction_tips": bool(row["reaction_tips"]),
                "notify_deposits": bool(row["notify_deposits"]),
            }
        return {"reaction_tips": True, "notify_deposits": True}

    def set_setting(self, tg_id: int, key: str, value: bool) -> None:
        if key not in ("reaction_tips", "notify_deposits"):
            raise ValueError(f"unknown setting: {key}")
        with self._lock:
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "INSERT INTO user_settings (tg_id) VALUES (%s) ON CONFLICT (tg_id) DO NOTHING",
                (tg_id,)
            )
            self._conn.execute(
                f"UPDATE user_settings SET {key} = %s WHERE tg_id = %s",
                (1 if value else 0, tg_id),
            )
            self._conn.commit()

    # ---------- per-user wallets (self-custody export/import) ----------

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
                "INSERT INTO user_wallets (tg_id, address, key_enc, seed_enc) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (tg_id) DO UPDATE SET address = EXCLUDED.address, "
                "key_enc = EXCLUDED.key_enc, seed_enc = EXCLUDED.seed_enc",
                (tg_id, address, key_enc, seed_enc),
            )
            self._conn.commit()

    # ---------- rain (group giveaway) ----------

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
            self.ensure_user(sender_id, None)
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (total, sender_id, total),
            )
            if cur.rowcount == 0:
                return False, "Недостаточно баланса. Пополни: /deposit", []
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('tip', %s, %s, %s, 'rain')",
                (sender_id, ",".join(map(str, chosen)), total),
            )
            for to_id in chosen:
                self.ensure_user(to_id, None)
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

    # ---------- user positions ----------

    def user_bet_stake(self, bet_id: int, tg_id: int) -> dict[int, int]:
        """option_idx -> total stake of this user in a market."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT option_idx, SUM(amount_micro) AS amount FROM bet_positions "
                "WHERE bet_id = %s AND tg_id = %s GROUP BY option_idx",
                (bet_id, tg_id),
            ).fetchall()
        return {int(r["option_idx"]): int(r["amount"]) for r in rows}

    def user_positions(self, tg_id: int) -> list[dict]:
        """Open bets where the user has a position: stake, current pool, potential payout."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT bet_id, option_idx, SUM(amount_micro) AS amount "
                "FROM bet_positions WHERE tg_id = %s "
                "GROUP BY bet_id, option_idx",
                (tg_id,),
            ).fetchall()
        out = []
        for r in rows:
            bet = self.get_bet(int(r["bet_id"]))
            if not bet or bet["status"] != "open":
                continue
            options = json.loads(bet["options"])
            totals = self.bet_totals(int(r["bet_id"]))
            pot = sum(totals.values())
            opt_idx = int(r["option_idx"])
            stake = int(r["amount"])
            win_stake = totals.get(opt_idx, 0)
            gross = stake * pot // win_stake if win_stake else 0
            out.append(
                {
                    "bet_id": int(r["bet_id"]),
                    "question": bet["question"],
                    "option": options[opt_idx],
                    "option_idx": opt_idx,
                    "stake_micro": stake,
                    "potential_micro": gross,
                    "close_at": bet["close_at"],
                }
            )
        return out

    # ---------- stats ----------

    def user_stats(self, tg_id: int) -> tuple[int, int, int, int]:
        """(tips_sent, tips_received, bets_won, bets_lost) in micro-units."""
        with self._lock:
            sent = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE tg_id = %s AND kind = 'tip'",
                (tg_id,),
            ).fetchone()["s"]
            received = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log "
                "WHERE kind = 'tip' AND counterparty = %s",
                (str(tg_id),),
            ).fetchone()["s"]
            received += self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log "
                "WHERE kind = 'x402' AND tg_id = %s",
                (tg_id,),
            ).fetchone()["s"]
            won = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE tg_id = %s AND kind = 'bet_win'",
                (tg_id,),
            ).fetchone()["s"]
            lost = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE tg_id = %s AND kind = 'bet'",
                (tg_id,),
            ).fetchone()["s"]
        return int(sent), int(received), int(won), int(lost)

    def creator_fees(self, tg_id: int) -> int:
        """Total 2% win-fee income this user earned as a market creator."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log "
                "WHERE tg_id = %s AND kind = 'fee' AND note = 'market fees'",
                (tg_id,),
            ).fetchone()
        return int(row["s"])

    # ---------- read API (web dashboard) ----------

    def market_view(self, bet_id: int) -> dict | None:
        """Public view of a market: options, pools, implied probability, status."""
        bet = self.get_bet(bet_id)
        if not bet:
            return None
        options = json.loads(bet["options"])
        totals = self.bet_totals(bet_id)
        pot = sum(totals.values())
        backers = self._backers_per_option(bet_id)
        items = []
        for i, opt in enumerate(options):
            pool = totals.get(i, 0)
            prob = round(pool / pot * 100, 1) if pot else 0.0
            items.append(
                {
                    "index": i,
                    "label": opt,
                    "pool": pool,
                    "probability": prob,
                    "backers": backers.get(i, 0),
                }
            )
        creator_name = self.username_of(bet["creator"])
        return {
            "id": bet["id"],
            "question": bet["question"],
            "status": bet["status"],
            "winner": bet["winner"],
            "creator": {"id": bet["creator"], "username": creator_name},
            "options": items,
            "pot": pot,
            "total_backers": sum(backers.values()),
            "close_at": bet["close_at"],
            "expired": bet["status"] == "open" and self.is_expired(bet),
            "created_at": bet["created_at"],
        }

    def _backers_per_option(self, bet_id: int) -> dict[int, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT option_idx, COUNT(DISTINCT tg_id) AS c FROM bet_positions "
                "WHERE bet_id = %s GROUP BY option_idx",
                (bet_id,),
            ).fetchall()
        return {int(r["option_idx"]): int(r["c"]) for r in rows}

    def payouts_for(self, bet_id: int) -> list[dict]:
        """Per-backer outcome of a RESOLVED market (deterministic re-computation
        of the parimutuel math used by resolve_bet). Used for result DMs."""
        with self._lock:
            bet = self.get_bet(bet_id)
            if not bet or bet["status"] != "resolved" or bet["winner"] is None:
                return []
            options = json.loads(bet["options"])
            positions = self._bet_positions(bet_id)
        total_pot = sum(int(p["amount"]) for p in positions)
        winners = [p for p in positions if int(p["option_idx"]) == int(bet["winner"])]
        win_stake = sum(int(p["amount"]) for p in winners)
        out = []
        for p in positions:
            amt = int(p["amount"])
            is_win = int(p["option_idx"]) == int(bet["winner"])
            net = 0
            if is_win:
                gross = amt * total_pot // win_stake
                profit = gross - amt
                fee = 0
                if profit > 0:
                    fee = (Decimal(profit) * config.WIN_FEE_PCT).to_integral_value(rounding=ROUND_CEILING)
                net = gross - int(fee)
            out.append(
                {
                    "tg_id": int(p["tg_id"]),
                    "win": is_win,
                    "option": options[int(p["option_idx"])],
                    "amount_micro": amt,
                    "net_micro": net,
                }
            )
        return out

    def volume_history(self, days: int = 14) -> list[dict]:
        """Daily processed volume (tips + deposits + bets) for the last N days."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT to_timestamp(created_at)::date AS day, "
                "COALESCE(SUM(CASE WHEN kind IN ('tip', 'deposit', 'bet', 'x402') THEN amount END), 0) AS v "
                "FROM tx_log WHERE created_at >= %s GROUP BY day ORDER BY day",
                (int(time.time()) - days * 86400,),
            ).fetchall()
        return [{"day": r["day"], "volume_micro": int(r["v"])} for r in rows]

    def global_stats(self) -> dict:
        with self._lock:
            users = self._conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            markets = self._conn.execute(
                "SELECT COUNT(*) AS c FROM bets WHERE status = 'open'"
            ).fetchone()["c"]
            tips = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE kind = 'tip'"
            ).fetchone()["s"]
            deposits = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE kind = 'deposit'"
            ).fetchone()["s"]
            bets_volume = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE kind = 'bet'"
            ).fetchone()["s"]
            fees = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE kind = 'fee'"
            ).fetchone()["s"]
            x402 = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE kind = 'x402'"
            ).fetchone()["s"]
            txs = self._conn.execute("SELECT COUNT(*) AS c FROM tx_log").fetchone()["c"]
            since30 = int(time.time()) - 30 * 86400
            vol30 = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log "
                "WHERE kind IN ('tip', 'deposit', 'bet', 'x402') AND created_at >= %s",
                (since30,),
            ).fetchone()["s"]
        return {
            "users": int(users),
            "open_markets": int(markets),
            "tips_micro": int(tips),
            "deposits_micro": int(deposits),
            "bets_micro": int(bets_volume),
            "x402_micro": int(x402),
            "fees_micro": int(fees),
            "volume_micro": int(tips + deposits + bets_volume + x402),
            "volume_30d_micro": int(vol30),
            "transactions": int(txs),
        }

    def leaderboard(self, limit: int = 10) -> list[dict]:
        rows = self.top_tippers(limit)
        return [
            {
                "username": self.username_of(r["tg_id"]) or f"id{r['tg_id']}",
                "total_micro": int(r["total"]),
            }
            for r in rows
        ]

    def user_view(self, tg_id: int) -> dict | None:
        self.ensure_user(tg_id, None)
        sent, received, won, lost = self.user_stats(tg_id)
        return {
            "id": tg_id,
            "username": self.username_of(tg_id),
            "balance_micro": int(self.balance(tg_id) * MICRO),
            "tips_sent_micro": sent,
            "tips_received_micro": received,
            "bets_won_micro": won,
            "bets_placed_micro": lost,
            "creator_fees_micro": self.creator_fees(tg_id),
        }

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

    def close(self) -> None:
        with self._lock:
            self._conn.close()


ledger = Ledger()
