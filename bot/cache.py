"""Redis-backed caching layer for hot-path reads.

Provides transparent read-through caching for balance lookups and other
frequently-repeated reads.  Cache misses fall through to the underlying
module; cache hits return instantly.

Config (bot.config):
    REDIS_URL — Redis connection string (redis://host:port/db)
                If empty/missing, the cache is a no-op passthrough (no Redis).

TTL strategy:
    balances:   30s (write-through on tip/withdraw)
    market_state: 1s (near-realtime)
    rate_limits: 60s (per-IP)
    x402_tx_cache: 5min (replay protection)

Design:
    - All keys are prefixed with the namespace (e.g. "tipbot:bal:{tg_id}")
    - No async Redis client needed — aioredis/ping are not in deps
    - Falls back gracefully when Redis is down (returns None, caller proceeds)
    - Thread-safe for sync callers, with async wrappers via asyncio.to_thread
"""

import json
import logging
from typing import Any

log = logging.getLogger("tipbot.cache")

# ---------------------------------------------------------------------------
# Redis client (lazy, optional)
# ---------------------------------------------------------------------------

_redis = None
_redis_available = True


def _get_redis():
    """Get or create the Redis client.  Returns None if Redis is unavailable."""
    global _redis, _redis_available
    if _redis is not None:
        return _redis
    if not _redis_available:
        return None
    try:
        import redis as _redis_mod

        from .. import config
        url = getattr(config, "REDIS_URL", "") or ""
        if not url:
            _redis_available = False
            return None
        _redis = _redis_mod.from_url(url, decode_responses=True, socket_timeout=2)
        _redis.ping()
        log.info("Redis connected: %s", url.split("@")[-1])  # hide credentials
        return _redis
    except Exception as e:
        log.warning("Redis unavailable, cache disabled: %s", e)
        _redis_available = False
        return None


# ---------------------------------------------------------------------------
# Namespace prefixes
# ---------------------------------------------------------------------------

_PREFIX = "tipbot:"


def _ns(namespace: str, key: str) -> str:
    return f"{_PREFIX}{namespace}:{key}"


# ---------------------------------------------------------------------------
# Core cache operations (sync)
# ---------------------------------------------------------------------------

def cache_get(namespace: str, key: str) -> Any | None:
    """Get a cached value.  Returns None on miss or Redis failure."""
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(_ns(namespace, key))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def cache_set(namespace: str, key: str, value: Any, ttl: int = 30) -> bool:
    """Set a cached value with TTL in seconds.  Returns True on success."""
    r = _get_redis()
    if r is None:
        return False
    try:
        r.setex(_ns(namespace, key), ttl, json.dumps(value, default=str))
        return True
    except Exception:
        return False


def cache_delete(namespace: str, key: str) -> bool:
    """Delete a cached key.  Returns True on success."""
    r = _get_redis()
    if r is None:
        return False
    try:
        r.delete(_ns(namespace, key))
        return True
    except Exception:
        return False


def cache_clear_namespace(namespace: str) -> int:
    """Delete all keys in a namespace.  Returns count deleted."""
    r = _get_redis()
    if r is None:
        return 0
    try:
        pattern = f"{_PREFIX}{namespace}:*"
        keys = list(r.scan_iter(match=pattern, count=500))
        if keys:
            return r.delete(*keys)
        return 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------

async def cache_get_async(namespace: str, key: str) -> Any | None:
    """Async cache_get."""
    import asyncio
    return await asyncio.to_thread(cache_get, namespace, key)


async def cache_set_async(namespace: str, key: str, value: Any, ttl: int = 30) -> bool:
    """Async cache_set."""
    import asyncio
    return await asyncio.to_thread(cache_set, namespace, key, value, ttl)


async def cache_delete_async(namespace: str, key: str) -> bool:
    """Async cache_delete."""
    import asyncio
    return await asyncio.to_thread(cache_delete, namespace, key)


# ---------------------------------------------------------------------------
# Convenience: balance cache (the hot path)
# ---------------------------------------------------------------------------

_BAL_TTL = 30  # seconds — balance is stale-tolerant


def get_cached_balance(tg_id: int) -> int | None:
    """Get cached USDC balance (micro-units).  None on miss."""
    return cache_get("bal", str(tg_id))


def set_cached_balance(tg_id: int, micro: int) -> bool:
    """Cache USDC balance (micro-units)."""
    return cache_set("bal", str(tg_id), micro, ttl=_BAL_TTL)


def invalidate_balance(tg_id: int) -> bool:
    """Drop cached balance (after tip/withdraw/deposit)."""
    return cache_delete("bal", str(tg_id))


# ---------------------------------------------------------------------------
# Convenience: market state cache
# ---------------------------------------------------------------------------

_MARKET_TTL = 1  # near-realtime


def get_cached_market(market_id: int) -> dict | None:
    """Get cached market state.  None on miss."""
    return cache_get("mkt", str(market_id))


def set_cached_market(market_id: int, state: dict) -> bool:
    """Cache market state."""
    return cache_set("mkt", str(market_id), state, ttl=_MARKET_TTL)


def invalidate_market(market_id: int) -> bool:
    """Drop cached market state (after trade/resolve)."""
    return cache_delete("mkt", str(market_id))
