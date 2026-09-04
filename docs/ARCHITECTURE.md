# Architecture

Tippy is a Telegram bot for USDC tipping, prediction markets, and paid
content on Base. Two processes, one database.

```
┌─────────────────┐     ┌──────────────────────┐
│  bot (aiogram)   │     │  web (FastAPI)        │
│  long-polling or │     │  /api/*, /me, /login  │
│  webhook         │     │  Telegram Login Widget │
└────────┬────────┘     └──────────┬───────────┘
         │                         │
         └────────┬────────────────┘
                  │
         ┌────────▼────────┐
         │   PostgreSQL     │
         │  (Ledger class)  │
         │  RLock per-proc  │
         └────────┬────────┘
                  │
         ┌────────▼────────┐
         │  Base chain      │
         │  (web3.py)       │
         │  USDC deposits   │
         │  withdrawals     │
         │  TipBotVault     │
         └─────────────────┘
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
- **TipBotVault** (contract) — holds user USDC reserves. Relayer has a
  24h rolling daily limit. Owner (multisig) has full control. Two-step
  ownership transfer.
- **Deposit scanner** — polls `eth_getLogs` for USDC `Transfer` events
  to the hot wallet or vault address. Credits internal ledger.

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
- Private-chat guards on /export, /wallet export, /import
- Rate limiting on /api/* endpoints
