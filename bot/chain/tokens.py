"""ERC-20 toolkit: balances, metadata, supply."""

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
