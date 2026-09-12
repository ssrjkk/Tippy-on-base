"""Ledger domain mixin: LedgerViewsMixin (split from bot/ledger.py)."""
import json
import time
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from .. import config
from ._base import MICRO


class LedgerViewsMixin:
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
