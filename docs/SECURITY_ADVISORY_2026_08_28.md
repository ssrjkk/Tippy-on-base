# Security Advisory — 2026-08-28

## Summary

A follow-up audit round (self-run, foundry-backed) on the on-chain market
contracts and vault closed five further issues **before any deployment**.
`OUTCOME_MARKET_ADDRESS` remains unconfigured on Base mainnet and TipBotVault
was never deployed — **no user funds were at risk at any point**.

Affected revisions: all `contracts/OutcomeMarket.sol`, `contracts/TipBotVault.sol`
prior to the fix commit.

---

## VULN-3: Unbounded cancel-refund rate drains a market's escrow (Critical)

### Description

`cancelExpired` computed the per-share refund rate as
`escrow * RATE_SCALE / totalShares` with **no cap at par**. A market with a
large unspent creator subsidy and a tiny outstanding supply priced one
micro-share far above $1: the smallest holder could `claimCancelled` the
WHOLE escrow (subsidy included). Amplified by `mintCompleteSet` remaining
callable after close — an attacker could mint a 1-micro complete set for
~2 micro-USDC and then claim the entire subsidy.

### Fix

- `claimRatePerShare` is capped at `RATE_SCALE` ($1 per micro-share);
- `mintCompleteSet` reverts with `MarketNotClosed` once
  `block.timestamp >= closesAt`;
- the unused subsidy returns to the creator via the existing dust sweep.

### Regression tests

`SecurityFixes.t.sol::test_CancelRefundCappedAtPar_SubsidyNotDrainable`,
`test_MintCompleteSetBlockedAfterClose` (forge), EVM suite
`test_cancel_expired_pays_trader_not_creator` (pytest, eth-tester).

---

## VULN-4: Dispute did not pause the cancel-expiry clock (Medium)

### Description

`disputeResolution` zeroed `resolvedAt`, so a permissionless
`cancelExpired` could fire one second after the 2h dispute window lapsed —
racing/pre-empting the owner's re-resolution and converting a healthy
market into par-refunds.

### Fix

`disputeResolution` keeps `resolvedAt` as the timestamp of the LAST
resolution activity and `cancelExpired` measures its window from
`max(closesAt, resolvedAt)`.

### Regression test

`test_DisputeDelaysCancelExpiry` (forge).

---

## VULN-5: Oracle/owner resolve-dispute ping-pong (Medium)

### Description

After a dispute the oracle could immediately re-post its answer, and the
owner could dispute again — an unbounded loop that freezes resolution and
re-arms the cancel clock each cycle.

### Fix

- new error `MarketDisputed`;
- `oracleResolve` reverts while `m.disputed` — after a dispute only
  `ownerResolve` can finalize;
- `disputeResolution` is once per market (`MarketDisputed` on re-entry).

### Regression tests

`test_DisputeBlocksOracleReResolve`, `test_SecondDisputeRejected` (forge).

---

## VULN-6: TipBotVault batchDistribute all-or-nothing (Low)

### Description

One failing USDC transfer (blacklisted recipient — FiatTokenV2 reverts)
reverted the WHOLE batch after `spentInWindow` had already been increased,
bricking the remaining daily distribution window.

### Fix

Transfers are performed via a low-level call: failing recipients are
SKIPPED and reported through a new `DistributeSkipped` event; only
successful payouts count against the relayer window. A batch whose
requested sum exceeds the vault reserves reverts up front with
`InsufficientReserves(requested, available)`. The redundant post-deploy
`setDailyLimit` transaction was removed from `deploy_vault.py` (the
constructor already sets it), and both deploy scripts now verify the chain
id and that USDC code exists before broadcasting.

### Regression test

`test_batch_distribute_skips_blacklisted_recipient` (pytest, eth-tester).

---

## Acceptance / notes

- Direct USDC transfers to `OutcomeMarket` remain stranded dust by design
  (no market credits them); documented in the contract header.
- `OutcomeMarket.rescueETH` (owner-only) recovers forced-ETH; there is no
  receive() fallback.
- Deploy guards: both deploy scripts assert `EXPECTED_CHAIN_ID` and
  non-empty USDC code; `deploy_outcome_market.py` warns loudly when the
  owner equals the deployer hot key.
