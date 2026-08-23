"""CREATE2 per-user deposit addresses.

Each user gets a unique USDC deposit address derived from their tg_id.
Deposits auto-forward to the hot wallet via the CREATE2 factory contract.

This simplifies onboarding: no /link + signature needed. Users just
send USDC to their unique address.

The factory contract must be deployed on Base before use.
Set CREATE2_FACTORY_ADDRESS in .env to enable.
"""
import hashlib
import os

# CREATE2 factory on Base (deploy this contract first)
FACTORY_ADDRESS: str | None = os.environ.get("CREATE2_FACTORY_ADDRESS")

# Minimal proxy bytecode (EIP-1167) pointing to the hot wallet
# This creates a contract that forwards all USDC to the hot wallet
MINIMAL_PROXY_BYTECODE = "0x3d602d80600a3d3981f3363d3d373d3d3d363d73"  # prefix


def _compute_address(tg_id: int) -> str:
    """Compute CREATE2 address for a user.

    Address = keccak256(0xff + factory + keccak256(salt) + keccak256(bytecode))[12:]
    Salt = keccak256(tg_id)
    """
    from web3 import Web3

    if not FACTORY_ADDRESS:
        return ""

    salt = Web3.keccak(text=str(tg_id))
    bytecode_hash = Web3.keccak(hexstr=MINIMAL_PROXY_BYTECODE)

    addr_bytes = Web3.keccak(
        b"\xff"
        + bytes.fromhex(FACTORY_ADDRESS[2:])
        + salt
        + bytecode_hash
    )[12:]

    return Web3.to_checksum_address(addr_bytes.hex())


def get_deposit_address(tg_id: int) -> str:
    """Get the unique deposit address for a user.

    Returns empty string if CREATE2 is not configured.
    """
    if not FACTORY_ADDRESS:
        return ""
    return _compute_address(tg_id)


def is_create2_enabled() -> bool:
    """Check if CREATE2 deposits are enabled."""
    return bool(FACTORY_ADDRESS)
