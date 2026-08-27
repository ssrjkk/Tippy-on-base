"""Tests for the hybrid LMSR+parimutuel pricing engine."""


import pytest

from bot.markets.liquidity import (
    _d,
    alpha,
    hybrid_prices,
    hybrid_trade_cost,
    lmsr_cost,
    lmsr_prices,
    lmsr_trade_cost,
    optimal_b,
    parimutuel_payout,
    parimutuel_prices,
)

# ---------------------------------------------------------------------------
# LMSR
# ---------------------------------------------------------------------------

class TestLMSR:
    def test_prices_sum_to_one(self):
        prices = lmsr_prices([500_000, 0], 1_000_000)
        assert abs(sum(prices) - _d(1)) < _d("1e-10")

    def test_equal_quantities_equal_prices(self):
        prices = lmsr_prices([500_000, 500_000], 1_000_000)
        assert abs(prices[0] - _d("0.5")) < _d("1e-10")

    def test_bias_toward_heavier_outcome(self):
        prices = lmsr_prices([1_000_000, 0], 500_000)
        assert prices[0] > _d("0.7")  # YES dominates

    def test_cost_positive(self):
        cost = lmsr_cost([500_000, 0], 1_000_000)
        assert cost > 0

    def test_zero_quantity_gives_subsidy_cost(self):
        """C(0,0,...) = b × ln(n) — the LMSR subsidy (logarithmic house edge)."""
        cost = lmsr_cost([0, 0], 1_000_000)
        assert cost > 0
        # b=1M, n=2: cost = 1M × ln(2) ≈ 693147
        assert abs(cost - _d(693147)) < _d("1000")

    def test_trade_cost_increases_shares(self):
        q = [0, 0]
        cost, new_q = lmsr_trade_cost(q, 1_000_000, 0, 100_000)
        assert cost > 0
        assert new_q[0] == 100_000
        assert new_q[1] == 0

    def test_larger_trade_costs_more(self):
        c1, _ = lmsr_trade_cost([0, 0], 1_000_000, 0, 100_000)
        c2, _ = lmsr_trade_cost([0, 0], 1_000_000, 0, 500_000)
        assert c2 > c1

    def test_b_zero_raises(self):
        with pytest.raises(ValueError):
            lmsr_prices([1, 0], 0)


# ---------------------------------------------------------------------------
# Parimutuel
# ---------------------------------------------------------------------------

class TestParimutuel:
    def test_empty_pool_uniform(self):
        prices = parimutuel_prices([0, 0])
        assert prices == [_d("0.5"), _d("0.5")]

    def test_proportional(self):
        prices = parimutuel_prices([75, 25])
        assert abs(prices[0] - _d("0.75")) < _d("1e-10")

    def test_payout_no_fee(self):
        p = parimutuel_payout([100, 200], 0, fee_pct=_d(0))
        assert p == _d(3)  # 300 / 100

    def test_payout_with_fee(self):
        p = parimutuel_payout([100, 200], 0, fee_pct=_d("0.02"))
        assert abs(p - _d("2.94")) < _d("0.01")

    def test_payout_no_winners(self):
        p = parimutuel_payout([0, 100], 0, fee_pct=_d(0))
        assert p == 0


# ---------------------------------------------------------------------------
# Alpha blending
# ---------------------------------------------------------------------------

class TestAlpha:
    def test_zero_volume(self):
        assert alpha(0) == _d(0)

    def test_high_volume(self):
        a = alpha(5_000_000)
        assert a > _d("0.99")

    def test_mid_volume(self):
        a = alpha(500_000)
        assert _d("0.5") < a < _d("0.75")

    def test_monotonically_increasing(self):
        a1 = alpha(100_000)
        a2 = alpha(500_000)
        a3 = alpha(2_000_000)
        assert a1 < a2 < a3


# ---------------------------------------------------------------------------
# Hybrid pricing
# ---------------------------------------------------------------------------

class TestHybrid:
    def test_empty_pool_matches_lmsr(self):
        """With zero stakes, hybrid = pure LMSR (alpha dominates at any vol)."""
        prices = hybrid_prices([0, 0], 1_000_000, [0, 0], volume_24h=10_000_000)
        lmsr_p = lmsr_prices([0, 0], 1_000_000)
        assert abs(prices[0] - lmsr_p[0]) < _d("0.01")

    def test_high_volume_matches_lmsr(self):
        """At high volume, α→1 so hybrid ≈ LMSR."""
        q = [500_000, 0]
        b = 1_000_000
        stakes = [300_000, 200_000]
        hp = hybrid_prices(q, b, stakes, volume_24h=100_000_000)
        lp = lmsr_prices(q, b)
        assert abs(hp[0] - lp[0]) < _d("0.01")

    def test_low_volume_lean_parimutuel(self):
        """At low volume, α→0 so hybrid ≈ parimutuel."""
        q = [0, 0]
        b = 1_000_000
        stakes = [90, 10]
        hp = hybrid_prices(q, b, stakes, volume_24h=100)
        pp = parimutuel_prices(stakes)
        # parimutuel is 0.9/0.1, LMSR is 0.5/0.5 (equal q)
        # hybrid should be closer to parimutuel
        assert abs(hp[0] - pp[0]) < _d("0.05")

    def test_prices_sum_to_one(self):
        prices = hybrid_prices([500_000, 0], 1_000_000, [300, 200], volume_24h=1_000_000)
        assert abs(sum(prices) - _d(1)) < _d("1e-10")

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            hybrid_prices([0, 0], 1_000_000, [100], volume_24h=0)


# ---------------------------------------------------------------------------
# Hybrid trade cost
# ---------------------------------------------------------------------------

class TestHybridTradeCost:
    def test_returns_valid_meta(self):
        cost, meta = hybrid_trade_cost(
            [0, 0], 1_000_000, [0, 0], 1_000_000, 0, 100_000
        )
        assert cost > 0
        assert "alpha" in meta
        assert "lmsr_cost" in meta
        assert "parimutuel_cost" in meta
        assert meta["new_q"][0] == 100_000
        assert meta["new_stakes"][0] == 100_000

    def test_high_volume_uses_lmsr_cost(self):
        cost_high, meta_high = hybrid_trade_cost(
            [0, 0], 1_000_000, [0, 0], 100_000_000, 0, 100_000
        )
        cost_low, meta_low = hybrid_trade_cost(
            [0, 0], 1_000_000, [0, 0], 100, 0, 100_000
        )
        # at high volume, alpha is near 1 so blended ≈ lmsr
        assert meta_high["alpha"] > _d("0.99")
        # at low volume, alpha is near 0 so blended ≈ parimutuel (= shares)
        assert meta_low["alpha"] < _d("0.1")


# ---------------------------------------------------------------------------
# Optimal b
# ---------------------------------------------------------------------------

class TestOptimalB:
    def test_basic(self):
        b = optimal_b(10_000_000, 2)  # $10k, 2 outcomes
        assert b > 0
        assert b > 5_000_000  # > $5k

    def test_more_outcomes_smaller_b(self):
        b2 = optimal_b(10_000_000, 2)
        b5 = optimal_b(10_000_000, 5)
        assert b5 < b2

    def test_more_volume_larger_b(self):
        b1 = optimal_b(1_000_000, 2)
        b2 = optimal_b(10_000_000, 2)
        assert b2 > b1
