"""Tests for the multi-relayer pool."""

import time
from pathlib import Path

import pytest
from web3 import Web3

from bot.chain.relayers import Relayer, RelayerPool, _load_usage, _save_usage


def _make_relayer(addr_suffix: str = "aa", daily_limit: int = 10_000_000) -> Relayer:
    """Build a Relayer with a deterministic address for testing."""
    addr = Web3.to_checksum_address("0x" + addr_suffix * 20)
    return Relayer(address=addr, _key="0x" + "00" * 32, _daily_limit_micro=daily_limit)


class TestRelayer:
    def test_can_send_within_limit(self):
        r = _make_relayer(daily_limit=1_000_000)
        assert r.can_send(500_000)

    def test_cannot_send_over_limit(self):
        r = _make_relayer(daily_limit=100)
        assert not r.can_send(200)

    def test_record_send_accumulates(self):
        r = _make_relayer(daily_limit=1_000_000)
        r.record_send(400_000)
        r.record_send(400_000)
        assert r.remaining() == 200_000

    def test_daily_reset(self):
        r = _make_relayer(daily_limit=1_000_000)
        r.record_send(900_000)
        # Simulate new day
        r._day_start = int(time.time()) // 86400 - 1
        assert r.remaining() == 1_000_000


class TestRelayerPersistence:
    @pytest.fixture(autouse=True)
    def _isolate_state_file(self, monkeypatch, tmp_path):
        state_file = tmp_path / "relayer_usage.json"
        monkeypatch.setattr("bot.chain.relayers._STATE_FILE_PATH", str(state_file))
        yield
        Path(state_file).unlink(missing_ok=True)

    def test_record_send_persists_for_production_relayer(self):
        r = _make_relayer(daily_limit=1_000_000)
        r._persist = True
        r.record_send(400_000)
        usage = _load_usage()
        day = str(int(time.time()) // 86400)
        assert usage.get(f"{day}:{r.address.lower()}") == 400_000

    def test_non_production_relayer_does_not_persist(self):
        r = _make_relayer(daily_limit=1_000_000)
        r.record_send(400_000)
        assert _load_usage() == {}

    def test_from_config_restores_spent_after_restart(self, monkeypatch):
        from bot import config as bot_config
        monkeypatch.setattr(bot_config, "RELAYER_PRIVATE_KEYS", "0x" + ("01" * 32))
        addr = Web3.to_checksum_address(Web3().eth.account.from_key("0x" + ("01" * 32)).address)
        day = str(int(time.time()) // 86400)
        _save_usage({f"{day}:{addr.lower()}": 300_000})
        pool = RelayerPool.from_config()
        assert pool.relayers[0].spent_today_persisted() == 300_000


class TestRelayerPool:
    def test_select_picks_relayers(self):
        r1 = _make_relayer("11")
        r2 = _make_relayer("22")
        pool = RelayerPool([r1, r2])
        picked = pool.select(100)
        assert picked is not None
        assert picked.address in (r1.address, r2.address)

    def test_select_returns_none_when_exhausted(self):
        r1 = _make_relayer(daily_limit=100)
        pool = RelayerPool([r1])
        pool.record(r1.address, 100)
        assert pool.select(1) is None

    def test_record_updates_relayer(self):
        r1 = _make_relayer(daily_limit=1_000_000)
        pool = RelayerPool([r1])
        pool.record(r1.address, 500_000)
        assert r1.remaining() == 500_000

    def test_round_robin(self):
        r1 = _make_relayer("11", daily_limit=1_000_000)
        r2 = _make_relayer("22", daily_limit=1_000_000)
        pool = RelayerPool([r1, r2])
        p1 = pool.select(1)
        p2 = pool.select(1)
        assert p1 is not None and p2 is not None
        assert p1.address != p2.address

    def test_total_remaining(self):
        r1 = _make_relayer(daily_limit=1_000_000)
        r2 = _make_relayer(daily_limit=2_000_000)
        pool = RelayerPool([r1, r2])
        assert pool.total_remaining == 3_000_000
        pool.record(r1.address, 500_000)
        assert pool.total_remaining == 2_500_000

    def test_status(self):
        r1 = _make_relayer("11")
        pool = RelayerPool([r1])
        status = pool.status()
        assert len(status) == 1
        assert "address" in status[0]
        assert "remaining" in status[0]

    def test_empty_pool(self):
        pool = RelayerPool([])
        assert pool.select(1) is None
        assert pool.total_remaining == 0
        assert pool.status() == []

    def test_skips_relayers_at_limit(self):
        r1 = _make_relayer("11", daily_limit=100)
        r2 = _make_relayer("22", daily_limit=1_000_000)
        pool = RelayerPool([r1, r2])
        pool.record(r1.address, 100)  # exhaust r1
        picked = pool.select(1)
        assert picked is not None
        assert picked.address == r2.address
