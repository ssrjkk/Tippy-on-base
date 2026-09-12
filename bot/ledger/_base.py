"""PostgreSQL ledger: internal USDC balances, wallet links, history.

Tips move instantly inside this ledger (no gas, no 12s wait).
Deposits credit here; withdrawals debit here and send USDC on-chain.
"""

import logging
from decimal import ROUND_FLOOR, Decimal, localcontext

from .. import config

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





