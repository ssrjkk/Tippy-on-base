# Tippy — Community Economy in USDC on Base

[![CI](https://github.com/ssrjkk/Tippy-on-base/actions/workflows/ci.yml/badge.svg)](https://github.com/ssrjkk/Tippy-on-base/actions/workflows/ci.yml)
![Tests](https://img.shields.io/badge/tests-419%20passed-brightgreen)
![Python](https://img.shields.io/badge/python-3.12+-blue)
![Network](https://img.shields.io/badge/network-Base-0052FF)

A Telegram bot that turns any chat or community into a financial ecosystem:
**instant USDC tips, Polymarket-style prediction markets with live AMM odds,
an AI assistant, paywalled content, and per-user wallets** — all on Base.
No custom contracts in the hot path: only the official USDC contract plus an
optional audited-style treasury vault; everything else is instant internal
accounting backed by public proof-of-reserves.

**Author:** [@ssrjkk](https://t.me/ssrjkk) · [@b2wmain](https://t.me/b2wmain) · [X / Twitter](https://x.com/ludych1) · [GitHub](https://github.com/ssrjkk)

---

## Features

### 💸 Instant tips (zero gas)
- `/tip 5 @nick` — or reply `/tip 5` to any message
- Recipient gets an immediate DM notification
- 🌧️ `/rain 10 [N]` — scatter USDC across active group members
- 🔥❤️⚡👏🎉 emoji reactions tip the message author (groups)

### 📈 Prediction markets v2 — Polymarket analog (LMSR AMM)
- `/market create 50 Who wins? | Alice | Bob 7d` — creator funds the AMM liquidity
- **Live odds that move with demand** — Hanson's Logarithmic Market Scoring Rule,
  exact `Decimal` math (`b = subsidy / ln(n)`)
- `/trade <id> <opt> <amount>` — buy outcome shares at the live price
- **`/sell <id> <opt> [50%]` — sell back any time before resolution** (exit anytime,
  not locked until the end like parimutuel pools)
- `/positions` — your portfolio with live mark-to-market value and PnL
- Resolution pays **1 USDC per winning share**; the market creator keeps the
  remaining liquidity pool as their earnings
- **Guaranteed solvency by the LMSR funding theorem**: the escrow can always
  cover the worst-case payout, verified by property tests along aggressive
  random trading paths
- Deadline pings + grace-period auto-refund protection for forgotten markets

### 🎲 Parimutuel polls (quick group games)
- `/bet create Question | Option 1 | Option 2 [24h|7d]`, `/bet <id> <opt> <amount>`
- Winners split the whole pot proportionally (2% fee on net profit to the creator)
- Inline cards, quick-amount buttons, two-tap resolution, cancel/refund paths

### 🧠 AI assistant
- `/ask <question>` — ask about crypto, Base, market strategy, bot usage
- Works with **any OpenAI-compatible API** (OpenAI, OpenRouter, local vLLM/llama.cpp)
  via `AI_API_URL` / `AI_API_KEY` / `AI_MODEL`
- Reply to a message with `/ask` to use it as context; rate-limited, typing indicator

### 💛 Donations & wallets
- `/donate` — personal donation page with QR (`t.me/<bot>?start=donate_<id>`)
- Deposits auto-credit with push notifications (Basescan tx link included)
- `/link <address>` + signature → automatic deposit crediting (ecrecover-verified;
  a deposit can only be claimed by the wallet's owner — never by tx-hash sniping)
- `/wallet` — built-in custodial wallet, export/import by seed phrase
- `/withdraw <address> <amount>` — on-chain payout (1% fee, min 1 USDC, ≤5/day),
  full auto-refund for stuck/reverted transactions
- `/tx <hash>` — look up any Base transaction and decode its USDC transfer

### 🔐 Paid content & channels
- `/paywall create 5 Title` → sell posts for USDC (buyers read instantly)
- `/paywall channel 5` → paid Telegram channel access, 5 USDC / 30 days,
  one-time invite links, expired subscribers auto-kicked
- **x402 HTTP payments**: `POST /api/x402/tip` and `POST /api/x402/paywall` —
  AI agents pay on-chain via the 402 handshake (invoice → pay → replay-proof credit)

### 🖥 Web dashboard (public transparency)
- Live stats, volume chart, markets with odds/backers, leaderboards, user profiles
- **Proof of Reserves** `/api/solvency`: bot liabilities vs on-chain USDC
  (read from the TipBotVault contract when deployed, else the hot wallet)
- Base design system UI (Base Black `#0A0B0D`, Primary Blue `#0052FF`)
- Public JSON API: `/api/stats`, `/api/markets`, `/api/predictions`,
  `/api/prediction/{id}`, `/api/leaderboard`, `/api/health`, `/qr`, rate-limited per IP

### ⛓ On-chain treasury (TipBotVault)
- Users deposit USDC into the vault contract — visible to anyone on Base
- Relayer distributes under a daily limit; owner (multisig) keeps full control
- `Distributed` events make every payout publicly auditable
- Deploy: `python scripts/deploy_vault.py` (compiles with solc 0.8.24, EIP-1559 fees)

## Quick start

```bash
pip install -r requirements.txt          # + requirements-dev.txt for tests
cp .env.example .env                     # fill BOT_TOKEN, BASE_RPC_URL, HOT_WALLET_KEY
python -m bot.main                       # bot
python -m uvicorn web.server:app --host 0.0.0.0 --port 8000   # dashboard
```

1. Create a bot with [@BotFather](https://t.me/BotFather)
2. Get a Base RPC (Alchemy / Infura / QuickNode free tier — the public
   `mainnet.base.org` is unstable for `eth_getLogs`)
3. Fund the hot wallet with ~$5 ETH for withdrawal gas
4. Optional: set `AI_API_KEY` to enable `/ask`

### Docker

```bash
docker compose up -d --build   # postgres + bot + dashboard + hourly backups
```

Services: `db` (PostgreSQL 16), `bot`, `web` (healthcheck on `/api/health`),
`backup` (pg_dump every 6h, 14-day rotation). Local port 5433 to avoid clashing
with a system Postgres.

Full production walkthrough: **[DEPLOY.md](DEPLOY.md)**.

## Commands

| Command | What it does |
|---|---|
| `/start` | Menu with all sections |
| `/tip 5 @nick` | Instant USDC tip (or reply to a message) |
| `/market create 50 Q \| A \| B [24h]` | Create an AMM prediction market |
| `/markets` · `/trade` · `/sell` · `/positions` | Trade shares at live odds |
| `/bet create Q \| A \| B [24h]` · `/bets` · `/resolve` · `/cancel` | Parimutuel polls |
| `/ask <question>` | AI assistant |
| `/deposit` / `/claim <tx>` / `/link` / `/confirm` | Fund your account |
| `/withdraw <addr> <amt>` | On-chain withdrawal (1% fee) |
| `/tx <hash>` | Decode a Base transaction |
| `/rain 10 [N]` | Group giveaway |
| `/paywall ...` | Paid posts and channels |
| `/balance` / `/stats` / `/top` / `/history` | Analytics |

## Architecture

```
bot/
├─ main.py        entrypoint, background watchers (deposits, withdrawals, deadlines)
├─ handlers/      aiogram handlers by domain (_common, menu, wallet, tips, bets, markets, stats, paywall, ai)
├─ ledger.py      PostgreSQL accounting + LMSR AMM engine (Decimal-exact)
├─ base.py        web3 layer: USDC transfers, deposit scanning, tx decoding
├─ ai.py          OpenAI-compatible client (stdlib urllib, no new deps)
├─ qr.py          local QR generation
└─ config.py      env-driven configuration
web/
├─ server.py      FastAPI: public API, proof-of-reserves, x402 endpoints
└─ static/        Base-design dashboard
contracts/TipBotVault.sol    on-chain treasury (proof of reserves)
tests/           419 tests: real Postgres, real dispatcher, real crypto, local EVM
```

## Testing

```bash
docker compose up -d db       # PostgreSQL for tests (port 5433)
python -m pytest tests -q     # 419 passed
```

What is tested *for real* (not mocked): money conservation across every flow
(tips, fees, refunds, parimutuel payouts, rain, **AMM buy/sell/resolve/cancel**
— balances + escrows always sum to deposits), deposit-security (claiming
someone else's tx is rejected), fee math, real signature recovery, the full
aiogram dispatcher with real Update objects, USDC ABI decoding, the FastAPI
dashboard against a real ledger, background watchers, 14 end-to-end scenarios,
and 12 TipBotVault tests on a local EVM (eth-tester + py-evm). Only external
networks are mocked (Telegram transport, RPC).

The LMSR engine additionally has a property test proving the funding theorem:
along randomized aggressive trading paths the escrow never drops below the
worst-case payout.

## Security

- `HOT_WALLET_KEY` is money — never commit `.env`
- Recommended treasury setup: hot wallet = relayer only (daily-limit capped by
  TipBotVault), owner = multisig
- Official USDC only: `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
- Prediction markets rely on the creator resolving honestly; deadline + grace
  auto-refund bounds the damage of abandoned markets
- Anti-spam cooldowns, per-day withdrawal limits, gas-griefing protection

## Roadmap

- Per-user deposit addresses (CREATE2 vaults)
- Withdrawal batching for gas savings
- Order-book style CLOB on top of the AMM
- On-chain market escrow (trustless resolution via UMA-style oracle)

## Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, style rules,
and the money-conservation requirement for any fund-touching change.
Security issues: [SECURITY.md](SECURITY.md) (no public issues).
Community rules: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Licensed under [MIT](LICENSE).

## Grant

Prepared for the [Base Builder Grants](https://www.base.io/ecosystem/grants)
program — pitch and application package in **[GRANT.md](GRANT.md)**.

---

**Author:** [@ssrjkk](https://t.me/ssrjkk) · [@b2wmain](https://t.me/b2wmain) · [X / Twitter](https://x.com/ludych1) · [GitHub](https://github.com/ssrjkk)

Built on [Base](https://base.org) · Powered by USDC
