// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {SD59x18, convert, exp, ln, UNIT} from "prb-math/SD59x18.sol";

/// @title LMSR
/// @notice Logarithmic Market Scoring Rule math for on-chain prediction markets.
/// @dev Direct port of the off-chain reference implementation in bot/ledger.py.
///
///      cost(q, b)   = b * ln( sum_i exp(q_i / b) )
///      price_i(q,b) = exp(q_i / b) / sum_j exp(q_j / b)
///
///      q_i is the outstanding share supply of outcome i (== ERC1155 totalSupply
///      of that outcome's token id — see OutcomeMarket, which never lets these
///      drift apart). b is the liquidity parameter; a market funded with
///      `subsidy` USDC and sized as b = subsidy / ln(n) can *always* cover the
///      worst-case payout (every share of one outcome redeeming at $1), this is
///      the "funding theorem" — see OutcomeMarket.createMarket.
///
///      All amounts in/out of this library are USDC micro-units (1e6) as plain
///      int256, converted to SD59x18 (1e18 fixed point) internally for the
///      exp/ln operations and converted back before returning. Trades are
///      SHARE-denominated on-chain (caller picks a share quantity, gets back an
///      exact cost) rather than the SPEND-denominated binary search the Python
///      bot uses off-chain — a closed-form cost(after) - cost(before) is two
///      cheap exp() calls; a spend-denominated binary search would be dozens of
///      exp() calls per trade and is not something you want to pay Base gas for.
///      The bot can still offer a "spend $X" UX by estimating the share amount
///      off-chain first (cheap in Python) and submitting that as `shares` with
///      a `maxCost`/`minProceeds` slippage bound — same pattern as any AMM
///      exact-output quote.
library LMSR {
    /// @dev `shares` enters the math through uint256 -> int256 casts. That
    ///      conversion wraps silently in Solidity, so a value above
    ///      type(int256).max would flip NEGATIVE and corrupt the cost curve
    ///      (afterC < beforeC -> quoted cost of 0 -> free giant buys).
    ///      Every entry point therefore rejects such values outright.
    error SharesTooLarge();

    function _checkShares(uint256 shares) private pure {
        if (shares > uint256(type(int256).max)) revert SharesTooLarge();
    }

    /// @dev Numerical-stability shift, mirrors the `m = max(q_micro)` trick in
    ///      bot/ledger.py: b*ln(sum(exp(q_i/b))) == m + b*ln(sum(exp((q_i-m)/b))).
    ///      Without this, exp() overflows/underflows badly once q gets large.
    function cost(int256[] memory q, int256 b) internal pure returns (SD59x18) {
        int256 m = q[0];
        for (uint256 i = 1; i < q.length; i++) {
            if (q[i] > m) m = q[i];
        }

        SD59x18 bWad = _toWad(b);
        SD59x18 sumExp;
        for (uint256 i = 0; i < q.length; i++) {
            sumExp = sumExp + exp(_toWad(q[i] - m).div(bWad));
        }
        // m/1e6 (micro -> whole) converted to wad, plus b * ln(sumExp)
        return _toWad(m) + bWad.mul(ln(sumExp));
    }

    /// @notice Price of outcome `idx`, as an 18-decimal fraction of $1 (e.g.
    ///         0.35e18 == 35 cents). Uses the same max-shift as cost() — the
    ///         shift cancels out in the ratio, it's purely for exp() safety.
    function price(int256[] memory q, int256 b, uint256 idx) internal pure returns (SD59x18) {
        int256 m = q[0];
        for (uint256 i = 1; i < q.length; i++) {
            if (q[i] > m) m = q[i];
        }
        SD59x18 bWad = _toWad(b);
        SD59x18 sumExp;
        SD59x18 target;
        for (uint256 i = 0; i < q.length; i++) {
            SD59x18 e = exp(_toWad(q[i] - m).div(bWad));
            sumExp = sumExp + e;
            if (i == idx) target = e;
        }
        return target.div(sumExp);
    }

    /// @notice Cost in USDC micro-units to buy `shares` of outcome `idx`,
    ///         rounded UP (house-favorable, mirrors the off-chain ceil-on-buy
    ///         convention — see bot/ledger.py buy_shares comment).
    function buyCost(int256[] memory q, int256 b, uint256 idx, uint256 shares) internal pure returns (uint256) {
        _checkShares(shares);
        SD59x18 before = cost(q, b);
        q[idx] += int256(shares);
        SD59x18 afterC = cost(q, b);
        q[idx] -= int256(shares); // caller passed memory by reference; restore
        return _ceilToMicro(afterC - before);
    }

    /// @notice Proceeds in USDC micro-units from selling `shares` of outcome
    ///         `idx`, rounded DOWN (house-favorable, mirrors off-chain floor-on-
    ///         sell convention).
    function sellProceeds(int256[] memory q, int256 b, uint256 idx, uint256 shares) internal pure returns (uint256) {
        _checkShares(shares);
        SD59x18 before = cost(q, b);
        q[idx] -= int256(shares);
        SD59x18 afterC = cost(q, b);
        q[idx] += int256(shares);
        return _floorToMicro(before - afterC);
    }

    /// @dev `convert(int256)` treats its argument as a *plain count* and
    ///      multiplies by 1e18 (so convert(5) == 5.0). Our `micro` values are
    ///      already scaled (1e6 == $1), so we need a direct wrap at the 1e18
    ///      raw representation, i.e. micro * 1e12 — NOT another trip through
    ///      convert(), which would multiply by 1e18 a second time.
    function _toWad(int256 micro) private pure returns (SD59x18) {
        return SD59x18.wrap(micro * 1e12);
    }

    function _floorToMicro(SD59x18 wad) private pure returns (uint256) {
        int256 raw = SD59x18.unwrap(wad);
        return raw < 0 ? 0 : uint256(raw) / 1e12;
    }

    function _ceilToMicro(SD59x18 wad) private pure returns (uint256) {
        int256 rawWad = SD59x18.unwrap(wad);
        if (rawWad <= 0) return 0;
        return (uint256(rawWad) + 1e12 - 1) / 1e12;
    }
}
