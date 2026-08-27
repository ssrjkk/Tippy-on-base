#!/usr/bin/env python3
"""Re-encrypt all user wallets with a new WALLET_ENC_KEY.

Usage:
    OLD_WALLET_ENC_KEY=<current key> WALLET_ENC_KEY=<new key> \\
        python scripts/reencrypt_wallets.py

Reads every row in user_wallets, decrypts it with the OLD key, re-encrypts it
with the NEW key, then commits. Idempotent: running twice with the same
OLD/NEW pair is a no-op (decrypt+encrypt of already-re-encrypted data with the
same keys reproduces the same ciphertext).

Why two explicit keys: `bot.wallets` builds ONE global Fernet from the env
WALLET_ENC_KEY at import time. When rotating you put the NEW key in
WALLET_ENC_KEY, but the rows are still encrypted with the OLD key — so a naive
decrypt() would fail on every row. This script passes both keys explicitly and
builds its own ciphers (identical derivation to bot/wallets.py).
"""
import base64
import hashlib
import sys

import dotenv

dotenv.load_dotenv()

from bot import config
from bot.ledger import Ledger

OLD_KEY = "OLD_WALLET_ENC_KEY"
NEW_KEY = "WALLET_ENC_KEY"


def _cipher(raw: str):
    from cryptography.fernet import Fernet

    key_b64 = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key_b64)


def main() -> None:
    old_raw = sys.argv[1] if len(sys.argv) > 1 else None
    if not old_raw:
        import os

        old_raw = os.environ.get(OLD_KEY, "").strip()
    new_raw = ""
    import os

    new_raw = os.environ.get(NEW_KEY, "").strip()

    if not old_raw or len(old_raw) < 32:
        print("ERROR: OLD_WALLET_ENC_KEY (or first CLI arg) must be >= 32 chars")
        sys.exit(1)
    if not new_raw or len(new_raw) < 32:
        print("ERROR: WALLET_ENC_KEY must be >= 32 chars")
        sys.exit(1)
    if new_raw == old_raw:
        print("WARNING: new key equals old key; nothing will change.")

    old_cipher = _cipher(old_raw)
    new_cipher = _cipher(new_raw)

    ledger = Ledger(config.DATABASE_URL)
    rows = ledger._conn.execute(
        "SELECT tg_id, key_enc, seed_enc FROM user_wallets"
    ).fetchall()

    if not rows:
        print("No wallets to re-encrypt.")
        ledger.close()
        return

    print(f"Re-encrypting {len(rows)} wallets: OLD key -> NEW key...")
    updated = 0
    for row in rows:
        try:
            old_key = old_cipher.decrypt(row["key_enc"].encode()).decode()
            old_seed = old_cipher.decrypt(row["seed_enc"].encode()).decode()
            new_key_enc = new_cipher.encrypt(old_key.encode()).decode()
            new_seed_enc = new_cipher.encrypt(old_seed.encode()).decode()
            if new_key_enc == row["key_enc"] and new_seed_enc == row["seed_enc"]:
                continue
            ledger._conn.execute(
                "UPDATE user_wallets SET key_enc = %s, seed_enc = %s WHERE tg_id = %s",
                (new_key_enc, new_seed_enc, row["tg_id"]),
            )
            updated += 1
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP tg_id={row['tg_id']}: {e}")

    ledger._conn.commit()
    print(f"Done. {updated}/{len(rows)} wallets re-encrypted.")
    ledger.close()


if __name__ == "__main__":
    main()
