"""Ledger domain mixin: LedgerMarketsMixin (split from bot/ledger.py)."""
import json
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from .. import config
from ._base import _LMSR_PREC, _d, lmsr_buy_shares, lmsr_prices, lmsr_sell_value


class LedgerMarketsMixin:
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
        if subsidy_micro < 0:
            raise ValueError(f"market subsidy must be non-negative (got {subsidy_micro})")
        n = len(options)
        if n < 2:
            raise ValueError(f"market must have at least 2 options (got {n})")
        with localcontext() as ctx:
            ctx.prec = _LMSR_PREC
            b = int((_d(subsidy_micro) / _d(n).ln()).to_integral_value(rounding=ROUND_FLOOR))
        if b <= 0:
            return "subsidy"
        with self._lock:
            self.ensure_user(creator_tg_id, None, commit=False)
            if not self.debit(creator_tg_id, subsidy_micro):
                self._conn.rollback()
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



    def bulk_amm_market_views(self, market_ids: list[int]) -> dict[int, dict]:
        """Batch :meth:`amm_market_view` for many markets — replaces the N+1
        query pattern (get_market + quantities + trader count per market) with
        a fixed set of batched queries against the single serialized
        connection."""
        if not market_ids:
            return {}
        ids = list(dict.fromkeys(int(i) for i in market_ids))
        placeholders = ",".join(["%s"] * len(ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM markets WHERE id IN ({placeholders})", ids
            ).fetchall()
            share_rows = self._conn.execute(
                f"SELECT market_id, option_idx, SUM(shares) AS s FROM market_shares "
                f"WHERE market_id IN ({placeholders}) GROUP BY market_id, option_idx",
                ids,
            ).fetchall()
            trader_rows = self._conn.execute(
                f"SELECT market_id, COUNT(DISTINCT tg_id) AS n FROM market_shares "
                f"WHERE market_id IN ({placeholders}) AND shares > 0 "
                f"GROUP BY market_id",
                ids,
            ).fetchall()
        by_id = {int(r["id"]): r for r in rows}
        quantities_by_id: dict[int, dict[int, int]] = {}
        for r in share_rows:
            quantities_by_id.setdefault(int(r["market_id"]), {})[
                int(r["option_idx"])
            ] = int(r["s"])
        traders_by_id = {int(r["market_id"]): int(r["n"]) for r in trader_rows}
        out: dict[int, dict] = {}
        for mid in ids:
            m = by_id.get(mid)
            if not m:
                continue
            options = json.loads(m["options"])
            totals = quantities_by_id.get(mid, {})
            q = [totals.get(i, 0) for i in range(len(options))]
            prices = lmsr_prices(q, int(m["b_micro"]))
            out[mid] = {
                "id": int(m["id"]),
                "question": m["question"],
                "status": m["status"],
                "winner": m["winner"],
                "close_at": m["close_at"],
                "creator": int(m["creator"]),
                "liquidity_micro": int(m["escrow_micro"]),
                "subsidy_micro": int(m["subsidy_micro"]),
                "traders": traders_by_id.get(mid, 0),
                "volume_micro": sum(q),
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
        return out



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

        Returns ('ok', info) or ('closed'|'deadline'|'badopt'|'balance'|'toosmall'|'ownmarket', {}).
        The share count is floored against the exact LMSR cost curve, so the
        user never overpays; the sub-micro remainder stays in the escrow.
        Trades below MARKET_MIN_TRADE_MICRO are rejected before any debit
        (dust orders would move prices by nothing but spam the tx log).
        """
        if spend_micro < 10_000:  # 0.01 USDC
            return "toosmall", {}
        with self._lock:
            committed = False
            try:
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
                # Anti-manipulation: the autonomous agent must never trade against
                # its own markets. Enforced in the DB (survives restarts) so the
                # in-memory guard in agent/tools.py is not the only line of defense.
                if tg_id == config.AGENT_TG_ID and int(m["creator"]) == tg_id:
                    self._conn.rollback()
                    return "ownmarket", {}
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
                self.ensure_user(tg_id, None, commit=False)
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
                committed = True
                return "ok", {
                    "shares": shares,
                    "cost": spend_micro,
                    "price": prices[option_idx],
                    "label": options[option_idx],
                }
            finally:
                if not committed:
                    # Never leak the FOR UPDATE row lock or leave the connection
                    # in INERROR when an unexpected exception fires mid-operation.
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass



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
            cur = self._conn.execute(
                "UPDATE market_shares SET shares = shares - %s, cost_micro = %s "
                "WHERE market_id = %s AND tg_id = %s AND option_idx = %s AND shares >= %s",
                (shares, new_cost, market_id, tg_id, option_idx, shares),
            )
            if cur.rowcount == 0:
                # Another transaction sold/closed the position between read and write.
                self._conn.rollback()
                return "noshare", {}
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

            # Trading closes at `close_at`; resolution must wait until then so a
            # freshly-listed market can't be resolved before others can weigh in.
            if m["close_at"] is not None and int(time.time()) < int(m["close_at"]):
                self._conn.rollback()
                return False, "Рынок ещё не достиг дедлайна — резолв доступен после закрытия торгов.", []

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
                "AND tg_id <> %s GROUP BY tg_id",
                (market_id, winning_idx, int(m["creator"])),
            ).fetchall()

            escrow = int(m["escrow_micro"])
            # 1 micro-share pays 1 micro-USDC (1e6 shares = 1 USDC payout)
            payout_total = sum(int(w["s"]) for w in winner_rows)
            # An outcome nobody but the creator holds (or nobody at all) must
            # not be declared the winner: with zero payouts the whole escrow —
            # subsidy included — would flow to the creator. Such a market can
            # only be cancelled (refunded) instead.
            if payout_total <= 0:
                self._conn.rollback()
                return False, (
                    "Нельзя объявить победителя без держателей исхода у других "
                    "участников — отмените рынок (возврат средств)."
                ), []
            if payout_total > escrow:  # cannot happen per funding theorem; belt & braces
                payout_total = escrow

            payouts: list[dict] = []
            weights: list[tuple[int, int]] = []  # (tg_id, gross) for leftover math
            distributed = 0
            credited_users = set()
            for w in winner_rows:
                gross = int(w["s"])
                if distributed + gross > payout_total:
                    gross = payout_total - distributed
                if gross <= 0:
                    continue
                distributed += gross
                tg = int(w["tg_id"])
                if tg not in credited_users:
                    self.ensure_user(tg, None, commit=False)
                    credited_users.add(tg)
                weights.append((tg, gross))
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
            if leftover > 0 and weights:
                # Creator takes the documented fee on winnings; every remaining
                # micro of escrow goes pro-rata to the winners instead of being
                # swept to the creator.
                creator_fee = min(
                    int((Decimal(distributed) * config.WIN_FEE_PCT).to_integral_value(rounding=ROUND_CEILING)),
                    leftover,
                )
                if creator_fee > 0:
                    self.ensure_user(int(m["creator"]), None, commit=False)
                    self._conn.execute(
                        "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                        (creator_fee, int(m["creator"])),
                    )
                    self._conn.execute(
                        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                        "VALUES ('fee', %s, %s, %s, %s)",
                        (int(m["creator"]), str(market_id), creator_fee, "market fees"),
                    )
                    leftover -= creator_fee
                if leftover > 0:
                    total_w = sum(g for _, g in weights)
                    extra: dict[int, int] = {}
                    given, remainders = 0, []
                    for tg, g in weights:
                        q = leftover * g // total_w
                        extra[tg] = q
                        given += q
                        remainders.append((leftover * g - q * total_w, tg, g))
                    for _rem, tg, _g in sorted(remainders, key=lambda r: (-r[0], -r[2], r[1])):
                        if given >= leftover:
                            break
                        extra[tg] = extra[tg] + 1
                        given += 1
                    for tg, amt in extra.items():
                        if amt <= 0:
                            continue
                        if tg not in credited_users:
                            self.ensure_user(tg, None, commit=False)
                            credited_users.add(tg)
                        self._conn.execute(
                            "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                            (amt, tg),
                        )
                        self._conn.execute(
                            "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                            "VALUES ('market_win', %s, %s, %s, %s)",
                            (tg, str(market_id), amt, m["question"]),
                        )
                        for p in payouts:
                            if p["tg_id"] == tg:
                                p["net_micro"] += amt
                                break
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
            refunded_users = set()
            for r in rows:
                refund = min(max(int(r["cost_micro"]), 0), available)
                if refund <= 0:
                    continue
                available -= refund
                tg = int(r["tg_id"])
                if tg not in refunded_users:
                    self.ensure_user(tg, None, commit=False)
                    refunded_users.add(tg)
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
                self.ensure_user(int(m["creator"]), None, commit=False)
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
