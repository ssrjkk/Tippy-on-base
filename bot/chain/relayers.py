"""Multi-relayer pool for high-throughput withdrawals.

Replaces the single-EOA bottleneck with N relayers, each with independent
nonces and daily USDC limits.  Round-robin selection distributes load;
nonce-per-relayer reads prevent race conditions under concurrency.

Config (bot.config):
    RELAYER_PRIVATE_KEYS  — comma-separated hex private keys
    RELAYER_DAILY_LIMIT   — per-relayer USDC daily cap (default 10_000)
    RELAYER_FEE_GAS_GWEI  — gas priority tip (default 0.01)

Relayer keys are derived at startup and stored in-memory only (never
logged or serialized).  Daily limits reset at midnight UTC.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from eth_typing import ChecksumAddress
from web3 import Web3

from .. import config

log = logging.getLogger("tipbot.relayers")

_STATE_FILE = Path(__file__).resolve().parent / ".relayer_usage.json"
_state_lock = threading.Lock()
# Overridable for tests/CI isolation.
_STATE_FILE_PATH = os.environ.get("RELAYER_STATE_FILE") or _STATE_FILE


def _load_usage() -> dict:
    """Load persisted per-day usage: {"<unixday>:<addr>": micro_spent}."""
    if not Path(_STATE_FILE_PATH).exists():
        return {}
    try:
        return json.loads(Path(_STATE_FILE_PATH).read_text())
    except (ValueError, OSError):
        return {}


def _save_usage(usage: dict) -> None:
    Path(_STATE_FILE_PATH).write_text(json.dumps(usage, indent=2))


@dataclass
class Relayer:
    """Single relayer: EOA address, derived from private key."""
    address: ChecksumAddress
    _key: str  # private key hex (never logged)
    _daily_limit_micro: int = 0
    _spent_today: int = 0
    _day_start: int = 0  # unix day (UTC)
    _persist: bool = False  # True only for production (from_config) relayers

    def __post_init__(self):
        self._lock = threading.Lock()
        if self._daily_limit_micro == 0:
            self._daily_limit_micro = int(
                Decimal(str(config.RELAYER_DAILY_LIMIT))
                * Decimal("1000000")  # USDC micro-units
            )

    def can_send(self, amount_micro: int) -> bool:
        """True if this relayer has enough remaining daily capacity."""
        with self._lock:
            self._maybe_reset()
            return self._spent_today + amount_micro <= self._daily_limit_micro

    def record_send(self, amount_micro: int) -> None:
        """Record a successful send against the daily cap."""
        with self._lock:
            self._maybe_reset()
            self._spent_today += amount_micro
            self._persist_state()

    def _persist_state(self) -> None:
        """Persist spent-today so a restart cannot reset the daily cap."""
        if not self._persist:
            return
        day = str(int(time.time()) // 86400)
        usage = _load_usage()
        usage[f"{day}:{self.address.lower()}"] = self._spent_today
        with _state_lock:
            _save_usage(usage)

    def remaining(self) -> int:
        """Remaining daily capacity in micro-USDC."""
        with self._lock:
            self._maybe_reset()
            return max(0, self._daily_limit_micro - self._spent_today)

    def spent_today_persisted(self) -> int:
        """Spent this UTC day (for diagnostics/tests)."""
        with self._lock:
            self._maybe_reset()
            return self._spent_today

    def _maybe_reset(self) -> None:
        """Reset the daily counter at midnight UTC and persist the new day."""
        now_day = int(time.time()) // 86400
        if now_day != self._day_start:
            self._day_start = now_day
            self._spent_today = 0
            self._persist_state()


class RelayerPool:
    """Thread-safe pool of relayers with round-robin selection.

    Usage:
        pool = RelayerPool.from_config()
        relayer = pool.select(amount_micro)  # picks next with capacity
        # ... build + sign + send using relayer._key ...
        pool.record(relayer.address, amount_micro)
    """

    def __init__(self, relayers: list[Relayer]):
        self._relayers = relayers
        self._idx = 0
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls) -> "RelayerPool":
        """Build pool from RELAYER_PRIVATE_KEYS config."""
        raw = config.RELAYER_PRIVATE_KEYS
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        relayers = []
        usage = _load_usage()
        day = str(int(time.time()) // 86400)
        for key in keys:
            try:
                acct = Web3().eth.account.from_key(key)
                addr = Web3.to_checksum_address(acct.address)
                r = Relayer(address=addr, _key=key, _persist=True)
                r._spent_today = int(usage.get(f"{day}:{addr.lower()}", 0))
                r._day_start = int(time.time()) // 86400
                relayers.append(r)
                log.info("relayer registered: %s (spent today=%s)", addr, r._spent_today)
            except Exception as e:
                log.warning("bad relayer key rejected (%s)", type(e).__name__)
        if not relayers:
            log.warning("no relayers configured — withdrawals will fail")
        return cls(relayers)

    def select(self, amount_micro: int) -> Relayer | None:
        """Pick the next relayer with enough remaining daily capacity.

        Round-robin across all relayers; returns None if all are exhausted.
        """
        if not self._relayers:
            return None
        with self._lock:
            n = len(self._relayers)
            for _ in range(n):
                r = self._relayers[self._idx % n]
                self._idx = (self._idx + 1) % n
                if r.can_send(amount_micro):
                    return r
        return None

    def record(self, address: str, amount_micro: int) -> None:
        """Record a successful send against the relayer's daily cap."""
        addr = Web3.to_checksum_address(address)
        with self._lock:
            for r in self._relayers:
                if r.address == addr:
                    r.record_send(amount_micro)
                    return

    @property
    def relayers(self) -> list[Relayer]:
        return list(self._relayers)

    @property
    def total_remaining(self) -> int:
        """Total remaining capacity across all relayers."""
        return sum(r.remaining() for r in self._relayers)

    def status(self) -> list[dict]:
        """Snapshot of all relayers for diagnostics."""
        return [
            {
                "address": r.address,
                "remaining": r.remaining(),
                "daily_limit": r._daily_limit_micro,
                "spent_today": r._spent_today,
            }
            for r in self._relayers
        ]


# ---------------------------------------------------------------------------
# Module-level singleton (lazy init)
# ---------------------------------------------------------------------------

_pool: RelayerPool | None = None


def get_pool() -> RelayerPool:
    """Get or create the global relayer pool singleton."""
    global _pool
    if _pool is None:
        _pool = RelayerPool.from_config()
    return _pool
