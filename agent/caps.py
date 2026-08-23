"""Spend-cap enforcement and circuit breaker.

Every agent action MUST pass through `check_action()` before execution.
"""

import json
import time
from pathlib import Path
from . import config

_STATE_FILE = Path(__file__).resolve().parent / ".agent_state.json"


def _load_state() -> dict:
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text())
    return {
        "daily_spent": 0.0,
        "daily_date": "",
        "actions_this_hour": 0,
        "hour_ts": 0,
        "consecutive_errors": 0,
        "cooldown_until": 0.0,
    }


def _save_state(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _hour_bucket() -> int:
    return int(time.time()) // 3600


def check_action(cost_usdc: float) -> str | None:
    """Returns error message if action is blocked, None if allowed."""
    state = _load_state()
    now = time.time()

    # Circuit breaker — cooldown active
    if now < state["cooldown_until"]:
        remaining = int(state["cooldown_until"] - now)
        return f"Circuit breaker active, cooldown {remaining}s remaining"

    # Daily cap
    today = _today()
    if state["daily_date"] != today:
        state["daily_date"] = today
        state["daily_spent"] = 0.0
    if state["daily_spent"] + cost_usdc > config.DAILY_SPEND_CAP_USDC:
        return f"Daily cap ${config.DAILY_SPEND_CAP_USDC} reached (${state['daily_spent']:.2f} spent)"

    # Per-tx cap
    if cost_usdc > config.PER_TX_CAP_USDC:
        return f"Per-tx cap ${config.PER_TX_CAP_USDC} exceeded (requested ${cost_usdc:.2f})"

    # Rate limit
    hour = _hour_bucket()
    if state["hour_ts"] != hour:
        state["hour_ts"] = hour
        state["actions_this_hour"] = 0
    if state["actions_this_hour"] >= config.MAX_ACTIONS_PER_HOUR:
        return f"Rate limit {config.MAX_ACTIONS_PER_HOUR} actions/hour reached"

    return None


def record_action(cost_usdc: float) -> None:
    """Call AFTER successful action to update counters."""
    state = _load_state()
    today = _today()
    if state["daily_date"] != today:
        state["daily_date"] = today
        state["daily_spent"] = 0.0
    state["daily_spent"] += cost_usdc
    state["actions_this_hour"] += 1
    state["consecutive_errors"] = 0
    _save_state(state)


def record_error() -> None:
    """Call on failure. Triggers circuit breaker after MAX_CONSECUTIVE_ERRORS."""
    state = _load_state()
    state["consecutive_errors"] += 1
    if state["consecutive_errors"] >= config.MAX_CONSECUTIVE_ERRORS:
        state["cooldown_until"] = time.time() + config.COOLDOWN_SECONDS
        state["consecutive_errors"] = 0
    _save_state(state)


def get_status() -> dict:
    """Return current agent state for monitoring."""
    state = _load_state()
    return {
        "daily_spent_usdc": state["daily_spent"],
        "daily_cap_usdc": config.DAILY_SPEND_CAP_USDC,
        "actions_this_hour": state["actions_this_hour"],
        "max_actions_per_hour": config.MAX_ACTIONS_PER_HOUR,
        "consecutive_errors": state["consecutive_errors"],
        "cooldown_active": time.time() < state["cooldown_until"],
    }
