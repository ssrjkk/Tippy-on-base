# Key Rotation & Compromise Runbook — Tippy

## 1. Key Types

| Key | Purpose | Stored in | Rotation frequency |
|---|---|---|---|
| `HOT_WALLET_KEY` | Signs USDC transfers (withdrawals) | `.env` → env var | Every 90 days or on compromise |
| `ORACLE_PRIVATE_KEY` | Signs oracle market resolutions | `.env` → env var | Every 90 days or on compromise |
| Vault owner key | Controls TipBotVault contract | Gnosis Safe multisig | Per Safe policy |
| Vault relayer key | Limited daily withdrawal from vault | `.env` → env var | Every 90 days or on compromise |

## 2. Key Rotation Procedure

### 2.1 Generate New Key

```bash
# Generate new private key (NEVER use online tools)
python3 -c "
from eth_account import Account
acct = Account.create()
print('Address:', acct.address)
print('Private key:', acct.key.hex())
"
```

### 2.2 Rotate HOT_WALLET_KEY

**Pre-rotation (15 min before):**
1. Check no pending withdrawals: `python -c "from bot.ledger import ledger; print(ledger.pending_withdraws())"`
2. Pause the bot: `kill` or `docker stop tipbot-bot`
3. Final solvency snapshot: `curl http://localhost:8000/api/solvency`

**Rotation steps:**
1. Generate new key (section 2.1)
2. Fund new hot wallet with gas: send ~0.001 ETH from old hot wallet
3. Update `.env`: `HOT_WALLET_KEY=0xNEW_KEY_HERE`
4. Update `WALLET_ENC_KEY` if encryption key also rotates
5. Restart bot: `python launch.py`
6. Verify: `curl http://localhost:8000/api/solvency` — balances unchanged
7. Test: `/deposit` → `/withdraw 0.01 0x...` → confirm on Base

**Post-rotation:**
1. Send remaining USDC from old hot wallet to new hot wallet on-chain
2. Verify old hot wallet balance = 0
3. Archive old key (encrypted, offline)
4. Update this runbook's log

### 2.3 Rotate ORACLE_PRIVATE_KEY

1. Generate new key
2. Update `.env`: `ORACLE_PRIVATE_KEY=0xNEW_KEY_HERE`
3. Update contract oracle address: call `setOracle(NEW_ADDRESS)` on OutcomeMarket
4. Verify: `oracleResolve` works from new address

### 2.4 Rotate Vault Relayer Key

1. Generate new key
2. Update `.env` with new relayer key
3. Transfer ownership on TipBotVault: call `transferOwnership(newRelayer)` (requires 24h timelock)
4. After timelock: `acceptOwnership()` from new relayer

## 3. Compromise Response

### 3.1 Suspected Compromise (Private Key Exposed)

**Immediate (< 5 min):**
1. **STOP THE BOT** — `kill` / `docker stop tipbot-bot`
2. Drain hot wallet USDC to cold storage:
   ```bash
   # Use old key to send all USDC to cold wallet
   python3 -c "
   from web3 import Web3
   w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org'))
   # ... send all USDC to cold wallet
   "
   ```
3. Check on-chain balance: `https://basescan.org/address/HOT_WALLET_ADDRESS`
4. If vault exists: revoke relayer permissions immediately

**Recovery (< 1 hour):**
1. Generate new key (section 2.1)
2. Fund new hot wallet with gas
3. Update `.env` with new key
4. Verify solvency: `curl http://localhost:8000/api/solvency`
5. Restart bot
6. Test full flow: deposit → tip → withdraw

**Post-incident (< 24 hours):**
1. Review `suspicious_activity` table for unauthorized withdrawals
2. Review `tx_log` for any unauthorized transactions
3. Check if any user funds were stolen
4. If funds stolen: report to authorities, notify affected users
5. Update runbook with lessons learned
6. Consider external audit of any code changes

### 3.2 Confirmed Compromise (Funds Stolen)

**Immediate:**
1. Stop bot, drain all remaining funds to cold storage
2. Snapshot all balances: `curl http://localhost:8000/api/solvency`
3. Export full `tx_log` for forensic analysis

**Recovery:**
1. Follow section 3.1 recovery steps
2. If on-chain contract compromised: upgrade or deploy new contract
3. Notify all users via Telegram broadcast
4. File police report if applicable

## 4. Emergency Contacts

| Role | Contact | When to call |
|---|---|---|
| Bot operator | ssrjkk | Any key compromise |
| Base support | via Discord | On-chain issues |
| Law enforcement | local authorities | Confirmed theft |

## 5. Audit Log

After any key rotation or compromise response, append to this section:

```
## YYYY-MM-DD — [rotation|compromise]
- Key rotated: HOT_WALLET_KEY
- Reason: scheduled / suspected / confirmed
- Funds at risk: $X
- User impact: none / minimal / major
- Follow-up: [actions taken]
```
