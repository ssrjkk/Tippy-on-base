"""Oracle layer: Chainlink feeds on Base + sequencer health monitoring.

Every price read is staleness-guarded (a dead upstream must never feed
bogus prices into UX or accounting) and TTL-cached (group commands hit
the same feeds repeatedly; each miss costs an RPC round trip).
"""

import asyncio
import time

from .. import config
from . import core

# Sequencer health changes rarely (only during incidents). Cache 10s.
_seq_cache: tuple[float, bool | None] | None = None
_SEQ_CACHE_TTL = 10.0

_AGGREGATOR_ABI = [
    {"inputs": [], "name": "latestRoundData", "outputs": [
        {"name": "roundId", "type": "uint80"},
        {"name": "answer", "type": "int256"},
        {"name": "startedAt", "type": "uint256"},
        {"name": "updatedAt", "type": "uint256"},
        {"name": "answeredInRound", "type": "uint80"},
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
]

# TTL cache for oracle prices: keyed by (feed, decimals, max_age);
# failures are NOT cached so a transient RPC blip can't pin None for a minute.
_price_cache: dict[tuple[str, int | None, int | None], tuple[float, float]] = {}


def price_cache_clear() -> None:
    """Drop cached oracle prices (tests / diagnostics)."""
    _price_cache.clear()


def clear_prices_caches() -> None:
    """Drop all price-layer caches including sequencer cache."""
    global _seq_cache
    _price_cache.clear()
    _seq_cache = None


def _sync_provider_caches() -> None:
    """Drop caches when the bound provider changed (e.g. tests rebinding w3)."""
    if core.sync_caches_with_provider():
        global _seq_cache
        _price_cache.clear()
        _seq_cache = None


def feed_price_sync(feed_address: str, *, decimals: int | None = None, max_age_seconds: int | None = None) -> float | None:
    """Read a Chainlink aggregator answer as a float; None when unavailable/stale.

    Stale answers (updatedAt older than max_age_seconds) are rejected so a dead
    upstream can't feed bogus prices into UX or accounting. Results are cached
    for config.PRICE_CACHE_SECONDS (0 disables the cache).
    """
    _sync_provider_caches()
    key = (feed_address.lower(), decimals, max_age_seconds)
    now = time.time()
    if config.PRICE_CACHE_SECONDS > 0:
        hit = _price_cache.get(key)
        if hit is not None and now - hit[0] < config.PRICE_CACHE_SECONDS:
            return hit[1]
    try:
        answer, updated_at, dec = _feed_read(feed_address, decimals=decimals)
        max_age = config.PRICE_FEED_MAX_AGE_SECONDS if max_age_seconds is None else max_age_seconds
        if answer <= 0:
            return None
        if max_age and int(updated_at) and now - int(updated_at) > max_age:
            return None
        value = answer / 10**dec
    except Exception:
        return None
    if config.PRICE_CACHE_SECONDS > 0:
        if len(_price_cache) > 1000:
            _price_cache.clear()
        _price_cache[key] = (now, value)
    return value


def _feed_read(feed_address: str, *, decimals: int | None = None) -> tuple[int, int, int]:
    """(answer, updatedAt, decimals) with RPC failover."""
    _, answer, _, updated_at, _ = core._contract_read(feed_address, _AGGREGATOR_ABI, "latestRoundData")
    if decimals is None:
        decimals = core._contract_read(feed_address, _AGGREGATOR_ABI, "decimals")
    return int(answer), int(updated_at), int(decimals)


async def feed_price(feed_address: str, **kwargs) -> float | None:
    """Async: Chainlink price off the event loop."""
    return await asyncio.to_thread(feed_price_sync, feed_address, **kwargs)


def l2_sequencer_ok_sync(feed_address: str | None = None) -> bool | None:
    """True while the Base sequencer is healthy (Chainlink uptime feed == 0).

    Cached 10s — sequencer status changes rarely (only during incidents).
    """
    global _seq_cache
    _sync_provider_caches()
    addr = feed_address or config.CHAINLINK_L2_SEQUENCER_FEED
    if not addr:
        return None
    now = time.monotonic()
    if _seq_cache is not None and now - _seq_cache[0] < _SEQ_CACHE_TTL:
        return _seq_cache[1]
    try:
        answer, _updated_at, _dec = _feed_read(addr, decimals=0)
        result = int(answer) == 0
    except Exception:
        result = None
    _seq_cache = (now, result)
    return result


async def l2_sequencer_ok() -> bool | None:
    """Async: sequencer health off the event loop."""
    return await asyncio.to_thread(l2_sequencer_ok_sync)


def get_eth_price_usd_sync() -> float | None:
    """ETH/USD from the Chainlink feed on Base. None if unavailable/stale."""
    return feed_price_sync(config.CHAINLINK_ETH_USD_FEED)


async def get_eth_price_usd() -> float | None:
    """Async: ETH/USD price off the event loop."""
    return await asyncio.to_thread(get_eth_price_usd_sync)


def get_usdc_price_usd_sync() -> float | None:
    """USDC/USD from the Chainlink feed on Base (~1.00; deviation is a red flag).

    Uses a wider staleness window than ETH because this feed updates on a
    long heartbeat, not per block.
    """
    return feed_price_sync(
        config.CHAINLINK_USDC_USD_FEED,
        max_age_seconds=config.USDC_PRICE_FEED_MAX_AGE_SECONDS,
    )


async def get_usdc_price_usd() -> float | None:
    """Async: USDC/USD price off the event loop."""
    return await asyncio.to_thread(get_usdc_price_usd_sync)
