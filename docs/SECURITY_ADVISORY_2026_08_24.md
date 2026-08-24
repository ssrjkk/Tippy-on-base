# Security Advisory — 2026-08-24

## Summary

Two critical vulnerabilities in the on-chain prediction market contracts
(`contracts/OutcomeMarket.sol`, `contracts/LMSR.sol`) were found in an
external security review and are fixed in this revision.

**Affected:** all pre-deploy revisions of `OutcomeMarket` / `LMSR`
(solidity ^0.8.24) prior to the fix commit.

**Deployed status:** `OUTCOME_MARKET_ADDRESS` was **never configured** on
Base mainnet (empty in `.env` → on-chain markets off). **No user funds were
at risk at any point.** The fixes land before first deployment.

---

## VULN-1: Signed-cast wrap in LMSR pricing (Critical)

### Description

`LMSR.buyCost()` / `LMSR.sellProceeds()` converted trade size with a plain
`int256(shares)` cast. In Solidity, uint256→int256 conversion **wraps
silently**: any `shares > type(int256).max` became a huge *negative*
quantity, driving the LMSR cost curve negative, so

    cost(after) - cost(before) < 0  →  _ceilToMicro() == 0

The market then quoted a **cost of 0 micro-USDC** for an arbitrarily large
share mint (`OutcomeMarket.buy()`), which could later be redeemed at $1 par
or used to drain escrow via `cancelExpired()`/`redeem()`.
The same wrap existed in `_currentQ()` (supply→q cast).

### Fix (defense in depth)

1. `LMSR`: explicit bounds check on every entry point — reverts with
   `SharesTooLarge()` if `shares > uint256(type(int256).max)`.
2. `OutcomeMarket`: new hard cap `MAX_SUPPLY_PER_OUTCOME = 1e15`
   (~$1B par) enforced on **every minting path** — `buy()` and
   `mintCompleteSet()`; zero-size trades rejected via `InvalidShares()`.
3. With supplies capped, the supply→q cast in `_currentQ()` is provably safe.

### Regression tests

`contracts/test/forge/SecurityFixes.t.sol`:
`test_RevertOnHugeShares_MarketLevel`, `test_RevertOnHugeShares_LMSRLevel`,
`test_NormalSharesStillWork`, `test_SupplyCapReverts`, `test_ZeroSharesReverts`.

---

## VULN-2: cancelExpired paid refunds to the creator (Critical)

### Description

`OutcomeMarket.cancelExpired()` computed each outcome's pro-rata slice of
the escrow correctly — then transferred **every slice to `m.creator`**
instead of the shareholders:

```solidity
uint256 refund = (escrow * shares) / totalShares;
usdc.safeTransfer(m.creator, refund);   // ← holders get nothing
```

Any expired-but-unresolved market let the creator (or anyone triggering the
cancel) confiscate trader cost basis in addition to keeping their subsidy.

### Why the suggested push-fix was not applicable

ERC1155 keeps no on-chain enumeration of holders, so a push loop "for each
holder" cannot be implemented without unbounded off-chain indexing.

### Fix (pull-based claims)

* `cancelExpired()` now marks the market cancelled and, **if shareholders
  exist**, reserves the whole escrow at one uniform per-share rate
  (`claimRatePerShare = escrow * 1e12 / totalShares`,
  `unclaimedEscrowMicro = escrow`). Nobody is paid at cancel time.
* New `claimCancelled(marketId)`: burns **all** of the caller's tokens for
  that market and pays `burned * rate / 1e12`. Reentrancy-guarded; capped by
  the reserve; reverts with `NothingToRedeem()` for non-holders.
* Creator receives funds only in two legitimate cases:
  * no shares exist at cancel time → subsidy returned;
  * after the final claim burns the last token → remaining floor-division
    dust is swept to the creator (`CreatorSwept`), so nothing stays locked.

### Regression tests

`test_CancelExpiredRefundsTraders_NotCreator` (the original exploit PoC now
fails to enrich anyone but the trader),
`test_CancelExpiredWithNoHoldersReturnsSubsidyToCreator`,
`test_TwoTradersConservation_LastClaimSweepsDustToCreator` (strict
conservation: payouts + dust == escrow, contract drained to 0),
`test_ClaimWithoutPositionReverts`, `test_TradingBlockedAfterCancel`.

---

## Timeline (all times UTC)

| When | Event |
|---|---|
| 2026-08-24 | Two external critical reports received (with PoCs) |
| 2026-08-24 | Both findings verified against source; deployment check confirmed contracts never deployed |
| 2026-08-24 | Fixes implemented + forge regression tests added |

## Actions for integrators

* If you indexed events from any testnet deployment of the pre-fix
  `OutcomeMarket`, discard those markets — do not migrate them.
* Run `bash scripts/setup_foundry.sh && forge test --match-contract SecurityFixesTest`
  to reproduce both fixes locally.
