"""Network reads: chain identity guard, blocks, fees, balances, activity.

Pure read-only view of Base. Every function degrades gracefully (None /
fallback) or fails loudly where silence would be dangerous (chain guard).

Caching strategy (in-process, per-process lifetime):
    chain_id       — immutable, cached forever
    is_contract    — immutable per address, cached forever
    block data     — cached 2s (Base block time ~2s)
    gas_price      — cached 2s (changes per block)
    nonce          — NEVER cached (mempool state, must be fresh)
    eth_balance    — NEVER cached (financial correctness)
"""

import asyncio
import time
from functools import lru_cache

from web3 import Web3

from .. import config
from . import core

# ---------------------------------------------------------------------------
# TTL cache helper
# ---------------------------------------------------------------------------

class _TTLCache:
    """Minimal TTL cache: dict[key] = (timestamp, value)."""

    __slots__ = ("_store", "_ttl")

    def __init__(self, ttl: float):
        self._store: dict = {}
        self._ttl = ttl

    def get(self, key):
        hit = self._store.get(key)
        if hit is not None and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        return None

    def set(self, key, value):
        self._store[key] = (time.monotonic(), value)

    def clear(self):
        self._store.clear()


# ---------------------------------------------------------------------------
# Immutable caches (chain never changes, address code never changes)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _chain_id_once() -> int:
    """Chain id — fetched once per process lifetime."""
    return core.w3.eth.chain_id


_is_contract_cache: dict[str, bool] = {}


def _is_contract_immutable(address: str) -> bool:
    """True when `address` is a smart contract. Cached forever (code is immutable)."""
    addr = Web3.to_checksum_address(address)
    cached = _is_contract_cache.get(addr)
    if cached is not None:
        return cached
    try:
        code = core.w3.eth.get_code(addr)
        result = bool(code and code != b"")
    except Exception:
        result = False
    _is_contract_cache[addr] = result
    return result


# ---------------------------------------------------------------------------
# Short-lived caches (block-level data, ~2s TTL)
# ---------------------------------------------------------------------------

_block_cache = _TTLCache(ttl=2.0)
_gas_price_cache = _TTLCache(ttl=2.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def eth_balance_sync(address: str) -> float:
    """ETH balance of any address on Base (in ETH units)."""
    try:
        wei = core.w3.eth.get_balance(Web3.to_checksum_address(address))
    except Exception:
        wei = core._rpc_call(lambda c, addr=address: core.w3.eth.get_balance(Web3.to_checksum_address(addr)))
    return wei / 10**18


async def eth_balance(address: str) -> float:
    """Async: ETH balance off the event loop."""
    return await asyncio.to_thread(eth_balance_sync, address)


def get_block_number_sync() -> int:
    """Current chain head block number."""
    return core.w3.eth.block_number


async def get_block_number() -> int:
    """Async: current block number off the event loop."""
    return await asyncio.to_thread(get_block_number_sync)


def get_gas_price_sync() -> int:
    """Current gas price in wei. Cached 2s."""
    _sync_provider_caches()
    hit = _gas_price_cache.get("gp")
    if hit is not None:
        return hit
    val = core.w3.eth.gas_price
    _gas_price_cache.set("gp", val)
    return val


async def get_gas_price() -> int:
    """Async: current gas price off the event loop."""
    return await asyncio.to_thread(get_gas_price_sync)


def estimate_gas_sync(to_address: str, value_wei: int = 0, data: bytes = b"") -> int:
    """Estimate gas for a simple ETH transfer or contract call."""
    try:
        return core.w3.eth.estimate_gas({
            "from": core.HOT_WALLET,
            "to": Web3.to_checksum_address(to_address),
            "value": value_wei,
            "data": data,
        })
    except Exception:
        return 21000  # fallback for simple transfer


async def estimate_gas(to_address: str, value_wei: int = 0, data: bytes = b"") -> int:
    """Async: estimate gas off the event loop."""
    return await asyncio.to_thread(estimate_gas_sync, to_address, value_wei, data)


# ---------------------------------------------------------------------------
# Chain identity & safety
# ---------------------------------------------------------------------------

def chain_id_sync() -> int:
    """Chain id of the connected network (8453 = Base mainnet). Cached forever."""
    _sync_provider_caches()
    return _chain_id_once()


async def chain_id() -> int:
    """Async: chain id off the event loop."""
    return await asyncio.to_thread(chain_id_sync)


def assert_base_chain_sync() -> int:
    """Verify the RPC is really the expected Base chain; raise otherwise."""
    cid = chain_id_sync()
    if config.EXPECTED_CHAIN_ID and cid != config.EXPECTED_CHAIN_ID:
        raise RuntimeError(
            f"RPC is on chain {cid}, expected {config.EXPECTED_CHAIN_ID} "
            "(EXPECTED_CHAIN_ID) — refusing to sign"
        )
    return cid


def nonce_sync(address: str) -> int:
    """Next nonce for `address` counting pending txs (mempool included).

    NEVER cached — nonce must reflect the latest mempool state to avoid
    'replacement transaction underpriced' errors on concurrent sends.
    """
    return core.w3.eth.get_transaction_count(Web3.to_checksum_address(address), "pending")


async def nonce(address: str) -> int:
    """Async: pending nonce off the event loop."""
    return await asyncio.to_thread(nonce_sync, address)


def is_contract_sync(address: str) -> bool:
    """True when `address` is a smart contract (False = EOA or empty).

    Cached forever — contract code is immutable on Base.
    """
    _sync_provider_caches()
    return _is_contract_immutable(address)


async def is_contract(address: str) -> bool:
    """Async: contract check off the event loop."""
    return await asyncio.to_thread(is_contract_sync, address)


# ---------------------------------------------------------------------------
# Blocks & fees (cached 2s)
# ---------------------------------------------------------------------------

def get_block_sync(block_identifier: int | str = "latest") -> dict | None:
    """Block info: number, hash, timestamp (unix sec), tx count. None if absent.

    Cached 2s for non-'latest' identifiers; 'latest' always uses cache.
    """
    _sync_provider_caches()
    if block_identifier == "latest":
        hit = _block_cache.get("latest")
        if hit is not None:
            return hit
    try:
        b = core.w3.eth.get_block(block_identifier)
    except Exception:
        return None
    result = {
        "number": int(b["number"]),
        "hash": "0x" + bytes(b["hash"]).hex(),
        "timestamp": int(b["timestamp"]),
        "transactions": len(b["transactions"]),
        "base_fee_gwei": float(b["baseFeePerGas"]) / 1e9 if b.get("baseFeePerGas") else None,
    }
    if block_identifier == "latest":
        _block_cache.set("latest", result)
    return result


async def get_block(block_identifier: int | str = "latest") -> dict | None:
    """Async: block info off the event loop."""
    return await asyncio.to_thread(get_block_sync, block_identifier)


def eip1559_fees_sync(priority_gwei: float = 0.01) -> dict:
    """Current EIP-1559 fee picture on Base, in gwei.

    Reuses cached block data (2s TTL) so two rapid calls in the same
    handler (e.g. gas + portfolio) only hit the RPC once.
    """
    blk = get_block_sync("latest")
    if not blk or blk.get("base_fee_gwei") is None:
        return {"base_fee_gwei": 0, "priority_gwei": priority_gwei, "max_fee_gwei": priority_gwei}
    base_fee = blk["base_fee_gwei"]
    return {
        "base_fee_gwei": base_fee,
        "priority_gwei": priority_gwei,
        "max_fee_gwei": (base_fee * 2 + priority_gwei),
    }


async def eip1559_fees(priority_gwei: float = 0.01) -> dict:
    """Async: EIP-1559 fees off the event loop."""
    return await asyncio.to_thread(eip1559_fees_sync, priority_gwei)


def _sync_provider_caches() -> None:
    """Drop caches when the bound provider changed (e.g. tests rebinding w3).

    Without this, values read against one provider (a test stub) would leak
    into the next test that binds a different provider to the same addresses.
    """
    if core.sync_caches_with_provider():
        _block_cache.clear()
        _gas_price_cache.clear()
        _is_contract_cache.clear()
        _chain_id_once.cache_clear()


def clear_network_caches() -> None:
    """Drop all in-process caches (tests / diagnostics)."""
    _block_cache.clear()
    _gas_price_cache.clear()
    _is_contract_cache.clear()
    _chain_id_once.cache_clear()
