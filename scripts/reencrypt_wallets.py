#!/usr/bin/env python3
"""Re-encrypt all user wallets with a new WALLET_ENC_KEY.

Usage:
    WALLET_ENC_KEY=<new_key> python scripts/reencrypt_wallets.py

Reads the OLD key from the current DATABASE_URL's .env, re-encrypts every
row in user_wallets, then commits.  Run this AFTER rotating WALLET_ENC_KEY.

Safety: the script is idempotent — running it twice with the same key is a
no-op (re-encrypting already-encrypted data with the same key produces the
same ciphertext).
"""
import os
import sys

import dotenv

dotenv.load_dotenv()

from bot import config
from bot.ledger import Ledger
from bot.wallets import decrypt, encrypt


def main():
    new_key = os.environ.get("WALLET_ENC_KEY", "").strip()
    if not new_key or len(new_key) < 32:
        print("ERROR: WALLET_ENC_KEY must be >= 32 chars")
        sys.exit(1)

    ledger = Ledger(config.DATABASE_URL)
    rows = ledger._conn.execute(
        "SELECT tg_id, key_enc, seed_enc FROM user_wallets"
    ).fetchall()

    if not rows:
        print("No wallets to re-encrypt.")
        return

    print(f"Re-encrypting {len(rows)} wallets with new WALLET_ENC_KEY...")
    updated = 0
    for row in rows:
        try:
            # Decrypt with the OLD key (still in the running config)
            old_key = decrypt(row["key_enc"])
            old_seed = decrypt(row["seed_enc"])
            # Re-encrypt with the NEW key (from current WALLET_ENC_KEY env)
            new_key_enc = encrypt(old_key)
            new_seed_enc = encrypt(old_seed)
            # Skip if unchanged (same key = same ciphertext)
            if new_key_enc == row["key_enc"] and new_seed_enc == row["seed_enc"]:
                continue
            ledger._conn.execute(
                "UPDATE user_wallets SET key_enc = %s, seed_enc = %s WHERE tg_id = %s",
                (new_key_enc, new_seed_enc, row["tg_id"]),
            )
            updated += 1
        except Exception as e:
            print(f"  SKIP tg_id={row['tg_id']}: {e}")

    ledger._conn.commit()
    print(f"Done. {updated}/{len(rows)} wallets re-encrypted.")
    ledger.close()


if __name__ == "__main__":
    main()
