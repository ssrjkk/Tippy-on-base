"""Per-user wallets: generate, encrypt, restore.

Every user gets a personal Base wallet (address + private key + BIP-39 seed
phrase). The bot keeps the key encrypted at rest so deposits/withdrawals can
be automated (custodial convenience), and the user can export the key and
seed at any time — self-custody on demand. /import lets a user attach their
own existing wallet by seed phrase instead.

SECURITY: WALLET_ENC_KEY MUST be set in .env explicitly.
It must be a 32-byte random key (generate via: python -c "import secrets; print(secrets.token_hex(32))").
DO NOT reuse HOT_WALLET_KEY for this.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from eth_account import Account

# eth_account hides mnemonic support behind an explicit opt-in until its API
# stabilizes; the feature is stable enough for BIP-39 (12/24 words).
Account.enable_unaudited_hdwallet_features()


# A dedicated WALLET_ENC_KEY is REQUIRED. We never fall back to deriving the
# encryption key from HOT_WALLET_KEY: doing so would mean anyone holding the
# hot-wallet key could decrypt every per-user wallet key/seed, defeating the
# point of encrypting them at rest. Missing/short key = fail closed.
_ENC_KEY_RAW = os.environ.get("WALLET_ENC_KEY")
if not _ENC_KEY_RAW or len(_ENC_KEY_RAW) < 32:
    raise ValueError(
        "WALLET_ENC_KEY must be set to a 32-byte random value. It must NOT be "
        "derived from or equal to HOT_WALLET_KEY (generate via: "
        "python -c \"import secrets; print(secrets.token_hex(32))\")."
    )

# Convert to Fernet-compatible key (URL-safe base64, 32 bytes) — same
# derivation as before so existing encrypted wallet data keeps decrypting.
_ENC_KEY_B64 = base64.urlsafe_b64encode(hashlib.sha256(_ENC_KEY_RAW.encode()).digest())
_CIPHER = Fernet(_ENC_KEY_B64)


def encrypt(secret: str) -> str:
    return _CIPHER.encrypt(secret.encode()).decode()


def decrypt(blob: str) -> str:
    try:
        return _CIPHER.decrypt(blob.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt wallet data. Invalid WALLET_ENC_KEY or corrupted data.")


def new_wallet() -> tuple[str, str, str]:
    """Return (address, private_key_hex, seed_phrase)."""
    acct, mnemonic = Account.create_with_mnemonic()
    return acct.address, "0x" + acct.key.hex(), mnemonic


def wallet_from_seed(seed: str) -> tuple[str, str]:
    """Recover (address, private_key_hex) from a BIP-39 seed phrase."""
    seed = seed.strip().lower()
    acct = Account.from_mnemonic(seed)
    return acct.address, "0x" + acct.key.hex()


def is_valid_seed(seed: str) -> bool:
    words = seed.strip().split()
    return len(words) in (12, 24)
