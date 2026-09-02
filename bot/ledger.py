"""PostgreSQL ledger: internal USDC balances, wallet links, history.

Tips move instantly inside this ledger (no gas, no 12s wait).
Deposits credit here; withdrawals debit here and send USDC on-chain.
"""

import json
import logging
import secrets
import threading
import time
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

import psycopg
import psycopg.errors
from psycopg.rows import dict_row

from . import config

audit_log = logging.getLogger("tipbot.audit")

MICRO = 10**config.USDC_DECIMALS


def _extract_fee(note: str | None) -> int:
    """Parse fee from tx_log note field ('fee=12345' → 12345)."""
    if note and note.startswith("fee="):
        try:
            return int(note.split("=", 1)[1])
        except (ValueError, IndexError):
            pass
    return 0


# ---------- LMSR AMM math (prediction markets v2) ----------
#
# The Logarithmic Market Scoring Rule (Hanson 2003) prices outcome shares:
#   cost(q)   = b * ln(sum(exp(q_i / b)))
#   price_i   = exp(q_i / b) / sum(exp(q_j / b))
# Buying d shares of i costs cost(q + d*e_i) - cost(q).
#
# Funding theorem: with initial subsidy S = b*ln(n), the maker's worst-case
# loss is bounded — escrow after any trading path is always >= max_i(q_i),
# the payout if outcome i wins. So resolution can always pay winning shares
# at 1 USDC each and the creator keeps the leftover. Rounding is house-
# favorable on every trade (buy cost ceil, sell proceeds floor), so the
# escrow only grows relative to the ideal curve — conservation is exact.

_LMSR_PREC = 40


def _d(x) -> Decimal:
    return Decimal(x) if not isinstance(x, Decimal) else x


def lmsr_cost(q_micro: list[int], b_micro: int) -> Decimal:
    """LMSR cost function in micro-USDC for integer micro-share quantities."""
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        b = _d(b_micro)
        m = max(q_micro)  # shift by max for numerical stability of exp
        s = sum(((_d(q) - m) / b).exp() for q in q_micro)
        return b * ((s).ln() + _d(m) / b)


def lmsr_prices(q_micro: list[int], b_micro: int) -> list[Decimal]:
    """Current probability per option (0..1)."""
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        b = _d(b_micro)
        m = max(q_micro)
        exps = [((_d(q) - m) / b).exp() for q in q_micro]
        total = sum(exps)
        return [e / total for e in exps]


def lmsr_buy_shares(q_micro: list[int], b_micro: int, option_idx: int, spend_micro: int) -> int:
    """Max whole micro-shares of `option_idx` buyable for exactly `spend_micro`.

    Binary search on the monotone cost curve; result floored so the user never
    pays more than `spend_micro` (the difference stays in the escrow).
    """
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        base_cost = lmsr_cost(q_micro, b_micro)

        def cost_after(delta: int) -> Decimal:
            q2 = list(q_micro)
            q2[option_idx] += delta
            return lmsr_cost(q2, b_micro)

        lo, hi = 0, 1
        while cost_after(hi) - base_cost <= _d(spend_micro):
            lo = hi
            hi *= 2
            if hi > 10**18:
                break
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if cost_after(mid) - base_cost <= _d(spend_micro):
                lo = mid
            else:
                hi = mid - 1
        return lo


def lmsr_sell_value(q_micro: list[int], b_micro: int, option_idx: int, shares: int) -> int:
    """Micro-USDC received for selling `shares` back to the AMM (floored)."""
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        q2 = list(q_micro)
        q2[option_idx] -= shares
        val = lmsr_cost(q_micro, b_micro) - lmsr_cost(q2, b_micro)
        return int(val.to_integral_value(rounding=ROUND_FLOOR))


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
        # Bound every statement: a query that hangs (network stall, DB lock
        # contention) would otherwise pin self._lock forever and freeze every
        # other ledger call on the event loop. Error out instead of hanging.
        try:
            self._conn.execute("SET statement_timeout = '10s'")
        except Exception:
            pass

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


SCHEMA_DDL = """
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
                    notify_deposits BIGINT NOT NULL DEFAULT 1, -- DM on credited deposit
                    lang          TEXT NOT NULL DEFAULT 'ru'   -- UI language: ru/en/zh
                );
                CREATE TABLE IF NOT EXISTS user_wallets (
                    id         BIGSERIAL PRIMARY KEY,
                    tg_id      BIGINT NOT NULL,
                    address    TEXT NOT NULL UNIQUE,
                    key_enc    TEXT NOT NULL,
                    seed_enc   TEXT NOT NULL,
                    slot       INT NOT NULL DEFAULT 1,
                    active     BOOLEAN NOT NULL DEFAULT true,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    UNIQUE (tg_id, slot)
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
                CREATE INDEX IF NOT EXISTS idx_paywall_subscriptions_expires
                    ON paywall_subscriptions (expires_at);
                CREATE TABLE IF NOT EXISTS markets (
                    id            BIGSERIAL PRIMARY KEY,
                    creator       BIGINT NOT NULL,
                    question      TEXT NOT NULL,
                    options       TEXT NOT NULL,            -- JSON array
                    status        TEXT NOT NULL DEFAULT 'open', -- open | resolved | cancelled
                    winner        BIGINT,
                    close_at      BIGINT,
                    subsidy_micro BIGINT NOT NULL,          -- creator deposit (AMM funding)
                    b_micro       BIGINT NOT NULL,          -- LMSR liquidity param (micro-USDC)
                    escrow_micro  BIGINT NOT NULL DEFAULT 0,-- AMM cash held (subsidy + buys - sells)
                    deadline_notified BIGINT NOT NULL DEFAULT 0,
                    grace_warned  BIGINT NOT NULL DEFAULT 0,
                    created_at    BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS market_shares (
                    market_id  BIGINT NOT NULL,
                    tg_id      BIGINT NOT NULL,
                    option_idx BIGINT NOT NULL,
                    shares     BIGINT NOT NULL DEFAULT 0,     -- micro-shares (1e6 shares = 1 USDC payout)
                    cost_micro BIGINT NOT NULL DEFAULT 0,     -- net paid; negative = realized profit
                    PRIMARY KEY (market_id, tg_id, option_idx)
                );
                CREATE INDEX IF NOT EXISTS idx_market_shares_user ON market_shares (tg_id);
ALTER TABLE bets ADD COLUMN IF NOT EXISTS close_at BIGINT;
ALTER TABLE tx_log ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS lang TEXT NOT NULL DEFAULT 'ru';
ALTER TABLE bets ADD COLUMN IF NOT EXISTS deadline_notified BIGINT NOT NULL DEFAULT 0;
ALTER TABLE bets ADD COLUMN IF NOT EXISTS grace_warned BIGINT NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS suspicious_activity (
    id          BIGSERIAL PRIMARY KEY,
    tg_id       BIGINT NOT NULL,
    kind        TEXT NOT NULL,        -- large_withdraw | rapid_withdraw | unusual_deposit
    details     TEXT NOT NULL,         -- JSON: amount, threshold, count, etc.
    severity    TEXT NOT NULL DEFAULT 'info',  -- info | warn | critical
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE INDEX IF NOT EXISTS idx_suspicious_tg ON suspicious_activity (tg_id);
CREATE INDEX IF NOT EXISTS idx_suspicious_created ON suspicious_activity (created_at);
CREATE TABLE IF NOT EXISTS community_treasuries (
    id          BIGSERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL UNIQUE,
    owner_tg    BIGINT NOT NULL,
    balance     BIGINT NOT NULL DEFAULT 0,
    quorum_pct  INTEGER NOT NULL DEFAULT 50,
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE TABLE IF NOT EXISTS treasury_transactions (
    id          BIGSERIAL PRIMARY KEY,
    treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
    kind        TEXT NOT NULL,
    tg_id       BIGINT,
    amount      BIGINT NOT NULL,
    note        TEXT,
    tx_hash     TEXT,
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE TABLE IF NOT EXISTS treasury_proposals (
    id          BIGSERIAL PRIMARY KEY,
    treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
    proposer_tg BIGINT NOT NULL,
    amount      BIGINT NOT NULL,
    to_address  TEXT NOT NULL,
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'voting',
    votes_yes   INTEGER NOT NULL DEFAULT 0,
    votes_no    INTEGER NOT NULL DEFAULT 0,
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
    closes_at   BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS treasury_votes (
    id          BIGSERIAL PRIMARY KEY,
    treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
    proposal_id BIGINT NOT NULL,
    tg_id       BIGINT NOT NULL,
    vote        INTEGER NOT NULL,
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
    UNIQUE (proposal_id, tg_id)
);
CREATE TABLE IF NOT EXISTS gas_drips (
    day  BIGINT PRIMARY KEY,              -- UTC day (unix // 86400)
    count BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS market_subsidies (
    day  BIGINT PRIMARY KEY,              -- UTC day (unix // 86400)
    total_micro BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS onchain_markets (
    id         BIGINT PRIMARY KEY,               -- on-chain OutcomeMarket marketId
    creator    BIGINT NOT NULL,
    question   TEXT NOT NULL,
    options    TEXT NOT NULL,                    -- JSON array (labels live off-chain)
    close_at   BIGINT NOT NULL,
    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE INDEX IF NOT EXISTS idx_onchain_markets_close ON onchain_markets (close_at);
ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS deadline_notified BIGINT NOT NULL DEFAULT 0;
ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS resolved_outcome BIGINT;
ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS cancelled_flag BIGINT NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS onchain_trades (
    id         BIGSERIAL PRIMARY KEY,
    market_id  BIGINT NOT NULL,
    tg_id      BIGINT NOT NULL,
    outcome    BIGINT NOT NULL,
    shares     BIGINT NOT NULL,              -- micro-shares bought (at buy time)
    tx_hash    TEXT,
    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE INDEX IF NOT EXISTS idx_onchain_trades_market ON onchain_trades (market_id, outcome);
CREATE TABLE IF NOT EXISTS notification_outbox (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT NOT NULL,
    text       TEXT NOT NULL,
    retries    INT NOT NULL DEFAULT 0,
    next_retry_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE TABLE IF NOT EXISTS create2_proxies (
    tg_id        BIGINT PRIMARY KEY,
    proxy_address TEXT NOT NULL,
    deployed     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_create2_proxies_addr ON create2_proxies (LOWER(proxy_address));
"""

class Ledger:
    def __init__(self, database: str = config.DATABASE_URL) -> None:
        # One connection per Ledger instance, serialized by a per-process RLock.
        # In production the bot and the web dashboard run in SEPARATE processes
        # (see docker-compose.yml); this RLock does NOT coordinate across them.
        # All cross-process safety comes from atomic SQL (transactions, unique
        # constraints, row-level locking), not from this lock.
        self._lock = threading.RLock()
        self._conn = ReconnectingConn(database)
        self._run_alembic(database)
        self.ensure_schema()  # idempotent; retries past lock contention

    def _ensure(self) -> None:
        """Reconnect if the underlying connection is dead/broken."""
        self._conn._ensure()

    def ping(self) -> None:
        """Lightweight DB liveness check (SELECT 1). Raises on failure."""
        with self._lock:
            self._conn.execute("SELECT 1")

    @staticmethod
    def _run_alembic(database: str) -> None:
        """Run ``alembic upgrade head`` to apply tracked schema migrations.

        This is a best-effort non-blocking call: if alembic is not installed
        or the alembic.ini / versions/ directory is missing (e.g. during
        tests or clean installs), we silently fall back to ensure_schema()
        which applies the full DDL idempotently.
        """
        try:
            import pathlib

            from alembic.config import Config

            from alembic import command
            ini = pathlib.Path(__file__).resolve().parent.parent / "alembic.ini"
            if not ini.exists():
                return
            cfg = Config(str(ini))
            cfg.set_main_option("sqlalchemy.url", database)
            command.upgrade(cfg, "head")
        except Exception:
            pass  # ensure_schema() is the safety net

    def ensure_schema(self, retries: int = 8, delay: float = 2.0) -> None:
        """Apply idempotent schema DDL, retrying past transient lock timeouts.

        Running this at Ledger() construction used to crash the whole process
        when a concurrent bot/web/test process held a lock (ALTER TABLE needs
        ACCESS EXCLUSIVE). Now we back off and retry instead of dying.
        """
        last = None
        for _ in range(retries):
            try:
                with self._lock:
                    self._conn.rollback()
                    self._conn.execute("SET lock_timeout = '30s'")
                    self._conn.execute("SET statement_timeout = '60s'")
                    self._conn.execute(SCHEMA_DDL)
                    self._conn.commit()
                return
            except (psycopg.errors.LockNotAvailable, psycopg.OperationalError) as e:
                last = e
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                time.sleep(delay)
        raise RuntimeError(f"schema migration failed after {retries} attempts: {last}")

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

    def set_create2_proxy(self, tg_id: int, proxy_address: str) -> None:
        """Record a CREATE2 proxy address for a tg_id (upsert).

        Only touches create2_proxies — the deposit scanner resolves the owner
        via tg_id_of_proxy. Never writes wallet_links so a user's own /link stays
        untouched (wallet_links.address is UNIQUE and owned by the /link flow).
        """
        with self._lock:
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "INSERT INTO create2_proxies (tg_id, proxy_address) "
                "VALUES (%s, %s) "
                "ON CONFLICT (tg_id) DO UPDATE SET proxy_address = EXCLUDED.proxy_address",
                (tg_id, proxy_address.lower()),
            )
            self._conn.commit()

    def tg_id_of_proxy(self, proxy_address: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tg_id FROM create2_proxies WHERE LOWER(proxy_address) = LOWER(%s)",
                (proxy_address,),
            ).fetchone()
        return row["tg_id"] if row else None

    def create2_proxy_of(self, tg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT proxy_address FROM create2_proxies WHERE tg_id = %s",
                (tg_id,),
            ).fetchone()
        return row["proxy_address"] if row else None

    def list_create2_proxies(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tg_id, proxy_address, deployed FROM create2_proxies ORDER BY tg_id"
            ).fetchall()
        return [dict(r) for r in rows]

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
            audit_log.info(json.dumps({"event": "credit", "tg_id": tg_id, "amount_micro": amount_micro, "kind": kind, "counterparty": counterparty, "tx_hash": tx_hash}))

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

    def reserve_x402_auth(self, nonce: str, tg_id: int, amount_micro: int, sender: str) -> bool:
        """Reserve an EIP-3009 authorization nonce (scheme "exact"): the row
        key is `auth:<nonce>` in x402_payments. True = reserved (this caller
        may settle); False = already used (replay). The balance credit only
        lands in finalize_x402_auth, after the on-chain settlement succeeds."""
        if not nonce:
            return False
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO x402_payments (tx_hash, recipient_tg, amount_micro, sender) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING",
                (nonce, tg_id, amount_micro, sender),
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
                "SELECT tx_hash, recipient_tg, amount_micro, sender, created_at "
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

    def finalize_x402_credit(
        self, nonce: str, settlement_tx: str, recipient_tg: int,
        amount_micro: int, sender: str,
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
                "amount_micro = %s, sender = %s WHERE tx_hash = %s",
                (settlement_tx, recipient_tg, amount_micro, sender, nonce),
            )
            if cur.rowcount == 0:
                return False
            self.ensure_user(recipient_tg, None)
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
        amount_micro: int, sender: str,
    ) -> bool:
        """Atomically convert a reserved authorization into a settled paywall
        purchase: swap the row key to the settlement tx, record the purchase,
        credit the item owner. False = reservation gone (replay/race)."""
        if not nonce or not settlement_tx:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE x402_payments SET tx_hash = %s, recipient_tg = %s, "
                "amount_micro = %s, sender = %s WHERE tx_hash = %s",
                (settlement_tx, owner_tg, amount_micro, sender, nonce),
            )
            if cur.rowcount == 0:
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

    def finalize_x402_auth(self, nonce: str, settlement_tx: str) -> bool:
        """Swap the reserved auth-nonce key for the real settlement tx hash
        once the on-chain transferWithAuthorization has confirmed."""
        if not nonce or not settlement_tx:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE x402_payments SET tx_hash = %s WHERE tx_hash = %s",
                (settlement_tx, nonce),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def x402_paid(self, tx_hash: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM x402_payments WHERE tx_hash = %s", (tx_hash,)
            ).fetchone()
            return row is not None

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
        if amount_micro <= 0:
            # A negative amount would invert the direction (the "sender"
            # would GAIN money) and mint it from thin air.
            return False
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
            audit_log.info(json.dumps({"event": "transfer", "from": from_id, "to": to_id, "amount_micro": amount_micro}))
            return True

    def debit(self, tg_id: int, amount_micro: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (amount_micro, tg_id, amount_micro),
            )
            if cur.rowcount > 0:
                audit_log.info(json.dumps({"event": "debit", "tg_id": tg_id, "amount_micro": amount_micro, "note": ""}))
            return cur.rowcount > 0

    def reserve_withdraw(
        self, tg_id: int, to_address: str, amount_micro: int, fee_micro: int
    ) -> int | None:
        """Atomically debit amount+fee and enqueue a withdrawal.

        Returns the tx_log id, or None if the user lacks the balance. The row is
        written BEFORE any on-chain send and starts as **queued**: it sits in the
        batch-payout queue until the batch watcher flushes it (via
        TipBotVault.batchDistribute or a direct transfer). Crash between debit and
        send is safe — the queued row survives and is flushed/refunded later.
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
                "VALUES ('withdraw', %s, %s, %s, %s, 'queued') RETURNING id",
                (tg_id, to_address, amount_micro, f"fee={fee_micro}"),
            )
            wd_id = int(cur.fetchone()["id"])
            if fee_micro > 0:
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note, status) "
                    "VALUES ('fee', %s, %s, %s, %s, 'done')",
                    (tg_id, to_address, fee_micro, f"withdraw_id={wd_id}"),
                )
            self._conn.commit()
            return wd_id

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
        in micro-units. Four categories:

          1. user balances (users.balance)
          2. AMM market escrows of open markets (markets.escrow_micro)
          3. parimutuel bet pools of open bets (sum of bet_positions)
          4. community treasury balances (community_treasuries.balance)

        Only counting user balances would understate real obligations: escrowed
        market funds, open bet pools and treasury deposits are all money the bot
        still owes even though they are not currently on a user's balance.
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
                "WHERE LOWER(sender) = LOWER(%s) AND claimed = 0 FOR UPDATE",
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
                "AND COALESCE(status, 'done') IN ('queued', 'done') AND created_at >= %s",
                (tg_id, since),
            ).fetchone()
        return int(row["c"])

    # ---- AML / suspicious-activity monitoring ----

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
                "UPDATE tx_log SET tx_hash = %s, status = 'done' WHERE id = %s",
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
            self._conn.execute(
                "UPDATE tx_log SET status = 'refunded' "
                "WHERE kind = 'fee' AND note = %s", (f"withdraw_id={wd_id}",)
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
            cur = self._conn.execute(
                "SELECT * FROM bets WHERE id = %s", (bet_id,)
            )
            row = cur.fetchone()
            # Pure read: drop the open transaction so the shared connection
            # never pins table locks for a later DDL (CREATE/ALTER), matching
            # the rollback-after-request convention documented on rollback().
            self._conn.rollback()
            return row

    def get_bet_for_update(self, bet_id: int) -> dict | None:
        """SELECT FOR UPDATE — exclusive lock on the bets row until the
        transaction commits or rolls back. The web service and the bot are
        separate processes over one database, so every mutating bet
        operation must serialize here (same pattern as markets)."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM bets WHERE id = %s FOR UPDATE",
                (bet_id,),
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
        if amount_micro <= 0:
            # debit(-X) would INCREASE the balance and record a negative stake
            # that vanishes from the pot at resolution (money creation). Raise
            # instead of returning a status: callers treat unknown statuses as
            # success in some handlers.
            raise ValueError("bet amount must be positive")
        with self._lock:
            bet = self.get_bet_for_update(bet_id)
            if not bet or bet["status"] != "open":
                self._conn.rollback()
                return "closed"
            if bet["close_at"] is not None and int(time.time()) > bet["close_at"]:
                self._conn.rollback()
                return "deadline"
            options = json.loads(bet["options"])
            if option_idx < 0 or option_idx >= len(options):
                self._conn.rollback()
                return "badopt"
            if not self.debit(tg_id, amount_micro):
                self._conn.rollback()
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
            bet = self.get_bet_for_update(bet_id)
            if not bet or bet["status"] != "open":
                self._conn.rollback()
                return False, "Ставка не найдена или уже закрыта."
            if bet["creator"] != resolver_id:
                self._conn.rollback()
                return False, "Закрыть может только создатель ставки."
            options = json.loads(bet["options"])
            if winning_idx < 0 or winning_idx >= len(options):
                self._conn.rollback()
                return False, "Неверный номер варианта."

            positions = self._bet_positions(bet_id)
            total_pot = sum(int(p["amount"]) for p in positions)
            if total_pot <= 0:
                self._conn.rollback()
                return False, "В ставке пока нет денег — закрыть нечего."

            winners = [p for p in positions if int(p["option_idx"]) == winning_idx]
            if not winners:
                self._conn.rollback()
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

            # Atomic: all balance updates + the status flip commit together.
            # A crash before this single commit rolls everything back (no
            # partial payout), and after it the status guard blocks re-entry.
            for tg_id, net in payouts:
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (net, tg_id),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('bet_win', %s, %s, %s, %s)",
                    (tg_id, str(bet_id), net, bet["question"]),
                )
            if creator_income > 0:
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (creator_income, bet["creator"]),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('fee', %s, %s, %s, %s)",
                    (bet["creator"], str(bet_id), creator_income, "market fees"),
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
            bet = self.get_bet_for_update(bet_id)
            if not bet or bet["status"] != "open":
                self._conn.rollback()
                return False, "Ставка не найдена или уже закрыта."
            if bet["creator"] != resolver_id and not self.is_expired(bet):
                self._conn.rollback()
                return False, "Отменить может только создатель ставки (или после дедлайна + grace)."
            refunded_by_creator = bet["creator"] == resolver_id
            # Atomic: refunds + status flip commit together so a crash can't
            # leave backers credited but the bet still 'open' (double refund).
            for p in self._bet_positions(bet_id):
                tg_id = int(p["tg_id"])
                amt = int(p["amount"])
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (amt, tg_id),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('bet_cancel', %s, %s, %s, %s)",
                    (tg_id, str(bet_id), amt, bet["question"]),
                )
            self._conn.execute(
                "UPDATE bets SET status = 'cancelled' WHERE id = %s", (bet_id,)
            )
            self._conn.commit()
            if refunded_by_creator:
                return True, "Ставка отменена, деньги возвращены."
            return True, "Рынок истёк — деньги возвращены всем участникам."

    # ---------- prediction markets v2 (LMSR AMM) ----------

    def create_market(
        self,
        creator_tg_id: int,
        question: str,
        options: list[str],
        subsidy_micro: int,
        close_at: int | None = None,
    ) -> int | str:
        """Creator funds the AMM with `subsidy_micro`; b = subsidy / ln(n).

        Returns the market id, or 'balance' if the creator can't fund it.
        """
        n = len(options)
        with localcontext() as ctx:
            ctx.prec = _LMSR_PREC
            b = int((_d(subsidy_micro) / _d(n).ln()).to_integral_value(rounding=ROUND_FLOOR))
        if b <= 0:
            return "subsidy"
        with self._lock:
            self.ensure_user(creator_tg_id, None)
            if not self.debit(creator_tg_id, subsidy_micro):
                return "balance"
            cur = self._conn.execute(
                "INSERT INTO markets (creator, question, options, close_at, subsidy_micro, b_micro, escrow_micro) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (creator_tg_id, question, json.dumps(options), close_at,
                 subsidy_micro, b, subsidy_micro),
            )
            market_id = int(cur.fetchone()["id"])
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('market_create', %s, %s, %s, %s)",
                (creator_tg_id, str(market_id), subsidy_micro, question),
            )
            self._conn.commit()
            return market_id

    def get_market(self, market_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM markets WHERE id = %s", (market_id,)
            ).fetchone()

    def get_market_for_update(self, market_id: int) -> dict | None:
        """SELECT FOR UPDATE — holds an exclusive row lock until the
        transaction commits or rolls back.  Use this inside every
        mutating market operation (buy, sell, resolve, cancel) to prevent
        two concurrent processes from racing on the same escrow."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM markets WHERE id = %s FOR UPDATE",
                (market_id,),
            ).fetchone()

    def open_markets(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM markets WHERE status = 'open' ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()

    def open_markets_past_deadline(self) -> list[dict]:
        """Open AMM markets whose deadline passed and whose creator wasn't yet
        asked to resolve (same protection as parimutuel bets)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, creator, question FROM markets "
                "WHERE status = 'open' AND close_at IS NOT NULL "
                "AND close_at <= %s AND deadline_notified = 0",
                (int(time.time()),),
            ).fetchall()

    def mark_market_deadline_notified(self, market_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE markets SET deadline_notified = 1 WHERE id = %s", (market_id,)
            )
            self._conn.commit()

    def markets_need_grace_warning(self, warn_before: int) -> list[dict]:
        grace = config.MARKET_GRACE_HOURS * 3600
        with self._lock:
            return self._conn.execute(
                "SELECT id, creator, question, close_at FROM markets "
                "WHERE status = 'open' AND close_at IS NOT NULL "
                "AND deadline_notified = 1 AND grace_warned = 0 "
                "AND %s >= close_at + %s - %s",
                (int(time.time()), grace, warn_before),
            ).fetchall()

    def mark_market_grace_warned(self, market_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE markets SET grace_warned = 1 WHERE id = %s", (market_id,)
            )
            self._conn.commit()

    def market_is_expired(self, market: dict) -> bool:
        if market["close_at"] is None:
            return False
        grace = config.MARKET_GRACE_HOURS * 3600
        return int(time.time()) > market["close_at"] + grace

    def market_quantities(self, market_id: int) -> list[int]:
        """Total outstanding micro-shares per option."""
        m = self.get_market(market_id)
        if not m:
            return []
        n = len(json.loads(m["options"]))
        with self._lock:
            rows = self._conn.execute(
                "SELECT option_idx, SUM(shares) AS s FROM market_shares "
                "WHERE market_id = %s GROUP BY option_idx",
                (market_id,),
            ).fetchall()
        totals = {int(r["option_idx"]): int(r["s"]) for r in rows}
        return [totals.get(i, 0) for i in range(n)]

    def market_prices(self, market_id: int) -> list[Decimal] | None:
        """Live probability per option (0..1), or None if the market is gone."""
        m = self.get_market(market_id)
        if not m:
            return None
        return lmsr_prices(self.market_quantities(market_id), int(m["b_micro"]))

    def amm_market_view(self, market_id: int) -> dict | None:
        """Dashboard-friendly snapshot of one LMSR market."""
        m = self.get_market(market_id)
        if not m:
            return None
        q = self.market_quantities(market_id)
        prices = lmsr_prices(q, int(m["b_micro"]))
        options = json.loads(m["options"])
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(DISTINCT tg_id) AS n FROM market_shares "
                "WHERE market_id = %s AND shares > 0",
                (market_id,),
            ).fetchone()
        return {
            "id": int(m["id"]),
            "question": m["question"],
            "status": m["status"],
            "winner": m["winner"],
            "close_at": m["close_at"],
            "creator": int(m["creator"]),
            "liquidity_micro": int(m["escrow_micro"]),
            "subsidy_micro": int(m["subsidy_micro"]),
            "traders": int(row["n"]) if row else 0,
            "volume_micro": sum(q),  # outstanding shares ≈ USDC that flowed in
            "options": [
                {
                    "index": i,
                    "label": o,
                    "price_pct": float(round(prices[i] * 100, 2)),
                    "shares": q[i],
                }
                for i, o in enumerate(options)
            ],
        }

    def open_amm_markets(self, limit: int = 20) -> list[dict]:
        return self.open_markets(limit)

    def _market_share_rows(self, market_id: int) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT tg_id, option_idx, shares, cost_micro FROM market_shares "
                "WHERE market_id = %s AND (shares > 0 OR cost_micro <> 0)",
                (market_id,),
            ).fetchall()

    def user_market_position(self, market_id: int, tg_id: int) -> dict[int, dict]:
        """option_idx -> {'shares': micro, 'cost': net paid micro} for one user."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT option_idx, shares, cost_micro FROM market_shares "
                "WHERE market_id = %s AND tg_id = %s AND (shares > 0 OR cost_micro <> 0)",
                (market_id, tg_id),
            ).fetchall()
        return {
            int(r["option_idx"]): {"shares": int(r["shares"]), "cost": int(r["cost_micro"])}
            for r in rows
        }

    def user_market_positions(self, tg_id: int) -> list[dict]:
        """All open AMM positions of a user, enriched with live prices."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ms.market_id, ms.option_idx, ms.shares, ms.cost_micro, "
                "m.question, m.options, m.status, m.b_micro "
                "FROM market_shares ms JOIN markets m ON m.id = ms.market_id "
                "WHERE ms.tg_id = %s AND ms.shares > 0 AND m.status = 'open' "
                "ORDER BY ms.market_id DESC",
                (tg_id,),
            ).fetchall()
        out = []
        for r in rows:
            options = json.loads(r["options"])
            prices = lmsr_prices(self.market_quantities(int(r["market_id"])), int(r["b_micro"]))
            out.append(
                {
                    "market_id": int(r["market_id"]),
                    "question": r["question"],
                    "option": options[int(r["option_idx"])],
                    "shares": int(r["shares"]),
                    "cost": int(r["cost_micro"]),
                    "price": prices[int(r["option_idx"])],
                    "value": int(r["shares"]) * prices[int(r["option_idx"])],
                }
            )
        return out

    def buy_shares(
        self, market_id: int, tg_id: int, option_idx: int, spend_micro: int
    ) -> tuple[str, dict]:
        """Spend up to `spend_micro` USDC on outcome shares at the live price.

        Returns ('ok', info) or ('closed'|'deadline'|'badopt'|'balance'|'toosmall', {}).
        The share count is floored against the exact LMSR cost curve, so the
        user never overpays; the sub-micro remainder stays in the escrow.
        Trades below MARKET_MIN_TRADE_MICRO are rejected before any debit
        (dust orders would move prices by nothing but spam the tx log).
        """
        if spend_micro < 10_000:  # 0.01 USDC
            return "toosmall", {}
        with self._lock:
            self._ensure()
            m = self.get_market_for_update(market_id)
            if not m or m["status"] != "open":
                self._conn.rollback()
                return "closed", {}
            if m["close_at"] is not None and int(time.time()) > m["close_at"]:
                self._conn.rollback()
                return "deadline", {}
            options = json.loads(m["options"])
            if option_idx < 0 or option_idx >= len(options):
                self._conn.rollback()
                return "badopt", {}
            if not self.debit(tg_id, spend_micro):
                self._conn.rollback()
                return "balance", {}
            q = self.market_quantities(market_id)
            shares = lmsr_buy_shares(q, int(m["b_micro"]), option_idx, spend_micro)
            if shares <= 0:
                # Nothing has been committed yet, so a plain rollback undoes
                # the debit — no need for an explicit refund credit.
                self._conn.rollback()
                return "toosmall", {}
            prices = lmsr_prices([*q[:option_idx], q[option_idx] + shares, *q[option_idx + 1:]],
                                 int(m["b_micro"]))
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "INSERT INTO market_shares (market_id, tg_id, option_idx, shares, cost_micro) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (market_id, tg_id, option_idx) DO UPDATE "
                "SET shares = market_shares.shares + EXCLUDED.shares, "
                "cost_micro = market_shares.cost_micro + EXCLUDED.cost_micro",
                (market_id, tg_id, option_idx, shares, spend_micro),
            )
            self._conn.execute(
                "UPDATE markets SET escrow_micro = escrow_micro + %s WHERE id = %s",
                (spend_micro, market_id),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('market_buy', %s, %s, %s, %s)",
                (tg_id, str(market_id), spend_micro, options[option_idx]),
            )
            self._conn.commit()
            return "ok", {
                "shares": shares,
                "cost": spend_micro,
                "price": prices[option_idx],
                "label": options[option_idx],
            }

    def sell_shares(
        self, market_id: int, tg_id: int, option_idx: int, shares_micro: int
    ) -> tuple[str, dict]:
        """Sell whole micro-shares back to the AMM at the live price (floored).

        Returns ('ok', info) or ('closed'|'deadline'|'badopt'|'noshare'|'toosmall', {}).
        """
        with self._lock:
            self._ensure()
            m = self.get_market_for_update(market_id)
            if not m or m["status"] != "open":
                self._conn.rollback()
                return "closed", {}
            if m["close_at"] is not None and int(time.time()) > m["close_at"]:
                self._conn.rollback()
                return "deadline", {}
            options = json.loads(m["options"])
            if option_idx < 0 or option_idx >= len(options):
                self._conn.rollback()
                return "badopt", {}
            pos = self.user_market_position(market_id, tg_id)
            held = pos.get(option_idx, {}).get("shares", 0)
            if held <= 0:
                self._conn.rollback()
                return "noshare", {}
            shares = min(shares_micro, held)
            if shares <= 0:
                self._conn.rollback()
                return "toosmall", {}
            q = self.market_quantities(market_id)
            value = lmsr_sell_value(q, int(m["b_micro"]), option_idx, shares)
            if value <= 0:
                self._conn.rollback()
                return "toosmall", {}
            new_cost = pos[option_idx]["cost"] - value  # realized profit lowers basis
            self._conn.execute(
                "UPDATE market_shares SET shares = shares - %s, cost_micro = %s "
                "WHERE market_id = %s AND tg_id = %s AND option_idx = %s",
                (shares, new_cost, market_id, tg_id, option_idx),
            )
            self._conn.execute(
                "UPDATE markets SET escrow_micro = escrow_micro - %s WHERE id = %s",
                (value, market_id),
            )
            # Direct credit (no intermediate commit) so the whole trade —
            # shares, escrow and payout — lands in one atomic transaction.
            self._conn.execute(
                "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                (value, tg_id),
            )
            self._conn.execute(
                "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                "VALUES ('market_sell', %s, %s, %s, %s)",
                (tg_id, str(market_id), value, options[option_idx]),
            )
            prices = lmsr_prices(q, int(m["b_micro"]))
            self._conn.commit()
            return "ok", {
                "shares": shares,
                "value": value,
                "price": prices[option_idx],
                "label": options[option_idx],
            }

    def resolve_market(
        self, market_id: int, winning_idx: int, resolver_id: int
    ) -> tuple[bool, str, list[dict]]:
        """Pay every winning share 1 USDC from the escrow; creator keeps the rest.

        The LMSR funding theorem guarantees escrow >= winning shares, so this
        never goes insolvent. Returns (ok, message, payouts) where payouts is
        [{'tg_id', 'net_micro', 'win'}] for DM notifications.
        """
        with self._lock:
            self._ensure()
            m = self.get_market_for_update(market_id)
            if not m or m["status"] != "open":
                self._conn.rollback()
                return False, "Рынок не найден или уже закрыт.", []
            if m["creator"] != resolver_id:
                self._conn.rollback()
                return False, "Закрыть может только создатель рынка.", []
            options = json.loads(m["options"])
            if winning_idx < 0 or winning_idx >= len(options):
                self._conn.rollback()
                return False, "Неверный номер варианта.", []

            # Anti-manipulation: the autonomous agent must never resolve its own
            # markets. Enforced here (persists across restarts) in addition to
            # the in-memory guard in agent/tools.py.
            if resolver_id == config.AGENT_TG_ID and int(m["creator"]) == resolver_id:
                self._conn.rollback()
                return False, "Агент не может резолвить собственные рынки.", []

            # Anti-manipulation: a creator holding shares of the outcome they
            # are about to declare could mint themselves a payout. Force them
            # to exit the position first (sell works while the market is open
            # and before the deadline), so resolution stays conflict-free.
            held = self._conn.execute(
                "SELECT SUM(shares) AS s FROM market_shares "
                "WHERE market_id = %s AND tg_id = %s AND option_idx = %s AND shares > 0",
                (market_id, resolver_id, winning_idx),
            ).fetchone()
            if held and int(held["s"] or 0) > 0:
                self._conn.rollback()
                label = options[winning_idx]
                return False, (
                    f"У вас есть доли варианта «{label}» — сначала продайте их: "
                    "резолвить рынок со ставкой на исход запрещено."
                ), []

            winner_rows = self._conn.execute(
                "SELECT tg_id, SUM(shares) AS s FROM market_shares "
                "WHERE market_id = %s AND option_idx = %s AND shares > 0 "
                "GROUP BY tg_id",
                (market_id, winning_idx),
            ).fetchall()

            escrow = int(m["escrow_micro"])
            # 1 micro-share pays 1 micro-USDC (1e6 shares = 1 USDC payout)
            payout_total = sum(int(w["s"]) for w in winner_rows)
            if payout_total > escrow:  # cannot happen per funding theorem; belt & braces
                payout_total = escrow

            payouts: list[dict] = []
            distributed = 0
            for w in winner_rows:
                gross = int(w["s"])
                if distributed + gross > payout_total:
                    gross = payout_total - distributed
                if gross <= 0:
                    continue
                distributed += gross
                tg = int(w["tg_id"])
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (gross, tg),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('market_win', %s, %s, %s, %s)",
                    (tg, str(market_id), gross, m["question"]),
                )
                payouts.append({"tg_id": tg, "net_micro": gross, "win": True})

            leftover = escrow - distributed
            if leftover > 0:
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (leftover, int(m["creator"])),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('fee', %s, %s, %s, %s)",
                    (int(m["creator"]), str(market_id), leftover, "market fees"),
                )
            # holders of losing outcomes get nothing (their cost was spent into
            # the escrow when they bought).
            for r in self._market_share_rows(market_id):
                if int(r["option_idx"]) != winning_idx and int(r["shares"]) > 0:
                    payouts.append({"tg_id": int(r["tg_id"]), "net_micro": 0, "win": False})

            # Atomic: all balance updates + the status flip commit together, so a
            # crash before this point rolls everything back (no partial payout),
            # and after it the status guard blocks re-entry (no double payout).
            self._conn.execute(
                "UPDATE markets SET status = 'resolved', winner = %s, escrow_micro = 0 "
                "WHERE id = %s",
                (winning_idx, market_id),
            )
            self._conn.commit()
            return True, f"Победил вариант {winning_idx + 1} — {options[winning_idx]}", payouts

    def cancel_market(self, market_id: int, resolver_id: int) -> tuple[bool, str]:
        """Refund net cost basis to holders; creator gets the escrow leftover.

        Anyone may cancel once the deadline + grace passed (dead-market
        protection, same as parimutuel bets).
        """
        with self._lock:
            self._ensure()
            m = self.get_market_for_update(market_id)
            if not m or m["status"] != "open":
                self._conn.rollback()
                return False, "Рынок не найден или уже закрыт."
            if m["creator"] != resolver_id and not self.market_is_expired(m):
                self._conn.rollback()
                return False, "Отменить может только создатель рынка (или после дедлайна + grace)."
            if resolver_id == config.AGENT_TG_ID and int(m["creator"]) == resolver_id:
                self._conn.rollback()
                return False, "Агент не может отменять собственные рынки."
            escrow = int(m["escrow_micro"])
            rows = sorted(
                self._market_share_rows(market_id),
                key=lambda r: int(r["cost_micro"]),
                reverse=True,
            )
            available = escrow
            for r in rows:
                refund = min(max(int(r["cost_micro"]), 0), available)
                if refund <= 0:
                    continue
                available -= refund
                tg = int(r["tg_id"])
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (refund, tg),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('market_cancel', %s, %s, %s, %s)",
                    (tg, str(market_id), refund, m["question"]),
                )
            if available > 0:
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (available, int(m["creator"])),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('fee', %s, %s, %s, %s)",
                    (int(m["creator"]), str(market_id), available, "market subsidy back"),
                )
            # Atomic: refunds + status flip commit together so a crash can't
            # leave backers credited but the market still 'open' (double refund).
            self._conn.execute(
                "UPDATE markets SET status = 'cancelled', escrow_micro = 0 WHERE id = %s",
                (market_id,),
            )
            self._conn.commit()
            return True, "Рынок отменён — ставки возвращены по цене входа."

    # ---------- on-chain markets (OutcomeMarket.sol registry) ----------
    #
    # The contract stores numbers only (labels on-chain would cost gas), so
    # the bot keeps the human-readable metadata here and the money/shares on
    # Base. ERC1155 balances are read from the chain, never from this table.

    def save_onchain_market(
        self, market_id: int, creator: int, question: str, options: list[str], close_at: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO onchain_markets (id, creator, question, options, close_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (market_id, creator, question, json.dumps(options), close_at),
            )
            self._conn.commit()

    def get_onchain_market(self, market_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM onchain_markets WHERE id = %s", (market_id,)
            ).fetchone()

    def list_onchain_markets(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM onchain_markets ORDER BY id DESC LIMIT %s", (limit,)
            ).fetchall()

    def onchain_markets_past_deadline(self) -> list[dict]:
        """Registered on-chain markets past close the creator wasn't asked to
        resolve yet (the watcher DMs them outcome-pick buttons)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, creator, question, options FROM onchain_markets "
                "WHERE resolved_outcome IS NULL AND cancelled_flag = 0 "
                "AND deadline_notified = 0 AND close_at <= %s",
                (int(time.time()),),
            ).fetchall()

    def mark_onchain_deadline_notified(self, market_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE onchain_markets SET deadline_notified = 1 WHERE id = %s",
                (market_id,),
            )
            self._conn.commit()

    def set_onchain_resolved(self, market_id: int, winner_idx: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE onchain_markets SET resolved_outcome = %s WHERE id = %s",
                (winner_idx, market_id),
            )
            self._conn.commit()

    def mark_onchain_cancelled(self, market_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE onchain_markets SET cancelled_flag = 1 WHERE id = %s",
                (market_id,),
            )
            self._conn.commit()

    def onchain_markets_overdue(self, grace_seconds: int) -> list[dict]:
        """Unresolved on-chain markets whose cancel window (24h on-chain) plus
        `grace_seconds` has passed — the watcher cancels them so holders can
        pull refunds and the creator subsidy is not stuck forever."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, question FROM onchain_markets "
                "WHERE resolved_outcome IS NULL AND cancelled_flag = 0 "
                "AND close_at + %s <= %s",
                (grace_seconds, int(time.time())),
            ).fetchall()

    def record_onchain_trade(
        self, market_id: int, tg_id: int, outcome: int, shares: int, tx_hash: str = ""
    ) -> None:
        """Log a successful on-chain buy (shares > 0). Registry-only: real
        holdings always live in ERC-1155, this just powers winner DMs."""
        with self._lock:
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "INSERT INTO onchain_trades (market_id, tg_id, outcome, shares, tx_hash) "
                "VALUES (%s, %s, %s, %s, %s)",
                (market_id, tg_id, outcome, shares, tx_hash),
            )
            self._conn.commit()

    def onchain_trades_for_outcome(self, market_id: int, outcome: int) -> list[dict]:
        """Per-user shares bought of one outcome (at buy time; holders may
        have sold since — redemption always reads the real ERC-1155 balance)."""
        with self._lock:
            return self._conn.execute(
                "SELECT tg_id, SUM(shares) AS shares FROM onchain_trades "
                "WHERE market_id = %s AND outcome = %s "
                "GROUP BY tg_id HAVING SUM(shares) > 0",
                (market_id, outcome),
            ).fetchall()

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

    # ---------- multi-wallet (slot-based) ----------

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
            from . import wallets as _wallets
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

    def bulk_market_views(self, bet_ids: list[int]) -> list[dict]:
        """Batch market_view for multiple bet IDs — fixes N+1 query pattern."""
        if not bet_ids:
            return []
        placeholders = ",".join(["%s"] * len(bet_ids))
        with self._lock:
            bets = self._conn.execute(
                f"SELECT * FROM bets WHERE id IN ({placeholders})", bet_ids
            ).fetchall()
            totals_rows = self._conn.execute(
                f"SELECT bet_id, option_idx, SUM(amount_micro) AS total "
                f"FROM bet_positions WHERE bet_id IN ({placeholders}) "
                f"GROUP BY bet_id, option_idx", bet_ids
            ).fetchall()
            backers_rows = self._conn.execute(
                f"SELECT bet_id, option_idx, COUNT(DISTINCT tg_id) AS c "
                f"FROM bet_positions WHERE bet_id IN ({placeholders}) "
                f"GROUP BY bet_id, option_idx", bet_ids
            ).fetchall()
        totals_map: dict[int, dict[int, int]] = {}
        for r in totals_rows:
            bid = int(r["bet_id"])
            totals_map.setdefault(bid, {})[int(r["option_idx"])] = int(r["total"])
        backers_map: dict[int, dict[int, int]] = {}
        for r in backers_rows:
            bid = int(r["bet_id"])
            backers_map.setdefault(bid, {})[int(r["option_idx"])] = int(r["c"])
        out = []
        for bet in bets:
            bid = int(bet["id"])
            options = json.loads(bet["options"])
            totals = totals_map.get(bid, {})
            pot = sum(totals.values())
            backers = backers_map.get(bid, {})
            items = []
            for i, opt in enumerate(options):
                pool = totals.get(i, 0)
                prob = round(pool / pot * 100, 1) if pot else 0.0
                items.append({"index": i, "label": opt, "pool": pool, "probability": prob, "backers": backers.get(i, 0)})
            creator_name = self.username_of(bet["creator"])
            out.append({
                "id": bid, "question": bet["question"], "status": bet["status"],
                "winner": bet["winner"], "close_at": bet["close_at"],
                "creator": {"id": bet["creator"], "username": creator_name},
                "options": items, "pot": pot,
                "total_backers": sum(backers.values()),
            })
        return out

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

    def rollback(self) -> None:
        """Drop any open read transaction so the shared connection never pins
        table locks (web request middleware calls this after every request)."""
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- notification outbox ----------

    def enqueue_notification(self, chat_id: int, text: str) -> int:
        """Queue a Telegram notification for retry-safe delivery."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notification_outbox (chat_id, text) VALUES (%s, %s) RETURNING id",
                (chat_id, text),
            )
            self._conn.commit()
            return int(cur.fetchone()["id"])

    def dequeue_notifications(self, limit: int = 10) -> list[dict]:
        """Fetch pending notifications due for delivery."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, chat_id, text FROM notification_outbox "
                "WHERE next_retry_at <= EXTRACT(EPOCH FROM now())::bigint "
                "ORDER BY id LIMIT %s", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def ack_notification(self, notif_id: int) -> None:
        """Mark a notification as delivered (delete it)."""
        with self._lock:
            self._conn.execute("DELETE FROM notification_outbox WHERE id = %s", (notif_id,))
            self._conn.commit()

    def retry_notification(self, notif_id: int, backoff: int) -> None:
        """Schedule a failed notification for retry with exponential backoff (max 3600s)."""
        import time as _time
        delay = min(backoff * 2, 3600)
        with self._lock:
            self._conn.execute(
                "UPDATE notification_outbox SET retries = retries + 1, "
                "next_retry_at = %s WHERE id = %s",
                (int(_time.time()) + delay, notif_id),
            )
            self._conn.commit()


ledger = Ledger()


import asyncio


class AsyncLedger:
    """Async proxy over the synchronous :class:`Ledger`.

    Every method call is dispatched to a worker thread via
    ``asyncio.to_thread`` so the aiogram event loop is never blocked by a
    synchronous ``psycopg`` query. Handlers ``await`` these calls exactly as
    if they were native coroutines.

    Non-callable attributes (e.g. ``_conn`` used by tests) pass through
    unchanged.
    """

    def __init__(self, real: "Ledger") -> None:
        # Use object.__setattr__ to avoid any __getattr__ recursion.
        object.__setattr__(self, "_real", real)

    def __getattr__(self, name: str):
        attr = getattr(object.__getattribute__(self, "_real"), name)
        if callable(attr):

            def _wrapper(*args, **kwargs):
                return asyncio.to_thread(attr, *args, **kwargs)

            return _wrapper
        return attr


# The instance handlers import. Keeps the synchronous `ledger` singleton for
# tests/non-async contexts while handlers run every query off the event loop.
async_ledger = AsyncLedger(ledger)
