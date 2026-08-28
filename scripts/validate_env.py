#!/usr/bin/env python3
"""Validate .env configuration before first deploy or after changes.

Checks all required secrets, format constraints, and common mistakes.
Run: python scripts/validate_env.py
"""
import os
import re
import sys

import dotenv

dotenv.load_dotenv()

ERRORS = []
WARNINGS = []


def require(name: str, pattern: str | None = None, min_len: int = 1, msg: str = ""):
    val = os.environ.get(name, "").strip()
    if not val:
        ERRORS.append(f"MISSING: {name} is not set")
        return val
    if len(val) < min_len:
        ERRORS.append(f"SHORT: {name} must be >= {min_len} chars, got {len(val)}")
    if pattern and not re.fullmatch(pattern, val):
        ERRORS.append(f"INVALID: {name} {msg or 'does not match expected format'}")
    return val


def optional(name: str, pattern: str | None = None, min_len: int = 0, msg: str = ""):
    val = os.environ.get(name, "").strip()
    if val and pattern and not re.fullmatch(pattern, val):
        WARNINGS.append(f"INVALID: {name} {msg or 'does not match expected format'}")
    if val and min_len and len(val) < min_len:
        WARNINGS.append(f"SHORT: {name} is {len(val)} chars, recommend >= {min_len}")
    return val


print("=== Tippy .env validation ===\n")

# --- Required secrets ---
require("BOT_TOKEN", r"\d+:[A-Za-z0-9_-]{35}", msg="must be numeric:alphanumeric")
require("HOT_WALLET_KEY", r"0x[0-9a-fA-F]{64}", msg="must be 0x + 64 hex chars")
enc = require("WALLET_ENC_KEY", min_len=32, msg="must be >= 32 chars (Fernet key)")
if enc and enc == os.environ.get("HOT_WALLET_KEY", "").strip():
    ERRORS.append("SECURITY: WALLET_ENC_KEY must NOT equal HOT_WALLET_KEY")

require("DATABASE_URL", msg="PostgreSQL connection string")
require("ADMIN_TG_ID", r"\d+", msg="must be a numeric Telegram user ID")

# --- RPC ---
require("BASE_RPC_URL", msg="Base mainnet RPC endpoint")

# --- Web (SECRET_KEY is required: independent of BOT_TOKEN for session security) ---
require("SECRET_KEY", min_len=32, msg="must be >= 32 chars, independent of BOT_TOKEN")
optional("WEBHOOK_URL", r"https://.*", msg="should be an https URL")
optional("WEBHOOK_SECRET", min_len=16, msg="should be >= 16 chars if set")

# --- Cross-checks ---
if enc == os.environ.get("SECRET_KEY", "").strip():
    ERRORS.append("SECURITY: WALLET_ENC_KEY must NOT equal SECRET_KEY")

vault = os.environ.get("VAULT_ADDRESS", "").strip()
if vault and not re.fullmatch(r"0x[0-9a-fA-F]{40}", vault):
    ERRORS.append(f"VAULT_ADDRESS {vault!r} is not a valid Ethereum address")

# --- On-chain markets (OutcomeMarket) ---
market = os.environ.get("OUTCOME_MARKET_ADDRESS", "").strip()
if market:
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", market):
        ERRORS.append(f"OUTCOME_MARKET_ADDRESS {market!r} is not a valid Ethereum address")
    oracle_addr = os.environ.get("ORACLE_ADDRESS", "").strip()
    oracle_key = os.environ.get("ORACLE_PRIVATE_KEY", "").strip()
    if not oracle_addr and not oracle_key:
        WARNINGS.append(
            "ON-CHAIN: OUTCOME_MARKET_ADDRESS is set but neither ORACLE_ADDRESS nor "
            "ORACLE_PRIVATE_KEY is configured — resolutions will fall back to the hot "
            "wallet (ownerResolve). Deploy with a dedicated oracle key for production."
        )
    if oracle_key and not re.fullmatch(r"0x[0-9a-fA-F]{64}", oracle_key):
        ERRORS.append("ORACLE_PRIVATE_KEY must be 0x + 64 hex chars")
    if oracle_addr and not re.fullmatch(r"0x[0-9a-fA-F]{40}", oracle_addr):
        ERRORS.append("ORACLE_ADDRESS is not a valid Ethereum address")
    if oracle_key and oracle_addr:
        WARNINGS.append(
            "ON-CHAIN: both ORACLE_ADDRESS and ORACLE_PRIVATE_KEY are set — the attester "
            "address is derived from the key; make sure they match the contract's oracle."
        )

# --- x402 agent payments ---
x402_enabled = os.environ.get("X402_ENABLED", "").strip() == "1"
x402_recv = os.environ.get("X402_RECEIVE_ADDRESS", "").strip()
if x402_enabled:
    if not x402_recv:
        ERRORS.append("X402_ENABLED=1 but X402_RECEIVE_ADDRESS is not set — x402 stays disabled")
    elif re.fullmatch(r"0x[0-9a-fA-F]{40}", x402_recv):
        hot = os.environ.get("HOT_WALLET_KEY", "").strip()
        if hot:
            # derive the hot wallet address lazily (eth_account is a hard dep)
            try:
                from eth_account import Account

                hot_addr = Account.from_key(hot).address
                if x402_recv.lower() == hot_addr.lower():
                    ERRORS.append(
                        "SECURITY: X402_RECEIVE_ADDRESS equals the deposit hot wallet — "
                        "any deposit tx could be replayed as an x402 payment. Use a "
                        "dedicated address."
                    )
            except Exception:
                pass  # eth_account unavailable — runtime guard in web/x402.py still applies
else:
    if x402_recv:
        WARNINGS.append("X402_RECEIVE_ADDRESS is set but X402_ENABLED != 1 — x402 payments stay disabled")

# --- EAS attestations (agent) ---
optional("EAS_SCHEMA_UID", r"0x[0-9a-fA-F]{64}", msg="must be 0x + 64 hex (registered EAS schema UID)")

# --- Gas drip budget ---
gd = os.environ.get("GAS_DRIP_DAILY_MAX", "").strip()
if gd and not gd.isdigit():
    ERRORS.append("GAS_DRIP_DAILY_MAX must be a non-negative integer (drips per UTC day)")

# --- Report ---
if ERRORS:
    print(f"\n{'='*50}")
    print(f"  {len(ERRORS)} ERROR(S) — fix before deploying:")
    print(f"{'='*50}")
    for e in ERRORS:
        print(f"  [ERROR]   {e}")

if WARNINGS:
    print(f"\n{'='*50}")
    print(f"  {len(WARNINGS)} WARNING(S) — review recommended:")
    print(f"{'='*50}")
    for w in WARNINGS:
        print(f"  [WARN]    {w}")

if not ERRORS and not WARNINGS:
    print("\n  All checks passed.")

print()
sys.exit(1 if ERRORS else 0)
