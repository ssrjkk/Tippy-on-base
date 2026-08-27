"""ERC-20 toolkit: balances, metadata, supply; hot-wallet & vault reserves."""

import asyncio

from web3 import Web3

from .. import config
from . import core

_token_meta_cache: dict[str, dict] = {}


def token_balance_sync(address: str, token_address: str | None = None) -> int:
    """ERC-20 balance of `address` in micro-units. Defaults to USDC."""
    addr = Web3.to_checksum_address(address)
    tok = token_address or config.USDC_ADDRESS
    contract = core.w3.eth.contract(address=Web3.to_checksum_address(tok), abi=config.ERC20_ABI)
    try:
        return contract.functions.balanceOf(addr).call()
    except Exception:
        return core._rpc_call(lambda c, a=addr: c.functions.balanceOf(a).call(), token_address=token_address)


async def token_balance(address: str, token_address: str | None = None) -> int:
    """Async: ERC-20 balance off the event loop."""
    return await asyncio.to_thread(token_balance_sync, address, token_address)


def token_allowance_sync(owner: str, spender: str, token_address: str | None = None) -> int:
    """ERC-20 allowance of `spender` to spend `owner`'s tokens."""
    tok = token_address or config.USDC_ADDRESS
    _ERC20_ABI_EXTRA = [
        {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    ]
    contract = core.w3.eth.contract(address=Web3.to_checksum_address(tok), abi=_ERC20_ABI_EXTRA)
    try:
        return contract.functions.allowance(
            Web3.to_checksum_address(owner), Web3.to_checksum_address(spender)
        ).call()
    except Exception:
        return 0


async def token_allowance(owner: str, spender: str, token_address: str | None = None) -> int:
    """Async: ERC-20 allowance off the event loop."""
    return await asyncio.to_thread(token_allowance_sync, owner, spender, token_address)


def token_meta_sync(token_address: str) -> dict:
    """symbol / decimals / name of any ERC-20, cached per process."""
    addr = Web3.to_checksum_address(token_address)
    cached = _token_meta_cache.get(addr)
    if cached:
        return dict(cached)
    contract = core.w3.eth.contract(address=addr, abi=config.ERC20_ABI)
    meta = {
        "address": addr,
        "symbol": contract.functions.symbol().call(),
        "decimals": int(contract.functions.decimals().call()),
        "name": "",
    }
    try:
        meta["name"] = contract.functions.name().call()
    except Exception:
        pass  # some minimal tokens omit name()
    _token_meta_cache[addr] = meta
    return dict(meta)


async def token_meta(token_address: str) -> dict:
    """Async: token metadata off the event loop."""
    return await asyncio.to_thread(token_meta_sync, token_address)


def erc20_total_supply_sync(token_address: str | None = None) -> int:
    """Total supply of an ERC-20 in raw units (USDC by default)."""
    tok = Web3.to_checksum_address(token_address or core.USDC)
    contract = core.w3.eth.contract(address=tok, abi=config.ERC20_ABI)
    try:
        return contract.functions.totalSupply().call()
    except Exception:
        return core._rpc_call(
            lambda c: c.functions.totalSupply().call(),
            token_address=token_address,
        )


async def erc20_total_supply(token_address: str | None = None) -> int:
    """Async: total supply off the event loop."""
    return await asyncio.to_thread(erc20_total_supply_sync, token_address)


def _hot_balance_sync() -> float:
    try:
        micro = core.usdc.functions.balanceOf(core.HOT_WALLET).call()
    except Exception:
        # Fallback: try all providers
        micro = core._rpc_call(lambda c: c.functions.balanceOf(core.HOT_WALLET).call())
    return micro / 10**config.USDC_DECIMALS


async def hot_balance() -> float:
    """Async: hot-wallet USDC balance (off the event loop)."""
    return await asyncio.to_thread(_hot_balance_sync)


def _vault_balance_sync() -> float | None:
    """On-chain USDC held by the TipBotVault treasury, or None if not deployed.

    This is the on-chain proof-of-reserves: anyone can re-verify it directly
    on Base (USDC.balanceOf(vault) == totalReserves()).
    """
    if not config.VAULT_ADDRESS:
        return None
    try:
        micro = core.usdc.functions.balanceOf(Web3.to_checksum_address(config.VAULT_ADDRESS)).call()
    except Exception:
        micro = core._rpc_call(lambda c: c.functions.balanceOf(Web3.to_checksum_address(config.VAULT_ADDRESS)).call())
    return micro / 10**config.USDC_DECIMALS


async def vault_balance() -> float | None:
    """Async: on-chain vault balance (off the event loop)."""
    return await asyncio.to_thread(_vault_balance_sync)
