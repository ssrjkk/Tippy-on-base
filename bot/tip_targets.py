"""Tip/x402 recipient resolution: Telegram usernames AND Basenames.

A target is resolved in this order:
  1. `@username` / `username`          -> ledger.find_by_username
  2. `name.base.eth` (a Basename)      -> on-chain resolution to an address,
                                          then the tg_id that owns the
                                          address (external linked wallet OR
                                          a custodial in-bot wallet).

Basename resolutions are cached for 5 minutes (names rarely change owners,
and every miss would otherwise cost an RPC round trip).
"""

import asyncio
import time

from bot.chain.basenames import is_basename, resolve_basename_sync, reverse_basename_sync
from bot.ledger import async_ledger as ledger

_CACHE_TTL_SECONDS = 300
_CACHE_MAX = 5000
_cache: dict[str, tuple[int | None, float]] = {}


async def resolve_tip_target(target: str) -> tuple[int | None, str | None]:
    """Resolve a user-supplied target to a tg_id.

    Returns (tg_id, error_key). Exactly one is meaningful:
      - (tg_id, None)              — resolved;
      - (None, None)               — not a basename; caller falls back to the
                                     Telegram-username path;
      - (None, 'basename_unknown') — a well-formed basename with no Tippy
                                     user behind it.
    """
    name = (target or "").strip().lstrip("@").lower()
    if not is_basename(name):
        return None, None

    hit = _cache.get(name)
    if hit is not None:
        tg_id, ts = hit
        if time.time() - ts <= _CACHE_TTL_SECONDS:
            if tg_id is None:
                return None, "basename_unknown"
            return tg_id, None
        _cache.pop(name, None)

    address = await asyncio.to_thread(resolve_basename_sync, name)
    if not address:
        _cache[name] = (None, time.time())
        return None, "basename_unknown"

    tg_id = await ledger.tg_id_of_address(address)  # external linked wallet
    if tg_id is None:
        tg_id = await ledger.tg_id_of_wallet_address(address)  # custodial wallet
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()  # crude bound; names repopulate on demand
    _cache[name] = (tg_id, time.time())
    if tg_id is None:
        return None, "basename_unknown"
    return tg_id, None

_reverse_cache: dict[str, tuple[str | None, float]] = {}
_REVERSE_TTL_SECONDS = 600


async def display_name_for(tg_id: int) -> str | None:
    """A displayable Basename for a user with no Telegram username.

    Resolves the user's address (external linked wallet first, then the
    custodial in-bot wallet) to its PRIMARY basename via ENSIP-19 reverse
    resolution. Cached 10 minutes (negative results too). None = no basename.
    """
    linked = await ledger.linked_address(tg_id)
    wallet = await ledger.get_active_wallet(tg_id)
    address = linked or (wallet["address"] if wallet else None)
    if not address:
        return None
    addr = address.lower()
    hit = _reverse_cache.get(addr)
    if hit is not None:
        name, ts = hit
        if time.time() - ts <= _REVERSE_TTL_SECONDS:
            return name
        _reverse_cache.pop(addr, None)
    name = await asyncio.to_thread(reverse_basename_sync, address)
    if len(_reverse_cache) >= _CACHE_MAX:
        _reverse_cache.clear()
    _reverse_cache[addr] = (name, time.time())
    return name
