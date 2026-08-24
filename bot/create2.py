"""CREATE2 per-user deposit addresses.

Each user gets a unique USDC deposit address derived from their tg_id.
Deposits auto-forward to the hot wallet via the CREATE2 factory contract.

This simplifies onboarding: no /link + signature needed. Users just
send USDC to their unique address.

SECURITY: CREATE2 deposits are DISABLED by default and only become active
when ALL of the following are true:
  1. CREATE2_FACTORY_ADDRESS is set (a real factory is deployed on Base),
  2. MINIMAL_PROXY_BYTECODE is a COMPLETE, deployable EIP-1167 proxy
     (prefix + 20-byte implementation + suffix),
  3. CREATE2_SAFE_DEPOSITS=1 is explicitly set by the operator.

Until then, ``get_deposit_address`` returns ``""`` and the deposit flow
falls back to the standard hot-wallet address. This prevents users from
sending USDC to an address with no runtime code (which would burn the
funds) when the proxy bytecode is incomplete or the factory was never
deployed.
"""
import logging
import os

logger = logging.getLogger(__name__)

# CREATE2 factory on Base. Must be a real deployed factory before enabling.
FACTORY_ADDRESS: str | None = os.environ.get("CREATE2_FACTORY_ADDRESS")

# Operator opt-in. CREATE2 deposits stay DISABLED until an operator has
# explicitly deployed and tested a factory + forwarding proxy and sets this.
CREATE2_SAFE_DEPOSITS = os.environ.get("CREATE2_SAFE_DEPOSITS") == "1"

# EIP-1167 minimal proxy bytecode. A complete proxy is:
#   PREFIX (20 bytes) + <20-byte implementation address> + SUFFIX (15 bytes)
#   = 45 bytes of init code.
# The shipped value is ONLY the 20-byte prefix and is intentionally NOT
# a deployable contract. Do not use it to compute real deposit addresses.
MINIMAL_PROXY_BYTECODE = "0x3d602d80600a3d3981f3363d3d373d3d3d363d73"  # INCOMPLETE prefix — DO NOT USE

# A complete EIP-1167 minimal proxy is at least 45 bytes long.
_EIP1167_MIN_LEN = 45


def _bytecode_complete() -> bool:
    try:
        return len(bytes.fromhex(MINIMAL_PROXY_BYTECODE[2:])) >= _EIP1167_MIN_LEN
    except ValueError:
        return False


def is_create2_enabled() -> bool:
    """Whether CREATE2 per-user deposit addresses are active.

    Enabled only when a factory is configured, the proxy bytecode is a
    complete (deployable) EIP-1167 contract, AND the operator has opted in
    via ``CREATE2_SAFE_DEPOSITS=1``. Otherwise deposits fall back to the
    standard hot-wallet address.
    """
    if not FACTORY_ADDRESS:
        return False
    if not CREATE2_SAFE_DEPOSITS:
        logger.warning(
            "CREATE2_FACTORY_ADDRESS is set but CREATE2_SAFE_DEPOSITS is not "
            "'1'; CREATE2 deposits remain DISABLED."
        )
        return False
    if not _bytecode_complete():
        logger.warning(
            "MINIMAL_PROXY_BYTECODE is incomplete (not a full EIP-1167 proxy); "
            "CREATE2 deposits are DISABLED to avoid burning USDC sent to an "
            "undeployable address."
        )
        return False
    return True


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

    Returns empty string if CREATE2 is not configured or not safe to use,
    so callers fall back to the standard hot-wallet deposit address.
    """
    if not is_create2_enabled():
        return ""
    return _compute_address(tg_id)
