"""Ledger domain mixin: LedgerMessagesMixin (split from bot/ledger.py)."""
import time


class LedgerMessagesMixin:
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
                self._conn.rollback()
                return False, "duplicate", author
            if not self.debit(reactor_id, amount_micro):
                # debit() leaves its UPDATE transaction open (it returns
                # without committing/rolling back); roll it back here so the
                # shared connection doesn't carry a stale write into the next
                # unrelated ledger call or block concurrent DDL.
                self._conn.rollback()
                return False, "balance", author
            self.credit(author, amount_micro, "tip", counterparty=str(reactor_id), note="reaction", commit=False)
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
