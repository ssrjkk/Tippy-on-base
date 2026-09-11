# Architecture

Tippy is a Telegram bot for USDC tipping, prediction markets, and paid
content on Base. Two processes, one database.

```
┌─────────────────┐  ┌──────────────────────┐  ┌──────────────────┐
│  bot (aiogram)   │  │  web (FastAPI)        │  │  agent (optional) │
│  long-polling or │  │  /api/*, /me, /login  │  │  news → LLM →    │
│  webhook         │  │  Telegram Login Widget │  │  markets + EAS   │
└────────┬────────┘  └──────────┬───────────┘  └────────┬─────────┘
         │                      │                        │
         └──────────┬───────────┴────────────────────────┘
                    │
          ┌─────────▼────────┐
          │   PostgreSQL      │
          │  (Ledger class)   │
          │  RLock per-proc   │
          └─────────┬─────────┘
                    │
          ┌─────────▼─────────┐
          │  Base chain        │
          │  (web3.py)         │
          │  USDC deposits     │
          │  withdrawals       │
          │  multi-relayer pool│
          │  TipBotVault       │
          │  EAS attestations  │
          └────────────────────┘
```

## Processes

### bot (`python -m bot.main`)
- Telegram bot via aiogram 3.17.0
- Handles all user commands (/balance, /tip, /deposit, /market, etc.)
- One Ledger instance, one DB connection, serialized by `threading.RLock`
- Runs deposit scanner (polls `eth_getLogs` for USDC transfers)
- Runs market deadline watcher, grace period watcher

### web (`uvicorn web.server:app`)
- FastAPI dashboard + API
- `/api/stats`, `/api/solvency`, `/api/wallet` — public
- `/login`, `/api/auth/telegram`, `/api/auth/wallet` — auth
- `/me` — user dashboard (requires session cookie)
- One Ledger instance, one DB connection, serialized by `threading.RLock`
- Serves static files (HTML/CSS/JS)
- Strict CSP: nonce-based `script-src` (no inline `<script>` without a nonce,
  no inline event handlers anywhere), `style-src 'self' 'unsafe-inline'`
  (inline style attributes only)
- x402 endpoints (`/api/x402/tip`, `/api/x402/paywall`): invoice → on-chain
  payment → verify → replay-proof credit capped at the invoice price
  (an overpay is credited at price, never more)

### agent (`python -m agent.main [--loop]`, optional)
- Autonomous market-maker: fetches crypto news → cheap LLM filter → strong
  LLM decision → creates markets, places bets, sells analysis via paywall
- Fail-closed: an LLM error means NO action; spend caps are validated at
  startup (refuses to run on a misconfigured set); circuit breaker with
  cooldown after consecutive errors
- Every action is EAS-attested on Base (dedicated `AGENT_EAS_KEY`; without a
  registered `EAS_SCHEMA_UID` it degrades to a local JSONL audit trail with a
  warning)
- Calls the bot's web API (`TIPPY_BASE_URL`) as its tool surface; the DB
  blocks it from trading on its own markets

Both processes share the same PostgreSQL database. Cross-process safety
comes from atomic SQL (transactions, `SELECT FOR UPDATE`, unique
constraints) — NOT from Python locks.

## Database

PostgreSQL 16+ with `dict_row` factory. Schema managed by:
1. **Alembic** (`alembic/versions/`) — tracked migrations
2. **ensure_schema()** — idempotent DDL fallback (runs on every startup)

Key tables:
- `users` — Telegram user IDs and USDC balances (internal ledger)
- `tx_log` — immutable audit trail of all balance changes
- `markets` — LMSR prediction markets (escrow, options, status)
- `market_shares` — per-user share positions
- `user_wallets` — encrypted custodial wallet keys/seeds
- `wallet_links` — on-chain address ↔ Telegram user binding

## On-chain

- **Hot wallet** (EOA) — relayer for withdrawals and tips. Holds USDC.
  Private key in `HOT_WALLET_KEY` env var. Daily cap enforced by vault.
- **Multi-relayer pool** (`bot/chain/relayers.py`, `RELAYER_PRIVATE_KEYS`) —
  several dedicated relayer keys, each with its own per-UTC-day USDC cap.
  Usage is persisted to a state file (`RELAYER_STATE_FILE`, default
  `.relayer_usage.json`) so a restart cannot reset the daily budget.
- **TipBotVault** (contract) — holds user USDC reserves. Relayer has a
  24h rolling daily limit. Owner (multisig) has full control. Two-step
  ownership transfer.
- **Deposit scanner** — polls `eth_getLogs` for USDC `Transfer` events
  to the hot wallet or vault address. Credits internal ledger. Supports
  per-user CREATE2 deposit proxies (deterministic addresses).
- **Withdrawals** — debited and queued atomically in one transaction; the
  per-user daily cap is a guarded `INSERT ... SELECT COUNT(*)` (no
  check-then-act race, safe across processes). Queued rows flush as a
  batch (`WITHDRAW_BATCH_*`) with a direct-send fallback.
- **OutcomeMarket** — on-chain prediction markets: ERC-1155 shares, LMSR
  pricing on-chain, oracle resolution with owner dispute window, 24h
  public cancel for abandoned markets.

## Smart Wallet (ERC-4337) — P2

Gasless, non-custodial per-user accounts on Base via account abstraction.
User operations are sponsored by a VerifyingPaymaster so users don't need
ETH. Deployed & proven on Base Sepolia (see `docs/ECOSYSTEM_DESIGN.md` §8).

- **SmartAccount** (CREATE2, deterministic) — each Telegram user gets a
  counterfactual address; no creation until first fund. Implements
  `IAccount.executeUserOp` + `UserOperation calldata` struct.
- **SmartAccountFactory** — `createAccount(owner, salt)` via CREATE2 with
  deployer/owner enforcement.
- **EntryPoint v0.6** — standard ERC-4337 singleton.
- **VerifyingPaymaster** — sponsors gas; daily limits, `_recordUsage`,
  correct `postOp(PostOpMode, bytes, uint256)` for EP v0.6.
- **bot/smart_wallet.py** — UserOp build + EIP-191 signing, paymaster data,
  `create_account` / `approve_and_trade_sync`. Config `SMART_WALLET_*`.

## Security model

- Funds are custodial by design (hot wallet holds USDC)
- Vault reduces hot wallet exposure via daily limit
- WALLET_ENC_KEY encrypts user wallet keys at rest
- Stateless HMAC sessions (no server-side store; rotate SECRET_KEY = logout all)
- Web login nonces are single-use (DB PK) and pruned after
  `LOGIN_NONCE_TTL_SECONDS`
- Private-chat guards on sensitive commands (see docs/SECURITY.md)
- All bot replies that interpolate user-controlled text use `html.escape`
  (HTML parse mode, no Markdown injection)
- Rate limiting on /api/* endpoints; strict CSP on the dashboard
