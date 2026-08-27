"""Prometheus-compatible metrics endpoint for monitoring.

Exposes key metrics in Prometheus text format:
  - tipbot_liabilities_usdc
  - tipbot_reserves_usdc
  - tipbot_solvent (0 or 1)
  - tipbot_users_total
  - tipbot_markets_open
  - tipbot_deposits_total
  - tipbot_withdrawals_total
  - tipbot_rpc_latency_seconds
"""

import time

from bot import base, config
from bot.ledger import async_ledger as ledger

_MICRO = 10 ** config.USDC_DECIMALS
_metrics_cache: dict | None = None
_metrics_ts: float = 0.0
_CACHE_TTL = 30  # seconds


async def collect_metrics() -> str:
    """Collect and format metrics in Prometheus text format."""
    global _metrics_cache, _metrics_ts

    now = time.time()
    if _metrics_cache and now - _metrics_ts < _CACHE_TTL:
        return _format_metrics(_metrics_cache)

    m = {}

    # Solvency
    try:
        liabilities = await ledger.total_liabilities()
        pending = await ledger.pending_deposit_total()
        m["liabilities_usdc"] = (liabilities + pending) / _MICRO
    except Exception:
        m["liabilities_usdc"] = -1

    try:
        vault_addr = config.VAULT_ADDRESS
        if vault_addr:
            reserves = await base.vault_balance()
        else:
            reserves = await base.hot_balance()
        m["reserves_usdc"] = reserves / _MICRO if reserves else -1
    except Exception:
        m["reserves_usdc"] = -1

    m["solvent"] = 1 if (
        m["reserves_usdc"] >= 0 and m["liabilities_usdc"] >= 0
        and m["reserves_usdc"] >= m["liabilities_usdc"]
    ) else 0

    # Stats
    try:
        s = await ledger.global_stats()
        m["users_total"] = s.get("users", 0)
        m["volume_usdc"] = s.get("volume_micro", 0) / _MICRO
        m["tips_usdc"] = s.get("tips_micro", 0) / _MICRO
        m["deposits_usdc"] = s.get("deposits_micro", 0) / _MICRO
    except Exception:
        m["users_total"] = 0
        m["volume_usdc"] = 0

    # Open markets
    try:
        markets = await ledger.open_markets(1000)
        m["markets_open"] = len(markets)
    except Exception:
        m["markets_open"] = 0

    _metrics_cache = m
    _metrics_ts = now
    return _format_metrics(m)


def _format_metrics(m: dict) -> str:
    lines = [
        "# HELP tipbot_liabilities_usdc Total user liabilities in USDC",
        "# TYPE tipbot_liabilities_usdc gauge",
        f"tipbot_liabilities_usdc {m.get('liabilities_usdc', -1)}",
        "",
        "# HELP tipbot_reserves_usdc On-chain reserves in USDC",
        "# TYPE tipbot_reserves_usdc gauge",
        f"tipbot_reserves_usdc {m.get('reserves_usdc', -1)}",
        "",
        "# HELP tipbot_solvent Whether the bot is solvent (1=yes, 0=no)",
        "# TYPE tipbot_solvent gauge",
        f"tipbot_solvent {m.get('solvent', 0)}",
        "",
        "# HELP tipbot_users_total Total registered users",
        "# TYPE tipbot_users_total gauge",
        f"tipbot_users_total {m.get('users_total', 0)}",
        "",
        "# HELP tipbot_markets_open Currently open prediction markets",
        "# TYPE tipbot_markets_open gauge",
        f"tipbot_markets_open {m.get('markets_open', 0)}",
        "",
        "# HELP tipbot_volume_usdc Total volume in USDC",
        "# TYPE tipbot_volume_usdc counter",
        f"tipbot_volume_usdc {m.get('volume_usdc', 0)}",
        "",
    ]
    return "\n".join(lines)
