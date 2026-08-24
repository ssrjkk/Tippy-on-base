# Bug Bounty Policy — Tippy

**Effective:** August 2026

## Scope

The following are in-scope for bug bounty reports:

### Smart Contracts
- `LMSR.sol` — Logarithmic Market Scoring Rule pricing engine
- `OutcomeMarket.sol` — ERC1155 outcome token market with resolution
- `TipBotVault.sol` — custodial treasury vault with 24h withdrawal window

### Bot & API
- Balance manipulation: any way to create, destroy, or steal funds
- Rate-limit bypass: exceeding withdrawal/tip limits
- Auth bypass: accessing other users' balances, claiming unclaimed deposits
- SQL injection: any unsanitized input reaching PostgreSQL
- Reentrancy: re-entering withdraw/tip/trade during state mutation
- Private key leakage: any code path exposing `HOT_WALLET_KEY` or user keys

### Out of Scope
- DoS via resource exhaustion (rate limiting exists)
- Social engineering of bot operators
- Known issues in third-party dependencies (report upstream)
- Theoretical attacks with no practical exploit path

## Rewards

| Severity | Reward |
|---|---|
| Critical (direct fund loss) | $500 - $2,000 |
| High (indirect fund risk) | $100 - $500 |
| Medium (information disclosure) | $50 - $100 |
| Low (best practice) | $10 - $50 |

## Rules

1. **No public disclosure** until we confirm the fix
2. **Good faith only** — don't exploit bugs for profit
3. **First reporter** gets the reward
4. Reports via GitHub Issues (private if possible) or email
5. We aim to respond within 48 hours

## How to Report

1. Open a GitHub Issue at https://github.com/ssrjkk/Tippy-on-base/issues
2. Include: vulnerability description, reproduction steps, impact assessment
3. For critical issues: email ssrjkk@pm.me with PGP if possible

## Paid From

Bug bounty rewards are paid from the Tippy treasury (on-chain proof at `/api/solvency`).
