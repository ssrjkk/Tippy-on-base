"""Community treasuries — group pools with voting for community expenses.

Each Telegram group can create a treasury that:
  - Accepts deposits from members
  - Has a configurable quorum for spending decisions
  - Tracks contributions and votes
  - Allows spending via majority vote

This extends the "community economy" beyond p2p tipping.
"""

import json
import time

from . import config


class TreasuryManager:
    """Manages community treasuries in PostgreSQL."""

    def __init__(self, ledger):
        self._ledger = ledger

    def _conn(self):
        return self._ledger._conn

    def ensure_treasury(self, chat_id: int, owner_tg: int) -> bool:
        """Create treasury for a Telegram group. Returns True if created."""
        with self._ledger._lock:
            existing = self._conn().execute(
                "SELECT id FROM community_treasuries WHERE chat_id = %s",
                (chat_id,),
            ).fetchone()
            if existing:
                return False
            self._conn().execute(
                "INSERT INTO community_treasuries (chat_id, owner_tg) VALUES (%s, %s)",
                (chat_id, owner_tg),
            )
            self._conn().commit()
            return True

    def get_treasury(self, chat_id: int) -> dict | None:
        """Get treasury info for a chat."""
        with self._ledger._lock:
            return self._conn().execute(
                "SELECT * FROM community_treasuries WHERE chat_id = %s",
                (chat_id,),
            ).fetchone()

    def deposit(self, chat_id: int, tg_id: int, amount_micro: int, note: str = "") -> bool:
        """Deposit USDC to community treasury from user balance."""
        with self._ledger._lock:
            treasury = self.get_treasury(chat_id)
            if not treasury:
                return False
            # transfer() moves money between two users (from_id, to_id) — a
            # treasury isn't a tg_id, so debit() (the same primitive
            # buy_shares/withdraw use to remove spendable balance) is the
            # right call here, not transfer().
            ok = self._ledger.debit(tg_id, amount_micro)
            if not ok:
                return False
            self._conn().execute(
                "UPDATE community_treasuries SET balance = balance + %s WHERE id = %s",
                (amount_micro, treasury["id"]),
            )
            self._conn().execute(
                "INSERT INTO treasury_transactions (treasury_id, kind, tg_id, amount, note) "
                "VALUES (%s, 'deposit', %s, %s, %s)",
                (treasury["id"], tg_id, amount_micro, note),
            )
            self._conn().commit()
            return True

    def propose(self, chat_id: int, proposer_tg: int, amount_micro: int,
                to_address: str, description: str) -> int | None:
        """Create a spending proposal. Returns proposal_id."""
        with self._ledger._lock:
            treasury = self.get_treasury(chat_id)
            if not treasury:
                return None
            if amount_micro > treasury["balance"]:
                return None
            closes_at = int(time.time()) + 86400  # 24h voting window
            cur = self._conn().execute(
                "INSERT INTO treasury_proposals "
                "(treasury_id, proposer_tg, amount, to_address, description, closes_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (treasury["id"], proposer_tg, amount_micro, to_address, description, closes_at),
            )
            self._conn().commit()
            return int(cur.fetchone()["id"])

    def vote(self, chat_id: int, proposal_id: int, tg_id: int, yes: bool) -> bool:
        """Vote on a proposal. Returns True if vote recorded."""
        with self._ledger._lock:
            treasury = self.get_treasury(chat_id)
            if not treasury:
                return False
            proposal = self._conn().execute(
                "SELECT * FROM treasury_proposals WHERE id = %s AND treasury_id = %s",
                (proposal_id, treasury["id"]),
            ).fetchone()
            if not proposal or proposal["status"] != "voting":
                return False
            if int(proposal["closes_at"]) < time.time():
                return False
            vote_val = 1 if yes else -1
            try:
                self._conn().execute(
                    "INSERT INTO treasury_votes (treasury_id, proposal_id, tg_id, vote) "
                    "VALUES (%s, %s, %s, %s)",
                    (treasury["id"], proposal_id, tg_id, vote_val),
                )
            except Exception:
                return False  # already voted
            # Update counters
            if yes:
                self._conn().execute(
                    "UPDATE treasury_proposals SET votes_yes = votes_yes + 1 WHERE id = %s",
                    (proposal_id,),
                )
            else:
                self._conn().execute(
                    "UPDATE treasury_proposals SET votes_no = votes_no + 1 WHERE id = %s",
                    (proposal_id,),
                )
            self._conn().commit()
            return True

    def finalize_proposals(self) -> list[dict]:
        """Finalize proposals whose voting window closed. Returns executed proposals."""
        with self._ledger._lock:
            now = int(time.time())
            proposals = self._conn().execute(
                "SELECT p.*, t.quorum_pct, t.chat_id "
                "FROM treasury_proposals p JOIN community_treasuries t ON p.treasury_id = t.id "
                "WHERE p.status = 'voting' AND p.closes_at < %s",
                (now,),
            ).fetchall()
            executed = []
            for p in proposals:
                total = p["votes_yes"] + p["votes_no"]
                if total == 0:
                    self._conn().execute(
                        "UPDATE treasury_proposals SET status = 'expired' WHERE id = %s",
                        (p["id"],),
                    )
                    continue
                pct = (p["votes_yes"] * 100) // total
                if pct >= p["quorum_pct"]:
                    # Execute: deduct from treasury
                    self._conn().execute(
                        "UPDATE community_treasuries SET balance = balance - %s WHERE id = %s",
                        (p["amount"], p["treasury_id"]),
                    )
                    self._conn().execute(
                        "UPDATE treasury_proposals SET status = 'approved' WHERE id = %s",
                        (p["id"],),
                    )
                    self._conn().execute(
                        "INSERT INTO treasury_transactions (treasury_id, kind, amount, note) "
                        "VALUES (%s, 'spend', %s, %s)",
                        (p["treasury_id"], p["amount"], p["description"]),
                    )
                    executed.append({**p, "result": "approved"})
                else:
                    self._conn().execute(
                        "UPDATE treasury_proposals SET status = 'rejected' WHERE id = %s",
                        (p["id"],),
                    )
                    executed.append({**p, "result": "rejected"})
            self._conn().commit()
            return executed

    def treasury_history(self, chat_id: int, limit: int = 20) -> list[dict]:
        """Recent treasury transactions."""
        with self._ledger._lock:
            treasury = self.get_treasury(chat_id)
            if not treasury:
                return []
            return self._conn().execute(
                "SELECT * FROM treasury_transactions WHERE treasury_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (treasury["id"], limit),
            ).fetchall()
