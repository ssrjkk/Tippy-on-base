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
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from eth_account import Account

from . import config

# eth_account hides mnemonic support behind an explicit opt-in until its API
# stabilizes; the feature is stable enough for BIP-39 (12/24 words).
Account.enable_unaudited_hdwallet_features()


# Prefer an explicit WALLET_ENC_KEY. If it is missing or too short we fall
# back to deriving a key from HOT_WALLET_KEY (the pre-refactor behaviour) so
# the bot still starts — but we warn loudly, because a dedicated key gives
# stronger isolation of encrypted user wallets.
_log = logging.getLogger("tipbot")
_ENC_KEY_RAW = os.environ.get("WALLET_ENC_KEY")
if not _ENC_KEY_RAW or len(_ENC_KEY_RAW) < 32:
    _log.warning(
        "WALLET_ENC_KEY missing or too short (<32 bytes); falling back to a key "
        "derived from HOT_WALLET_KEY. Set WALLET_ENC_KEY to a 32-byte random "
        "value (python -c \"import secrets; print(secrets.token_hex(32))\") for "
        "stronger isolation of encrypted user wallets."
    )
    _ENC_KEY_RAW = config.HOT_WALLET_KEY

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
