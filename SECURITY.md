# Security Policy

Tippy moves real money (USDC on Base). Security reports are taken seriously
and handled with priority.

## Supported versions

| Version | Supported |
|---|---|
| `main` branch | ✅ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security problems.**

Contact channels (preferred order):

1. Telegram: [@ssrjkk](https://t.me/ssrjkk)
2. X / Twitter: [@ludych1](https://x.com/ludych1) (DMs open)

Include: affected component, reproduction steps, impact assessment, and any
proof-of-concept. You will get an acknowledgement within **48 hours** and a
fix timeline within **7 days**.

Please give us reasonable time to patch before public disclosure; we will
credit reporters in the release notes unless anonymity is requested.

## Scope highlights (what we care about most)

- **Fund safety**: any path that creates, destroys, or redirects USDC
  (double-credits, replay of deposits/x402 payments, AMM escrow insolvency,
  fee bypass, withdrawal race conditions)
- **Deposit claiming**: `/claim` must only ever credit the wallet owner
  (ecrecover binding)
- **Key handling**: anything that leaks `HOT_WALLET_KEY`, per-user wallet
  seeds/keys (`bot/wallets.py`), or webhook secrets
- **Webhook auth**: `X-Telegram-Bot-Api-Secret-Token` verification bypasses

## Known design boundaries (not vulnerabilities)

- Parimutuel bets and LMSR prediction markets resolve by the market creator;
  deadline + grace-period auto-refund bounds the damage of an absent creator.
  On-chain trustless resolution is on the roadmap.
- The hot wallet is custodial by design; production deployments should use
  TipBotVault with a multisig owner so the relayer key is daily-limit capped.

## Wallet encryption key (WALLET_ENC_KEY)

`WALLET_ENC_KEY` is a **dedicated 32-byte secret** used to encrypt every
user's custodial wallet private key and BIP-39 seed at rest in the
database. It MUST be set explicitly in `.env` (see `.env.example`); if it
is missing or shorter than 32 bytes the bot falls back to deriving a key
from `HOT_WALLET_KEY` and logs a security warning from `config.validate()`.

Why it matters: a leak of `.env` together with a database dump (backups run
every 6h) lets an attacker decrypt **all** users' wallets, not just the hot
wallet. `WALLET_ENC_KEY` must be a separate random value
(`python -c "import secrets; print(secrets.token_hex(32))"`), never
`HOT_WALLET_KEY`, and must be backed up separately from the database.
