"""Runtime state and failover primitives for the Base chain adapter layer.

This module owns every mutable connection object (providers, contract
handles, the signing wallet) so there is exactly ONE place to rebind in
tests or during an operator rotation: `bot.chain.core`.

Domain modules (prices, tokens, transfers, ...) access state as
`core.w3` / `core.usdc` attribute lookups at call time — never import the
objects directly — so a single patch here propagates everywhere.
"""

import logging
import time

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

# Circuit breaker: if all RPC providers fail 5+ times within 60s, pause for 30s.
_CB_FAIL_THRESHOLD = 5
_CB_WINDOW_SECONDS = 60
_CB_OPEN_SECONDS = 30
_cb_fail_times: list[float] = []
_cb_open_until: float = 0.0


def _cb_record_failure() -> None:
    global _cb_open_until
    now = time.monotonic()
    _cb_fail_times.append(now)
    # Prune old entries outside the window
    cutoff = now - _CB_WINDOW_SECONDS
    while _cb_fail_times and _cb_fail_times[0] < cutoff:
        _cb_fail_times.pop(0)
    if len(_cb_fail_times) >= _CB_FAIL_THRESHOLD and _cb_open_until <= now:
        _cb_open_until = now + _CB_OPEN_SECONDS
        log.warning("RPC circuit breaker OPEN for %ds (%d failures in %ds)",
                    _CB_OPEN_SECONDS, len(_cb_fail_times), _CB_WINDOW_SECONDS)


def _cb_check() -> None:
    now = time.monotonic()
    if _cb_open_until > now:
        raise RuntimeError(f"RPC circuit breaker open — retry in {int(_cb_open_until - now)}s")


def _cb_reset() -> None:
    _cb_fail_times.clear()


def _rpc_call(fn, *args, token_address: str | None = None, abi=None):
    """Try `fn(contract)` across all RPC providers before giving up.

    `fn` receives an ERC-20 handle bound to each provider (USDC by default).
    Returns the result on success; raises the last exception if all fail.
    Includes circuit breaker: pauses if all providers fail repeatedly.
    """
    _cb_check()
    last_err = None
    for provider in _w3_providers:
        try:
            tok = Web3.to_checksum_address(token_address) if token_address else USDC
            contract = provider.eth.contract(address=tok, abi=abi or config.ERC20_ABI)
            result = fn(contract, *args)
            _cb_reset()
            return result
        except Exception as e:
            last_err = e
            url = getattr(getattr(provider, "provider", None), "endpoint_uri", "?")
            log.debug("RPC %s failed: %s", url, e)
            continue
    _cb_record_failure()
    raise RuntimeError(f"all RPC providers failed: {last_err}")


def _contract_read(contract_address: str, abi: list, fn_name: str, *args):
    """Read a view function across all RPC providers before giving up.

    Same failover discipline as _rpc_call, but for arbitrary contracts
    (feeds, resolvers, routers, ...) so a throttling endpoint can't break UX.
    Includes circuit breaker: pauses if all providers fail repeatedly.
    """
    _cb_check()
    last_err = None
    cs = Web3.to_checksum_address(contract_address)
    for provider in _w3_providers:
        try:
            contract = provider.eth.contract(address=cs, abi=abi)
            result = getattr(contract.functions, fn_name)(*args).call()
            _cb_reset()
            return result
        except Exception as e:
            last_err = e
            url = getattr(getattr(provider, "provider", None), "endpoint_uri", "?")
            log.debug("RPC %s failed (%s): %s", url, fn_name, e)
            continue
    _cb_record_failure()
    raise RuntimeError(f"all RPC providers failed for {fn_name}: {last_err}")


def _active_then_fallbacks(first=None):
    """Providers to try in order: the requested `first` (or the active
    provider), then the configured fallbacks.

    `core.w3` may be rebound by operators/tests to a provider not in
    _w3_providers; that active one is always tried first so a single rebind
    (or a mocked provider) still controls the money path. Callers in `base`
    pass `first` = their own legacy provider (base.w3) so existing fakes that
    patch that object keep working, with the core RPC pool as a fallback.
    """
    active = first or w3
    seen = {id(active)}
    yield active
    if id(w3) not in seen:
        seen.add(id(w3))
        yield w3
    for p in _w3_providers:
        if id(p) not in seen:
            seen.add(id(p))
            yield p


def send_raw_transaction(raw: bytes, first=None) -> bytes:
    """Broadcast a signed raw tx, trying the active provider then fallbacks.

    `raw` is already signed and nonce-locked by the caller. Re-broadcasting
    the same raw tx to another provider on timeout is safe (same nonce, same
    hash); at most one lands and a late reply from the first is identical.
    """
    _cb_check()
    last_err = None
    for provider in _active_then_fallbacks(first):
        try:
            result = provider.eth.send_raw_transaction(raw)
            _cb_reset()
            return result
        except Exception as e:
            last_err = e
            url = getattr(getattr(provider, "provider", None), "endpoint_uri", "?")
            log.debug("RPC %s send_raw_transaction failed: %s", url, e)
            continue
    _cb_record_failure()
    raise RuntimeError(f"all RPC providers failed to broadcast tx: {last_err}")


def get_transaction_count(address: str, first=None) -> int:
    """Pending nonce of `address` across providers (failover)."""
    _cb_check()
    last_err = None
    for provider in _active_then_fallbacks(first):
        try:
            result = provider.eth.get_transaction_count(Web3.to_checksum_address(address), "pending")
            _cb_reset()
            return int(result)
        except Exception as e:
            last_err = e
            continue
    _cb_record_failure()
    raise RuntimeError(f"all RPC providers failed to read nonce: {last_err}")


def get_latest_base_fee(first=None) -> int:
    """baseFeePerGas of the latest block across providers (failover)."""
    _cb_check()
    last_err = None
    for provider in _active_then_fallbacks(first):
        try:
            result = provider.eth.get_block("latest")["baseFeePerGas"]
            _cb_reset()
            return int(result)
        except Exception as e:
            last_err = e
            continue
    _cb_record_failure()
    raise RuntimeError(f"all RPC providers failed to read base fee: {last_err}")


def get_code(address: str, first=None) -> bytes:
    """Runtime code of an address across providers (failover)."""
    _cb_check()
    last_err = None
    for provider in _active_then_fallbacks(first):
        try:
            result = provider.eth.get_code(Web3.to_checksum_address(address))
            _cb_reset()
            return result
        except Exception as e:
            last_err = e
            continue
    _cb_record_failure()
    raise RuntimeError(f"all RPC providers failed to read code: {last_err}")


def get_transaction_receipt(tx_hash: str, first=None) -> dict | None:
    """Transaction receipt across providers (failover).

    Returns None when the tx is not yet mined on the first responsive provider
    (web3 returns None rather than raising for a missing receipt).
    """
    _cb_check()
    for provider in _active_then_fallbacks(first):
        try:
            result = provider.eth.get_transaction_receipt(tx_hash)
            # Receipts are chain-wide: a provider returning None means the tx
            # is not yet mined (web3 returns None rather than raising). That is
            # authoritative — there is no point querying another RPC.
            if result is None:
                return None
            _cb_reset()
            return dict(result)
        except Exception as e:
            last_err = e
            continue
    _cb_record_failure()
    raise RuntimeError(f"all RPC providers failed to read receipt: {last_err}")


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
