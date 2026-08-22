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

## Vault security (TipBotVault.sol)

The on-chain vault uses a **two-step ownership transfer** (`transferOwnership`
→ `acceptOwnership`).  The new owner must call `acceptOwnership()` to
complete the handover; a mistyped address cannot steal the vault.

The relayer's daily spending cap uses a **24-hour rolling window** (not a
calendar day), preventing the midnight-boundary bypass where a relayer could
spend `dailyLimit` on the last block of one day and again on the first block
of the next.

## Session revocation

Web sessions are **stateless signed HMAC cookies** (no server-side store).
There is no way to revoke a single session — the only kill-switch is to
rotate `SECRET_KEY` in `.env`, which logs out **every** user simultaneously.
Document this so operators know the trade-off before deploying.

## Private-chat guards

The `/export`, `/wallet export`, and `/import` commands refuse to execute
outside a private chat (`message.chat.type != "private"`).  They reply with
a hint to DM the bot and best-effort delete the triggering message, preventing
leakage of private keys and seed phrases into group history.

## Emergency runbook

### Kill-switches

| Scenario | Action | Impact |
|---|---|---|
| Hot wallet key compromised | Rotate `HOT_WALLET_KEY` in `.env`, restart bot. If vault deployed: call `vault.setRelayer(newAddress)` from owner. | Hot wallet drained up to `dailyLimit` since last `_rollWindow` reset. Vault funds safe (relayer can't withdraw reserves). |
| Session cookie leak | Rotate `SECRET_KEY` in `.env`, restart web. | All users logged out; must re-authenticate. |
| `WALLET_ENC_KEY` compromised | Rotate `WALLET_ENC_KEY`, re-encrypt all user wallets (`scripts/reencrypt_wallets.py` or manual SQL). | Old key can decrypt dumps taken before rotation. Rotate DB backups too. |
| Vault owner key compromised | Call `vault.transferOwnership(newSafe)` immediately; attacker has 0-window until new owner calls `acceptOwnership`. Contact team to coordinate. | Vault funds at risk until ownership transferred. |
| Relayer exceeded daily limit | `vault.setDailyLimit(0)` from owner to freeze all relayer payouts, then investigate. | All withdrawal/tip payouts stop until limit restored. |
| Bot flooding / DDoS | Set `WEB_RATE_LIMIT=10` in `.env` (or deploy behind Cloudflare WAF). | Legitimate dashboard users see 429 errors. |

### Key rotation procedure

1. Generate new key: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update `.env` with new value
3. Restart the affected service (`docker compose restart bot` / `web`)
4. Verify health: `/api/health` returns 200
5. **Do not delete old `.env`** — keep a sealed copy for rollback

### Backup verification

- Database backups run every 6h (cron job or managed DB)
- Verify restore monthly: spin up a clean Postgres, restore dump, check
  `SELECT count(*) FROM users` and `SELECT sum(balance) FROM users` match
  expected totals
- `WALLET_ENC_KEY` backup is separate from DB backups (password manager / vault)
