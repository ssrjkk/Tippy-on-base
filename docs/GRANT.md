# Tippy — Base Builder Grant Application

## Track: Agents / Agentic Commerce

## One-liner
Tippy is a Telegram bot + autonomous AI agent that creates and trades prediction markets on Base, monetizing analysis via x402 micro-payments — a fully on-chain agent economy.

## What it does
1. **Telegram Bot** — USDC tipping, prediction markets, paywalls, paid channels on Base
2. **Autonomous Agent** — monitors news, creates markets, trades on its own analysis
3. **x402 Monetization** — agent sells signals to other agents/humans via HTTP 402 handshake

## Demo
- Agent reads a crypto headline → creates a prediction market on Base
- Agent places a $1 bet on its own market (capped, transparent)
- Agent mints a paywalled analysis post → buys it back via x402 payment
- Full cycle: perceive → decide → act → monetize → attest (on-chain)

## Tech Stack
- **Bot:** Python 3.12, aiogram 3.17, FastAPI, PostgreSQL
- **On-chain:** Solidity 0.8.24, LMSR AMM, ERC1155 outcome shares, USDC settlement
- **Payments:** x402 (HTTP 402 + USDC on Base)
- **AI:** Groq/OpenAI LLM for market decisions, structured JSON output
- **Infrastructure:** Alembic migrations, Foundry tests, GitHub Actions CI

## Key Metrics (for grant KPIs)
| Metric | Target | Measurement |
|--------|--------|-------------|
| x402 agent-transactions | ≥50 in 30 days | On-chain USDC transfers via x402 endpoints |
| Agent-created markets | ≥10 in 30 days | `create_market` calls from agent tg_id |
| Signal revenue | ≥$10 in 30 days | x402 paywall purchases by external agents |
| Agent PnL | Non-negative at 30 days | Agent wallet balance vs starting capital |

## Safety
- **No raw keys** — hot wallet key encrypted at rest (WALLET_ENC_KEY)
- **Spend caps** — $50/day, $10/tx, 20 actions/hour, enforced in code
- **Circuit breaker** — 3 consecutive errors → 5min cooldown
- **Agent isolation** — agent cannot withdraw, deploy contracts, or modify config
- **Audit trail** — every action logged to ledger + local JSONL (EAS attestation planned)

## Links
- GitHub: https://github.com/ssrjkk/Tippy-on-base
- Bot: @tippy_on_base_bot
- Contract: TipBotVault.sol (two-step ownership, daily rolling limit)

## What we need
- **Render deployment** credits for persistent hosting
- **Base Sepolia** testnet USDC for agent demo
- **Feedback** on agent architecture for production scaling
