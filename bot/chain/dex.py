"""Aerodrome DEX: real executable swap pricing from Base's largest AMM.

Router.getAmountsOut walks the actual pool route, so a quote includes pool
fees and current reserves depth — this is what a swap would really pay,
not a mid-market rate.
"""

import asyncio

from web3 import Web3

from .. import config
from . import core

_AERODROME_ROUTE_TYPE = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "stable", "type": "bool"},
    {"name": "factory", "type": "address"},
]

_AERODROME_ABI = [
    {
        "name": "getAmountsOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "routes", "type": "tuple[]", "components": _AERODROME_ROUTE_TYPE},
        ],
        "outputs": [{"name": "", "type": "uint256[]"}],
    }
]


def aerodrome_quote_sync(
    amount_in_raw: int,
    token_in: str,
    token_out: str,
    *,
    stable: bool = False,
) -> int | None:
    """Executable output amount (raw units) for swapping on Aerodrome.

    Returns None when there is no route / liquidity or the RPC fails —
    callers must treat that as "cannot trade", never as zero slippage.
    """
    try:
        route = [
            (
                Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out),
                bool(stable),
                Web3.to_checksum_address(config.AERODROME_FACTORY_ADDRESS),
            )
        ]
        amounts = core._contract_read(
            config.AERODROME_ROUTER_ADDRESS, _AERODROME_ABI, "getAmountsOut",
            int(amount_in_raw), route,
        )
        return int(amounts[-1]) if amounts else None
    except Exception:
        return None


def usdc_to_eth_quote_sync(amount_micro: int) -> float | None:
    """Convenience: how much ETH (float) `amount_micro` micro-USDC buys."""
    out = aerodrome_quote_sync(amount_micro, config.USDC_ADDRESS, config.WETH_ADDRESS)
    return out / 10**18 if out is not None else None


async def usdc_to_eth_quote(amount_micro: int) -> float | None:
    """Async: Aerodrome USDC->ETH quote off the event loop."""
    return await asyncio.to_thread(usdc_to_eth_quote_sync, amount_micro)
