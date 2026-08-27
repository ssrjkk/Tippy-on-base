"""Base chain adapter layer.

Layered architecture (top -> bottom):
    handlers/services  ->  bot.base (facade)  ->  bot.chain.*

Modules:
    core         runtime state, provider failover, signature recovery
    network      chain guard, blocks, fees, ETH balance, activity
    tokens       ERC-20 reads (balances, meta, supply, reserves)
    prices       Chainlink feeds + sequencer health + TTL cache
    basenames    Base name service resolution (forward/reverse/availability)
    dex          Aerodrome executable swap quotes
    transfers    signing paths: EIP-1559 build/sign/send (serialized)
    transactions receipt polling, status, input decoding
    deposits     deposit intake sweeps + stuck-withdrawal refunds

State lives ONLY in core (and per-module caches); every cross-module access
is a qualified attribute lookup (`core.w3`) so tests patch one surface.
"""

from . import (
    basenames,
    core,
    deposits,
    dex,
    network,
    prices,
    tokens,
    transactions,
    transfers,
)

__all__ = [
    "basenames", "core", "deposits", "dex", "network",
    "prices", "tokens", "transactions", "transfers",
]
