"""Ledger domain mixin: LedgerBetsMixin (split from bot/ledger.py)."""
import json
import time
from decimal import ROUND_CEILING, Decimal

from .. import config
from ._base import MICRO


class LedgerBetsMixin:
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
        """Returns 'ok' | 'closed' | 'deadline' | 'badopt' | 'balance' | 'cap'."""
        if amount_micro <= 0:
            # debit(-X) would INCREASE the balance and record a negative stake
            # that vanishes from the pot at resolution (money creation). Raise
            # instead of returning a status: callers treat unknown statuses as
            # success in some handlers.
            raise ValueError("bet amount must be positive")
        max_bet_micro = int(Decimal(config.MAX_BET_USDC) * Decimal(MICRO))
        if amount_micro > max_bet_micro:
            # Enforced at the ledger so BOTH the text path and the
            # callback-button path share the same per-trade cap (a forged
            # callback_data must not bypass MAX_BET_USDC).
            return "cap"
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
            self.ensure_user(tg_id, None, commit=False)
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
            credited_users = set()
            for tg_id, net in payouts:
                if tg_id not in credited_users:
                    self.ensure_user(tg_id, None, commit=False)
                    credited_users.add(tg_id)
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
                self.ensure_user(bet["creator"], None, commit=False)
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
            refunded_users = set()
            for p in self._bet_positions(bet_id):
                tg_id = int(p["tg_id"])
                if tg_id not in refunded_users:
                    self.ensure_user(tg_id, None, commit=False)
                    refunded_users.add(tg_id)
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
