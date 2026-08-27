"""Tests for the Redis caching layer.

Uses a mock Redis client since no Redis server is available in CI.
Tests inject the mock directly into the cache module's internals.
"""


import pytest

import bot.cache as cache


class MockRedis:
    """Minimal in-memory Redis mock for testing."""

    def __init__(self):
        self._store = {}

    def ping(self):
        return True

    def get(self, key):
        return self._store.get(key)

    def setex(self, key, ttl, value):
        self._store[key] = value
        return True

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                n += 1
        return n

    def scan_iter(self, match, count=500):
        import fnmatch
        return [k for k in self._store if fnmatch.fnmatch(k, match)]


@pytest.fixture(autouse=True)
def _mock_redis():
    """Inject a MockRedis into the cache module for every test."""
    mock = MockRedis()
    old_redis = cache._redis
    old_avail = cache._redis_available
    cache._redis = mock
    cache._redis_available = True
    yield mock
    cache._redis = old_redis
    cache._redis_available = old_avail


class TestCacheOperations:
    def test_get_miss(self, _mock_redis):
        assert cache.cache_get("test", "key1") is None

    def test_set_then_get(self, _mock_redis):
        cache.cache_set("test", "key1", {"value": 42}, ttl=60)
        result = cache.cache_get("test", "key1")
        assert result == {"value": 42}

    def test_delete(self, _mock_redis):
        cache.cache_set("test", "key1", "hello", ttl=60)
        assert cache.cache_delete("test", "key1") is True
        assert cache.cache_get("test", "key1") is None

    def test_clear_namespace(self, _mock_redis):
        cache.cache_set("ns1", "a", 1, ttl=60)
        cache.cache_set("ns1", "b", 2, ttl=60)
        cache.cache_set("ns2", "c", 3, ttl=60)
        deleted = cache.cache_clear_namespace("ns1")
        assert deleted == 2
        assert cache.cache_get("ns2", "c") == 3

    def test_namespace_isolation(self, _mock_redis):
        cache.cache_set("ns_a", "key", "val_a", ttl=60)
        cache.cache_set("ns_b", "key", "val_b", ttl=60)
        assert cache.cache_get("ns_a", "key") == "val_a"
        assert cache.cache_get("ns_b", "key") == "val_b"


class TestRedisUnavailable:
    def test_get_returns_none(self):
        cache._redis = None
        cache._redis_available = False
        assert cache.cache_get("test", "key") is None

    def test_set_returns_false(self):
        cache._redis = None
        cache._redis_available = False
        assert cache.cache_set("test", "key", "val") is False

    def test_delete_returns_false(self):
        cache._redis = None
        cache._redis_available = False
        assert cache.cache_delete("test", "key") is False

    def test_clear_returns_zero(self):
        cache._redis = None
        cache._redis_available = False
        assert cache.cache_clear_namespace("test") == 0


class TestBalanceCache:
    def test_set_get_invalidate(self, _mock_redis):
        cache.set_cached_balance(123, 5_000_000)
        assert cache.get_cached_balance(123) == 5_000_000
        cache.invalidate_balance(123)
        assert cache.get_cached_balance(123) is None

    def test_miss_returns_none(self, _mock_redis):
        assert cache.get_cached_balance(999) is None


class TestMarketCache:
    def test_set_get_invalidate(self, _mock_redis):
        state = {"prices": [0.5, 0.5], "status": "open"}
        cache.set_cached_market(42, state)
        assert cache.get_cached_market(42) == state
        cache.invalidate_market(42)
        assert cache.get_cached_market(42) is None
