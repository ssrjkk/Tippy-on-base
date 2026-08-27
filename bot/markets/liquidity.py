"""Hybrid LMSR + Parimutuel pricing engine.

Blends two market-making models to get the best of both:

    LMSR (high volume):
        - Continuous pricing, no counterparty risk
        - Requires LP capital (subsidy parameter b)
        - Cost: C(q) = b × ln(Σ exp(qᵢ/b))

    Parimutuel (low volume):
        - No LP capital needed (pure pool)
        - Simple: pᵢ = stake_i / total_stake
        - High variance at low liquidity

    Hybrid:
        effective_price = α × LMSR_price + (1 - α) × parimutuel_implied_prob
        where α = 1 - exp(-volume / K)

All arithmetic uses Decimal for deterministic precision (no floating-point
drift in odds/costs).  LMSR uses the same max-shift trick as ledger.py
for numerical stability of large exponent differences.

Design:
    - Pure functions, no I/O — easy to test, easy to compose
    - Configurable via SMOOTHING_K (blend scaling) and FEE_PCT
    - Alpha function: smooth sigmoid that transitions from parimutuel
      to LMSR over the volume range [0, 5K]
"""

from decimal import Decimal, localcontext

# ---------------------------------------------------------------------------
# Precision & helpers
# ---------------------------------------------------------------------------
_LMSR_PREC = 50


def _d(x) -> Decimal:
    """Fast Decimal conversion."""
    return Decimal(x)


# ---------------------------------------------------------------------------
# LMSR primitives (same math as ledger.py, but standalone)
# ---------------------------------------------------------------------------

def lmsr_cost(q: list[int], b: int) -> Decimal:
    """LMSR cost function C(q) = b × ln(Σ exp(qᵢ/b)).

    q: micro-quantities per outcome (e.g. [500000, 0] = 0.5 USDC YES, 0 NO)
    b: liquidity parameter in micro-USDC (determines slippage)
    Returns cost in micro-USDC.
    """
    if b <= 0:
        raise ValueError("b must be positive")
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        bd = _d(b)
        m = max(q)  # shift for numerical stability
        s = sum(((_d(qi) - m) / bd).exp() for qi in q)
        return bd * (s.ln() + _d(m) / bd)


def lmsr_prices(q: list[int], b: int) -> list[Decimal]:
    """Current probability per outcome (0..1)."""
    if b <= 0:
        raise ValueError("b must be positive")
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        bd = _d(b)
        m = max(q)
        exps = [((_d(qi) - m) / bd).exp() for qi in q]
        total = sum(exps)
        return [e / total for e in exps]


def lmsr_trade_cost(
    q: list[int], b: int, option_idx: int, shares: int
) -> tuple[Decimal, list[int]]:
    """Cost of buying `shares` micro-shares of outcome `option_idx`.

    Returns (cost_micro, new_quantities).
    """
    new_q = list(q)
    new_q[option_idx] += shares
    cost = lmsr_cost(new_q, b) - lmsr_cost(q, b)
    return cost, new_q


# ---------------------------------------------------------------------------
# Parimutuel pricing (pure pool, no LP)
# ---------------------------------------------------------------------------

def parimutuel_prices(stakes: list[int]) -> list[Decimal]:
    """Implied probability from pool stakes: pᵢ = stake_i / total."""
    total = sum(stakes)
    if total == 0:
        n = len(stakes)
        return [_d(1) / _d(n)] * n  # uniform if empty
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        return [_d(s) / _d(total) for s in stakes]


def parimutuel_payout(
    stakes: list[int], winner_idx: int, fee_pct: Decimal = _d("0.02")
) -> Decimal:
    """Payout per micro-stake for the winning outcome.

    payout = total_pool × (1 - fee) / total_stake_winners
    """
    total = sum(stakes)
    winner_stake = stakes[winner_idx]
    if winner_stake == 0:
        return _d(0)
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        return _d(total) * (_d(1) - fee_pct) / _d(winner_stake)


# ---------------------------------------------------------------------------
# Blending: alpha = f(volume)
# ---------------------------------------------------------------------------

def alpha(volume_24h: int, k: int = 500_000) -> Decimal:
    """Smooth blend factor: α ∈ [0, 1].

    α = 1 - exp(-volume / k)

    At volume=0 → α=0 (pure parimutuel)
    At volume=k → α≈0.63
    At volume=5k → α≈0.993 (near-pure LMSR)

    k is in micro-USDC (default 0.50 USDC = 500k micro).
    For market-level scaling, multiply k by number of outcomes.
    """
    if volume_24h <= 0:
        return _d(0)
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        return _d(1) - (-_d(volume_24h) / _d(k)).exp()


# ---------------------------------------------------------------------------
# Hybrid pricing
# ---------------------------------------------------------------------------

def hybrid_prices(
    q: list[int],
    b: int,
    stakes: list[int],
    volume_24h: int = 0,
    k: int = 500_000,
) -> list[Decimal]:
    """Effective price per outcome via α-blended LMSR + parimutuel.

    p_hybrid = α × p_lmsr + (1-α) × p_parimutuel

    At low volume (α→0): parimutuel dominates (no LP capital risk).
    At high volume (α→1): LMSR dominates (continuous pricing).
    """
    n = len(q)
    if len(stakes) != n:
        raise ValueError("q and stakes must have same length")
    a = alpha(volume_24h, k * n)  # scale k by n for fair comparison
    lmsr_p = lmsr_prices(q, b)
    para_p = parimutuel_prices(stakes)
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        return [a * lp + (_d(1) - a) * pp for lp, pp in zip(lmsr_p, para_p)]


def hybrid_trade_cost(
    q: list[int],
    b: int,
    stakes: list[int],
    volume_24h: int,
    option_idx: int,
    shares: int,
    k: int = 500_000,
    fee_pct: Decimal = _d("0.02"),
) -> tuple[Decimal, dict]:
    """Cost of buying `shares` micro-shares of outcome `option_idx`.

    Returns (cost_micro, meta) where meta contains:
        lmsr_cost, parimutuel_cost, blended_cost, alpha, new_q, new_stakes
    """
    # LMSR component
    lmsr_c, new_q = lmsr_trade_cost(q, b, option_idx, shares)

    # Parimutuel component: cost = shares (user puts stake into pool)
    # But pool payout depends on total, so effective cost = shares
    para_c = _d(shares)

    # Blend costs
    a = alpha(volume_24h, k * len(q))
    blended = a * lmsr_c + (_d(1) - a) * para_c

    # Apply fee on top
    fee = (blended * fee_pct).quantize(_d("1"))
    total_cost = blended + max(fee, _d(1))  # min 1 micro fee

    new_stakes = list(stakes)
    new_stakes[option_idx] += shares

    meta = {
        "lmsr_cost": lmsr_c,
        "parimutuel_cost": para_c,
        "blended_cost": blended,
        "alpha": a,
        "fee": fee,
        "total_cost": total_cost,
        "new_q": new_q,
        "new_stakes": new_stakes,
    }
    return total_cost, meta


def optimal_b(daily_volume: int, n_outcomes: int) -> int:
    """Optimal liquidity parameter b = V / (N × ln(N)).

    Returns b in micro-USDC.  For N=2 (YES/NO):
        V=$10k/day → b≈7215 USDC
        V=$100k/day → b≈72150 USDC
    """
    if n_outcomes < 2:
        raise ValueError("need at least 2 outcomes")
    with localcontext() as ctx:
        ctx.prec = _LMSR_PREC
        b = _d(daily_volume) / (_d(n_outcomes) * _d(n_outcomes).ln())
        return int(b.quantize(_d("1")))
