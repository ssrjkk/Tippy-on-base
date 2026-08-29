"""Basenames (Base native name service): on-chain identity resolution.

Forward (`name.base.eth` -> address), reverse (address -> primary name via
the official ENSIP-19 ReverseRegistrar) and availability checks against the
Basenames Registry — all straight from Base, no indexer involved.
"""

import asyncio
import re

from web3 import Web3

from .. import config
from . import core

_RESOLVER_ABI = [
    {"inputs": [{"name": "node", "type": "bytes32"}], "name": "addr",
     "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "node", "type": "bytes32"}], "name": "name",
     "outputs": [{"name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
]

_REVERSE_REGISTRAR_ABI = [
    {"inputs": [{"name": "addr", "type": "address"}], "name": "node",
     "outputs": [{"name": "", "type": "bytes32"}], "stateMutability": "view", "type": "function"},
]

_REGISTRY_ABI = [
    {"inputs": [{"name": "node", "type": "bytes32"}], "name": "owner",
     "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]

_BASENAME_RE = re.compile(r"^[a-zA-Z0-9-]{1,62}(\.[a-zA-Z0-9-]{1,62})*\.base\.eth$")


def is_basename(name: str) -> bool:
    """True when `name` looks like a valid basename (`something.base.eth`)."""
    return bool(_BASENAME_RE.match((name or "").strip().lower()))


def namehash(name: str) -> bytes:
    """ENS namehash (ENSIP-1): 'my.base.eth' -> bytes32 node."""
    node = b"\x00" * 32
    name = (name or "").strip().rstrip(".")
    if not name:
        return node
    for label in reversed(name.split(".")):
        if label:
            node = core.w3.keccak(node + core.w3.keccak(text=label))
    return node


def resolve_basename_sync(name: str) -> str | None:
    """Resolve `something.base.eth` to its address via the Base L2 resolver.

    Returns a checksummed address, or None when the name is malformed,
    unregistered, or the resolver call fails.
    """
    name = (name or "").strip().lower()
    if not _BASENAME_RE.match(name):
        return None
    try:
        addr = core._contract_read(
            config.BASE_L2_RESOLVER_ADDRESS, _RESOLVER_ABI, "addr", namehash(name)
        )
        return Web3.to_checksum_address(addr) if addr and int(addr, 16) else None
    except Exception:
        return None


async def resolve_basename(name: str) -> str | None:
    """Async: basename resolution off the event loop."""
    return await asyncio.to_thread(resolve_basename_sync, name)


def reverse_basename_sync(address: str) -> str | None:
    """Reverse lookup: the primary basename of `address`, or None.

    Uses the official Base ReverseRegistrar to compute the ENSIP-19 reverse
    node on-chain (its label hashing is nontrivial), then reads name() from
    the public L2 resolver. Absence of a record is normal — None, not raise.
    """
    try:
        addr_cs = Web3.to_checksum_address(address)
        node = core._contract_read(
            config.BASE_REVERSE_REGISTRAR_ADDRESS, _REVERSE_REGISTRAR_ABI, "node", addr_cs
        )
        name = core._contract_read(config.BASE_L2_RESOLVER_ADDRESS, _RESOLVER_ABI, "name", node)
        return name or None
    except Exception:
        return None


async def reverse_basename(address: str) -> str | None:
    """Async: reverse basename lookup off the event loop."""
    return await asyncio.to_thread(reverse_basename_sync, address)


def basename_available_sync(name: str) -> bool | None:
    """True when `<name>.base.eth` resolves to the zero address — i.e. NO
    resolver record exists for it (unregistered).

    IMPORTANT: availability must be checked via the L2 RESOLVER, not
    `Registry.owner(namehash(name))` — under .base.eth the registry returns
    the L2Registrar for every subnode, so owner != 0 even for names nobody
    registered (the old check reported literally everything as 'taken').

    None for malformed input or RPC failure. False = a resolver record
    exists (registered, or at least configured).
    """
    name = (name or "").strip().lower()
    if not _BASENAME_RE.match(name):
        return None
    try:
        addr = core._contract_read(
            config.BASE_L2_RESOLVER_ADDRESS, _RESOLVER_ABI, "addr", namehash(name)
        )
        return not (addr and int(addr, 16))
    except Exception:
        return None


async def basename_available(name: str) -> bool | None:
    """Async: basename availability off the event loop."""
    return await asyncio.to_thread(basename_available_sync, name)
