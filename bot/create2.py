"""CREATE2 per-user deposit addresses.

Each user gets a unique USDC deposit address derived from their tg_id.
Deposits are swept to the hot wallet via a shared EIP-1167 forwarding proxy
deployed on demand by the CREATE2 factory.

Flow:
  1. Operator deploys USDCForwarder + Create2Factory (scripts/deploy_create2_factory.py)
     and sets CREATE2_FACTORY_ADDRESS / CREATE2_FACTORY_FORWARDER /
     CREATE2_PROXY_BYTECODE / CREATE2_SAFE_DEPOSITS=1 in .env.
  2. ``get_deposit_address(tg_id)`` deterministically computes an address.
  3. ``ensure_proxy_deployed(tg_id)`` creates the proxy on-chain via the
     factory (idempotent — the CREATE2 address is the same every time).
  4. When funds arrive the bot calls ``forward()`` on the proxy (or any user
     can), which moves balance to the hot wallet.

SECURITY: CREATE2 deposits are DISABLED by default and only become active
when ALL of the following are true:
  1. CREATE2_FACTORY_ADDRESS is set (a real factory on the network),
  2. CREATE2_FACTORY_FORWARDER is set (the shared forwarder implementation),
  3. CREATE2_PROXY_BYTECODE is the COMPLETE EIP-1167 init code the factory
     deploys (45+ bytes: PREFIX + 20-byte forwarder + SUFFIX),
  4. CREATE2_SAFE_DEPOSITS=1 is explicitly set by the operator.

Until then, ``get_deposit_address`` returns ``""`` and the deposit flow
falls back to the standard hot-wallet address — so users can never send USDC
to an address with no runtime code (which would burn the funds).
"""
import logging
import os

from . import config

logger = logging.getLogger(__name__)

# Deployed CREATE2 factory (real, on Base). Empty = disabled.
FACTORY_ADDRESS: str | None = os.environ.get("CREATE2_FACTORY_ADDRESS") or None

# Shared EIP-1167 forwarder the factory's proxies delegatecall. Empty = disabled.
FORWARDER_ADDRESS: str | None = os.environ.get("CREATE2_FACTORY_FORWARDER") or None

# Full EIP-1167 proxy creation bytecode: PREFIX + <20-byte forwarder> + SUFFIX.
# Set by the deploy script to the EXACT init code the factory deploys, so the
# offline CREATE2 address matches the on-chain proxy.
MINIMAL_PROXY_BYTECODE: str = (os.environ.get("CREATE2_PROXY_BYTECODE", "") or "").lower()

# Operator opt-in. CREATE2 deposits stay DISABLED until an operator has
# explicitly deployed and tested a factory + forwarding proxy and sets this.
CREATE2_SAFE_DEPOSITS = os.environ.get("CREATE2_SAFE_DEPOSITS") == "1"

# A complete EIP-1167 minimal proxy is at least 45 bytes long.
_EIP1167_MIN_LEN = 45


def _bytecode_complete() -> bool:
    try:
        if not MINIMAL_PROXY_BYTECODE.startswith("0x"):
            return False
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
    if not FORWARDER_ADDRESS:
        logger.warning("CREATE2_FACTORY_FORWARDER not set; CREATE2 deposits remain DISABLED.")
        return False
    if not CREATE2_SAFE_DEPOSITS:
        logger.warning(
            "CREATE2_FACTORY_ADDRESS is set but CREATE2_SAFE_DEPOSITS is not "
            "'1'; CREATE2 deposits remain DISABLED."
        )
        return False
    if not _bytecode_complete():
        logger.warning(
            "CREATE2_PROXY_BYTECODE is incomplete (not a full EIP-1167 proxy); "
            "CREATE2 deposits are DISABLED to avoid burning USDC sent to an "
            "undeployable address."
        )
        return False
    return True


def _salt_from_tg(tg_id: int) -> bytes:
    """Salt used by both the bot and the factory: keccak(decimal(tg_id))."""
    from web3 import Web3

    return Web3.keccak(text=str(tg_id))


def _usdc_balance_of(address: str) -> int:
    """USDC balance of a proxy (reads on-chain, no gas, with RPC failover)."""
    from bot.chain import core

    abi = [{
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }]
    return int(core._contract_read(core.USDC, abi, "balanceOf", core.Web3.to_checksum_address(address)))


def _compute_address(tg_id: int) -> str:
    """Compute CREATE2 address for a user.

    Address = keccak256(0xff + factory + keccak256(salt) + keccak256(bytecode))[12:]
    Salt = keccak256(decimal_string(tg_id))
    """
    from web3 import Web3

    if not FACTORY_ADDRESS:
        return ""

    salt = _salt_from_tg(tg_id)
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


def _build_and_send(build_fn) -> str:
    """Shared signing path for CREATE2 admin txs (proxy deploy / forward sweep).

    Mirrors bot.chain.transfers._build_and_send: chain-id guard first, then a
    shared send lock + pending-nonce read so concurrent sends can't race.
    Returns the tx hash.
    """
    from bot.chain import core, network
    from bot.chain.transfers import _send_lock

    network.assert_base_chain_sync()
    acct = core.w3.eth.account.from_key(config.HOT_WALLET_KEY)
    with _send_lock:
        n = core.w3.eth.get_transaction_count(core.HOT_WALLET, "pending")
        base_fee = int(core.w3.eth.get_block("latest")["baseFeePerGas"])
        priority = core.w3.to_wei("0.01", "gwei")
        max_fee = base_fee * 2 + priority
        tx = build_fn(n, max_fee, priority)
        signed = acct.sign_transaction(tx)
        return "0x" + core.w3.eth.send_raw_transaction(signed.raw_transaction).hex()


def _deploy_proxy_sync(tg_id: int) -> str:
    from bot.chain import core, network
    from bot.chain.transfers import _send_lock  # noqa: F401 (reuse same lock)

    factory_addr = core.Web3.to_checksum_address(FACTORY_ADDRESS)
    factory = core.w3.eth.contract(
        address=factory_addr,
        abi=[{
            "inputs": [{"internalType": "uint256", "name": "tgId", "type": "uint256"}],
            "name": "deploy",
            "outputs": [{"internalType": "address", "name": "proxy", "type": "address"}],
            "stateMutability": "nonpayable",
            "type": "function",
        }],
    )
    core.w3.eth.account.from_key(config.HOT_WALLET_KEY)  # ensure valid key early

    def build(nonce_: int, max_fee: int, priority: int) -> dict:
        return factory.functions.deploy(tg_id).build_transaction({
            "from": core.HOT_WALLET,
            "nonce": nonce_,
            "maxPriorityFeePerGas": priority,
            "maxFeePerGas": max_fee,
            "chainId": core.w3.eth.chain_id,
        })

    return _build_and_send(build)


def _sweep_proxy_sync(tg_id: int) -> str | None:
    from bot.chain import core

    addr = _compute_address(tg_id)
    if core.w3.eth.get_code(addr) in (b"", b"\x00"):
        return None

    proxy = core.w3.eth.contract(
        address=addr,
        abi=[{
            "inputs": [],
            "name": "forward",
            "outputs": [
                {"internalType": "uint256", "name": "usdcForwarded", "type": "uint256"},
                {"internalType": "uint256", "name": "ethForwarded", "type": "uint256"},
            ],
            "stateMutability": "nonpayable",
            "type": "function",
        }],
    )

    def build(nonce_: int, max_fee: int, priority: int) -> dict:
        return proxy.functions.forward().build_transaction({
            "from": core.HOT_WALLET,
            "nonce": nonce_,
            "maxPriorityFeePerGas": priority,
            "maxFeePerGas": max_fee,
            "chainId": core.w3.eth.chain_id,
        })

    return _build_and_send(build)


async def sweep_proxy(tg_id: int) -> str | None:
    """Call ``forward()`` on a user's proxy, moving USDC+ETH to hot wallet.

    Returns the tx hash, or None if CREATE2 is disabled / proxy has no code.
    Amounts are reconciled by the deposit scanner on the hot-wallet side.
    """
    if not is_create2_enabled():
        return None
    import asyncio
    return await asyncio.to_thread(_sweep_proxy_sync, tg_id)


def ensure_proxy_deployed(tg_id: int) -> str | None:
    """Create (idempotently) the deposit proxy for a tg_id on-chain.

    Returns the proxy address, or None if CREATE2 is disabled. Only triggers
    a transaction when the proxy has no runtime code yet — the CREATE2 address
    is deterministic, so a second call is just a free ``get_code``.
    """
    if not is_create2_enabled():
        return None
    from bot.chain import core

    addr = _compute_address(tg_id)
    if core.w3.eth.get_code(addr) not in (b"", b"\x00"):
        return addr  # already deployed

    _deploy_proxy_sync(tg_id)
    return addr


async def deploy_proxy(tg_id: int) -> str | None:
    """Async: create the deposit proxy off the event loop (non-blocking)."""
    if not is_create2_enabled():
        return None
    import asyncio
    return await asyncio.to_thread(ensure_proxy_deployed, tg_id)


def _sweep_all_proxies_sync() -> list[int]:
    """Call ``forward()`` on every registered proxy that holds USDC.

    Returns the list of tg_ids whose proxy was swept. Funds move to the hot
    wallet, where the deposit scanner credits the owner via tg_id_of_proxy.
    """
    from bot.ledger import ledger

    if not is_create2_enabled():
        return []
    swept: list[int] = []
    for row in ledger.list_create2_proxies():
        tg_id = int(row["tg_id"])
        try:
            addr = _compute_address(tg_id)
            bal = _usdc_balance_of(addr)
            if bal <= 0:
                continue
            if _sweep_proxy_sync(tg_id) is not None:
                swept.append(tg_id)
        except Exception:
            logger.warning("create2 sweep failed for tg_id=%s", tg_id, exc_info=True)
    return swept


async def sweep_all_proxies() -> list[int]:
    """Async: sweep all registered proxies off the event loop."""
    if not is_create2_enabled():
        return []
    import asyncio
    return await asyncio.to_thread(_sweep_all_proxies_sync)