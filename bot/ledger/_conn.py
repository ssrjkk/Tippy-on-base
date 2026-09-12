import psycopg
from psycopg.rows import dict_row


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
        # Capture whether we were mid-transaction BEFORE _ensure() may replace a
        # broken connection. If we reconnect first and only then read the status,
        # the fresh connection reports IDLE and we'd silently run this statement
        # on it — dropping every earlier uncommitted statement of the same
        # logical operation (e.g. the debit in reserve_withdraw without its
        # credit, or a pre-persisted withdraw hash without the debit).
        was_in_transaction = self._transaction_status != psycopg.pq.TransactionStatus.IDLE
        self._ensure()
        in_transaction = self._transaction_status != psycopg.pq.TransactionStatus.IDLE
        try:
            return self._conn.execute(query, params, **kwargs)
        except psycopg.OperationalError:
            # Never retry a statement that ran inside an already-open
            # transaction: the prior statements are uncommitted, and re-running
            # just this one on a fresh connection would silently drop them
            # (e.g. a debit without its credit). Propagate so the caller rolls
            # back and retries the whole operation from a clean state.
            if in_transaction or was_in_transaction:
                raise
            # Only the first statement of a fresh transaction may be retried,
            # and only when the server rolled it back (dead connection). A
            # statement_timeout aborts the backend transaction (status != IDLE)
            # so re-running there is both unsafe and pointless.
            can_retry = False
            try:
                can_retry = self._transaction_status == psycopg.pq.TransactionStatus.IDLE
            except Exception:
                can_retry = self._conn.broken or self._conn.closed
            if not can_retry:
                raise
            self._connect()
            return self._conn.execute(query, params, **kwargs)

    @property
    def _transaction_status(self):
        """Raw psycopg transaction status of the underlying connection.

        Treats a dead/broken connection as UNKNOWN so callers deciding whether
        to commit (e.g. ensure_user) fall back to their default behavior."""
        try:
            return self._conn.info.transaction_status
        except Exception:
            return psycopg.pq.TransactionStatus.UNKNOWN

    def commit(self) -> None:
        self._ensure()
        # Do NOT swallow OperationalError here. If the server rolled back our
        # uncommitted transaction (connection drop, statement timeout), every
        # write since the last commit/rollback is gone — the caller MUST know,
        # or it will believe a withdrawal/credit landed when it did not (a
        # pre-persisted withdraw hash without its debit, etc.) and the caller
        # would proceed as if the tx committed. Propagate so the caller
        # reconciles/retries from a clean state instead of returning success.
        self._conn.commit()

    def rollback(self) -> None:
        self._ensure()
        try:
            self._conn.rollback()
        except psycopg.OperationalError:
            # Rollback failure is benign: the transaction is already gone
            # (broken connection / server already aborted it); the goal —
            # clearing any open transaction — is already achieved.
            pass

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
