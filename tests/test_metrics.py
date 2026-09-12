"""Metrics endpoint: numeric correctness (reserves vs liabilities in USDC)."""

from unittest.mock import AsyncMock, patch

import pytest

from web import metrics


@pytest.fixture(autouse=True)
def _reset_cache():
    metrics._metrics_cache = None
    metrics._metrics_ts = 0.0
    yield
    metrics._metrics_cache = None
    metrics._metrics_ts = 0.0


def _parse(out: str) -> dict:
    d = {}
    for line in out.splitlines():
        if line and not line.startswith("#"):
            name, _, val = line.partition(" ")
            d[name] = float(val)
    return d


def _make_env(hot: float | None):
    mock_ledger = MagicMockishLedger()
    mock_base = AsyncMock()
    if hot is None:
        mock_base.hot_balance = AsyncMock(side_effect=Exception("RPC down"))
    else:
        mock_base.hot_balance = AsyncMock(return_value=hot)
    mock_config = MockConfig(VAULT_ADDRESS="", USDC_DECIMALS=6)
    return mock_ledger, mock_base, mock_config


class MockConfig:
    def __init__(self, VAULT_ADDRESS, USDC_DECIMALS):
        self.VAULT_ADDRESS = VAULT_ADDRESS
        self.USDC_DECIMALS = USDC_DECIMALS


class MagicMockishLedger:
    total_liabilities = AsyncMock(return_value=60_000_000)
    open_markets = AsyncMock(return_value=[])
    global_stats = AsyncMock(return_value={"users": 2, "volume_micro": 10_000_000, "tips_micro": 4_000_000, "deposits_micro": 10_000_000})


@pytest.mark.asyncio
async def test_metrics_solvent_units():
    """hot_balance returns float USDC (100.0); liabilities 60 USDC -> solvent."""
    ledger, base, config = _make_env(hot=100.0)
    with patch("web.metrics.ledger", ledger), patch("web.metrics.base", base), patch("web.metrics.config", config):
        parsed = _parse(await metrics.collect_metrics())
    assert parsed["tipbot_liabilities_usdc"] == 60.0
    assert parsed["tipbot_reserves_usdc"] == 100.0
    assert parsed["tipbot_solvent"] == 1.0


@pytest.mark.asyncio
async def test_metrics_insolvent_when_reserves_low():
    """Regression: old code divided dollar reserves by 1e6, solvent was always 0."""
    ledger, base, config = _make_env(hot=40.0)
    with patch("web.metrics.ledger", ledger), patch("web.metrics.base", base), patch("web.metrics.config", config):
        parsed = _parse(await metrics.collect_metrics())
    assert parsed["tipbot_reserves_usdc"] == 40.0
    assert parsed["tipbot_solvent"] == 0.0

