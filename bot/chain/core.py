"""Runtime state and failover primitives for the Base chain adapter layer.

This module owns every mutable connection object (providers, contract
handles, the signing wallet) so there is exactly ONE place to rebind in
tests or during an operator rotation: `bot.chain.core`.

Domain modules (prices, tokens, transfers, ...) access state as
`core.w3` / `core.usdc` attribute lookups at call time — never import the
objects directly — so a single patch here propagates everywhere.
"""

import logging

from eth_account.messages import encode_defunct
from eth_typing import ChecksumAddress
from web3 import Web3

from .. import config

log = logging.getLogger("tipbot.chain")

# ---------------------------------------------------------------------------
# RPC provider with automatic failover
# ---------------------------------------------------------------------------
_PRIMARY_RPC = config.BASE_RPC_URL

# Optional fallback RPCs: comma-separated URLs in BASE_RPC_FALLBACK_URLS.
_RPC_FALLBACKS: list[str] = [
    u.strip() for u in (getattr(config, "BASE_RPC_FALLBACK_URLS", "") or "").split(",") if u.strip()
]

_ALL_RPC_URLS = [_PRIMARY_RPC, *_RPC_FALLBACKS]


def _make_w3(url: str) -> Web3:
    """Create a Web3 instance with timeout."""
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": config.RPC_TIMEOUT_SECONDS}))


# Provider list for failover. Index 0 is the primary endpoint.
_w3_providers = [_make_w3(u) for u in _ALL_RPC_URLS]
w3 = _w3_providers[0]

# Track the provider object consumers cached results against. Tests rebind
# `core.w3` to a fresh stub; any cache keyed on the OLD provider is stale, so
# modules call sync_caches_with_provider() before a cached read and clear
# their caches when the provider object identity changes.
_provider_identity = w3


def sync_caches_with_provider() -> bool:
    """Return True when `core.w3` changed since the last call.

    Consumer modules should clear their in-process caches when this returns
    True (the cached values were produced against a different provider).
    """
    global _provider_identity
    if w3 is _provider_identity:
        return False
    _provider_identity = w3
    return True

# Primary contract handles (bound to primary provider)
HOT_WALLET = Web3.to_checksum_address(w3.eth.account.from_key(config.HOT_WALLET_KEY).address)
USDC = Web3.to_checksum_address(config.USDC_ADDRESS)
usdc = w3.eth.contract(address=USDC, abi=config.ERC20_ABI)


def _rpc_call(fn, *args, token_address: str | None = None, abi=None):
    """Try `fn(contract)` across all RPC providers before giving up.

    `fn` receives an ERC-20 handle bound to each provider (USDC by default).
    Returns the result on success; raises the last exception if all fail.
    """
    last_err = None
    for provider in _w3_providers:
        try:
            tok = Web3.to_checksum_address(token_address) if token_address else USDC
            contract = provider.eth.contract(address=tok, abi=abi or config.ERC20_ABI)
            return fn(contract, *args)
        except Exception as e:
            last_err = e
            url = getattr(getattr(provider, "provider", None), "endpoint_uri", "?")
            log.debug("RPC %s failed: %s", url, e)
            continue
    raise RuntimeError(f"all RPC providers failed: {last_err}")


def _contract_read(contract_address: str, abi: list, fn_name: str, *args):
    """Read a view function across all RPC providers before giving up.

    Same failover discipline as _rpc_call, but for arbitrary contracts
    (feeds, resolvers, routers, ...) so a throttling endpoint can't break UX.
    """
    last_err = None
    cs = Web3.to_checksum_address(contract_address)
    for provider in _w3_providers:
        try:
            contract = provider.eth.contract(address=cs, abi=abi)
            return getattr(contract.functions, fn_name)(*args).call()
        except Exception as e:
            last_err = e
            url = getattr(getattr(provider, "provider", None), "endpoint_uri", "?")
            log.debug("RPC %s failed (%s): %s", url, fn_name, e)
            continue
    raise RuntimeError(f"all RPC providers failed for {fn_name}: {last_err}")


def hot_wallet() -> ChecksumAddress:
    return HOT_WALLET


def rpc_url() -> str:
    """Return the primary Base RPC URL (used by onchain_market.py)."""
    return _PRIMARY_RPC


# ---------------------------------------------------------------------------
# Signature recovery (wallet-link auth)
# ---------------------------------------------------------------------------

def _recover_signer_sync(message: str, signature: str) -> str:
    """Recover the address that signed `message` (ETH personal_sign)."""
    return w3.eth.account.recover_message(
        encode_defunct(text=message), signature=signature
    )


async def recover_signer(message: str, signature: str) -> str:
    """Async: recover signer (off the event loop)."""
    import asyncio
    return await asyncio.to_thread(_recover_signer_sync, message, signature)
