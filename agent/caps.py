"""Spend-cap enforcement and circuit breaker.

Every agent action MUST pass through `check_action()` before execution.
`check_action` atomically VALIDATES **and RESERVES** the budget under a
process-wide lock: counters are updated before the caller executes, so two
concurrent actions cannot both pass the check on the same snapshot and
overshoot the daily cap. If execution then fails, the caller returns the
reservation via `release_action()`; once globally reserved, an action never
calls `record_action` (there is nothing left to record — the reservation is
the record). The state file is written atomically so a crash cannot corrupt
the counters mid-write.
"""

import json
import os
import threading
import time
from pathlib import Path

from . import config

_STATE_FILE = Path(__file__).resolve().parent / ".agent_state.json"

_state_lock = threading.Lock()


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return _default_state()
    try:
        return json.loads(_STATE_FILE.read_text())
    except (ValueError, OSError):
        return _default_state()


def _default_state() -> dict:
    return {
        "daily_spent": 0.0,
        "daily_date": "",
        "actions_this_hour": 0,
        "hour_ts": 0,
        "consecutive_errors": 0,
        "cooldown_until": 0.0,
    }


def _save_state(state: dict) -> None:
    tmp = _STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, _STATE_FILE)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _hour_bucket() -> int:
    return int(time.time()) // 3600


def check_action(cost_usdc: float) -> str | None:
    """Validate the action against all caps AND reserve its budget atomically.

    On success returns None and the budget is already reserved (both the
    daily spend and the hourly action counter are incremented under the
    lock). On failure returns an error message and reserves NOTHING.

    Callers MUST call `release_action(cost_usdc)` when execution fails so
    the reservation is returned.
    """
    with _state_lock:
        state = _load_state()
        now = time.time()

        # Circuit breaker — cooldown active
        if now < state["cooldown_until"]:
            remaining = int(state["cooldown_until"] - now)
            return f"Circuit breaker active, cooldown {remaining}s remaining"

        # Per-tx cap — checked first: a single oversized action is invalid
        # regardless of how much daily budget remains. Zero is allowed for
        # actions that do not spend (e.g. creating a paywall signal).
        if cost_usdc < 0:
            return f"Action cost must be non-negative (got ${cost_usdc:.2f})"
        if cost_usdc > config.PER_TX_CAP_USDC:
            return f"Per-tx cap ${config.PER_TX_CAP_USDC} exceeded (requested ${cost_usdc:.2f})"

        # Daily cap
        today = _today()
        if state["daily_date"] != today:
            state["daily_date"] = today
            state["daily_spent"] = 0.0
        if state["daily_spent"] + cost_usdc > config.DAILY_SPEND_CAP_USDC:
            return f"Daily cap ${config.DAILY_SPEND_CAP_USDC} reached (${state['daily_spent']:.2f} spent)"

        # Rate limit
        hour = _hour_bucket()
        if state["hour_ts"] != hour:
            state["hour_ts"] = hour
            state["actions_this_hour"] = 0
        if state["actions_this_hour"] >= config.MAX_ACTIONS_PER_HOUR:
            return f"Rate limit {config.MAX_ACTIONS_PER_HOUR} actions/hour reached"

        # Reserve: budget + action counter bumped under the same lock the
        # check ran under, so a concurrent check_action sees this action.
        state["daily_spent"] += cost_usdc
        state["actions_this_hour"] += 1
        _save_state(state)
        return None


def release_action(cost_usdc: float) -> None:
    """Return a previously-reserved budget because execution failed.

    Undoes the reservation: subtracts the cost back out of the daily spend
    and decrements the hourly action counter.
    """
    with _state_lock:
        state = _load_state()
        today = _today()
        if state["daily_date"] != today:
            state["daily_date"] = today
            state["daily_spent"] = 0.0
        state["daily_spent"] = max(0.0, state["daily_spent"] - cost_usdc)
        state["actions_this_hour"] = max(0, state["actions_this_hour"] - 1)
        _save_state(state)


def record_action(cost_usdc: float) -> None:
    """Backwards-compatible no-op.

    Budget is reserved atomically in check_action; calling this again would
    double-count. Kept so old call sites don't silently break.
    """
    return


def record_error() -> None:
    """Call on failure. Triggers circuit breaker after MAX_CONSECUTIVE_ERRORS."""
    with _state_lock:
        state = _load_state()
        state["consecutive_errors"] += 1
        if state["consecutive_errors"] >= config.MAX_CONSECUTIVE_ERRORS:
            state["cooldown_until"] = time.time() + config.COOLDOWN_SECONDS
            state["consecutive_errors"] = 0
        _save_state(state)


def get_status() -> dict:
    """Return current agent state for monitoring."""
    with _state_lock:
        state = _load_state()
        return {
            "daily_spent_usdc": state["daily_spent"],
            "daily_cap_usdc": config.DAILY_SPEND_CAP_USDC,
            "actions_this_hour": state["actions_this_hour"],
            "max_actions_per_hour": config.MAX_ACTIONS_PER_HOUR,
            "consecutive_errors": state["consecutive_errors"],
            "cooldown_active": time.time() < state["cooldown_until"],
        }
