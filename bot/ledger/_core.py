"""Ledger domain mixin: LedgerCoreMixin (split from bot/ledger.py)."""
import threading
import time

import psycopg
import psycopg.errors

from .. import config
from ._conn import ReconnectingConn
from ._schema import SCHEMA_DDL


class LedgerCoreMixin:
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
            ini = pathlib.Path(__file__).resolve().parent.parent.parent / "alembic.ini"
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
