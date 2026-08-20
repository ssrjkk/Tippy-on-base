"""Per-user wallets: generate, encrypt, restore.

Every user gets a personal Base wallet (address + private key + BIP-39 seed
phrase). The bot keeps the key encrypted at rest so deposits/withdrawals can
be automated (custodial convenience), and the user can export the key and
seed at any time — self-custody on demand. /import lets a user attach their
own existing wallet by seed phrase instead.
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from eth_account import Account

from . import config

# eth_account hides mnemonic support behind an explicit opt-in until its API
# stabilizes; the feature is stable enough for BIP-39 (12/24 words).
Account.enable_unaudited_hdwallet_features()


def _enc_key() -> bytes:
    """Deterministic Fernet key: explicit WALLET_ENC_KEY if set, otherwise a
    stable derivation from HOT_WALLET_KEY (no extra secret to lose)."""
    raw = os_environ_wallet_key()
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


def os_environ_wallet_key() -> str:
    import os

    return os.environ.get("WALLET_ENC_KEY") or config.HOT_WALLET_KEY


def encrypt(secret: str) -> str:
    return Fernet(_enc_key()).encrypt(secret.encode()).decode()


def decrypt(blob: str) -> str:
    return Fernet(_enc_key()).decrypt(blob.encode()).decode()


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
