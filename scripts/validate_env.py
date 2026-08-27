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
