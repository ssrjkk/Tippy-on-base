"""Signing paths: EIP-1559 build -> sign -> broadcast, serialized.

Every send funnels through _build_and_send: chain-id guard first (one bad
RPC URL must never move hot-wallet funds onto another network), then a
shared lock + pending-nonce read so concurrent sends can't race.
"""

import asyncio
import threading
from decimal import ROUND_CEILING, Decimal

from web3 import Web3

from .. import config
from . import core, network

# Only the signing paths need this lock, so it lives here rather than in
# core (keeps the shared-state module free of send-path concerns).
_send_lock = threading.Lock()


def _build_and_send(build_fn) -> str:
    """Shared signing path: build an EIP-1559 tx under the send lock, sign, broadcast.

    `build_fn(nonce, max_fee_wei, priority_wei)` must return a transaction
    dict (web3 build_transaction output). Verifies the chain id first — one
    bad RPC URL must never move hot-wallet funds onto another network.
    """
    network.assert_base_chain_sync()
    acct = core.w3.eth.account.from_key(config.HOT_WALLET_KEY)
    with _send_lock:
        n = core.w3.eth.get_transaction_count(core.HOT_WALLET, "pending")
        base_fee = int(core.w3.eth.get_block("latest")["baseFeePerGas"])
        # Priority tip 0.01 gwei (Base's practical floor; lower tips can leave
        # a tx stuck). Max fee ~2x headroom absorbs short fee spikes.
        priority = core.w3.to_wei("0.01", "gwei")
        max_fee = base_fee * 2 + priority
        tx = build_fn(n, max_fee, priority)
        signed = acct.sign_transaction(tx)
        return "0x" + core.w3.eth.send_raw_transaction(signed.raw_transaction).hex()


def _send_eth_sync(to_address: str, amount_wei: int) -> str:
    """Send ETH (gas) from the hot wallet to an address. Returns tx hash.

    Used to drip gas to user wallets for on-chain market operations.
    Serialized by the shared send lock to avoid nonce conflicts.
    """

    def build(nonce_: int, max_fee: int, priority: int) -> dict:
        return {
            "from": core.HOT_WALLET,
            "to": Web3.to_checksum_address(to_address),
            "value": amount_wei,
            "nonce": nonce_,
            "maxPriorityFeePerGas": priority,
            "maxFeePerGas": max_fee,
            "chainId": core.w3.eth.chain_id,
            "gas": 21000,
        }

    return _build_and_send(build)


async def send_eth(to_address: str, amount_wei: int) -> str:
    """Async: send ETH from hot wallet off the event loop."""
    return await asyncio.to_thread(_send_eth_sync, to_address, amount_wei)


def _send_token_sync(to_address: str, amount_raw: int, token_address: str | None = None) -> str:
    """Send any ERC-20 from the hot wallet. amount_raw is in token micro-units.

    token_address=None sends USDC via the dedicated usdc handle (same math).
    Serialized with all other sends through _send_lock / shared nonce logic.
    """
    to_addr = Web3.to_checksum_address(to_address)

    def build(nonce_: int, max_fee: int, priority: int) -> dict:
        if token_address:
            contract = core.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=config.ERC20_ABI)
        else:
            contract = core.usdc  # pre-bound USDC handle
        return contract.functions.transfer(to_addr, int(amount_raw)).build_transaction({
            "from": core.HOT_WALLET,
            "nonce": nonce_,
            "maxPriorityFeePerGas": priority,
            "maxFeePerGas": max_fee,
            "chainId": core.w3.eth.chain_id,
        })

    return _build_and_send(build)


async def send_token(to_address: str, amount_raw: int, token_address: str | None = None) -> str:
    """Async: generic ERC-20 send from the hot wallet (off the event loop)."""
    return await asyncio.to_thread(_send_token_sync, to_address, amount_raw, token_address)


def _approve_token_sync(spender: str, amount_raw: int, token_address: str | None = None) -> str:
    """Approve `spender` to pull `amount_raw` of a token from the hot wallet."""

    def build(nonce_: int, max_fee: int, priority: int) -> dict:
        tok = Web3.to_checksum_address(token_address) if token_address else core.USDC
        contract = core.w3.eth.contract(address=tok, abi=config.ERC20_ABI)
        return contract.functions.approve(Web3.to_checksum_address(spender), int(amount_raw)).build_transaction({
            "from": core.HOT_WALLET,
            "nonce": nonce_,
            "maxPriorityFeePerGas": priority,
            "maxFeePerGas": max_fee,
            "chainId": core.w3.eth.chain_id,
        })

    return _build_and_send(build)


async def approve_token(spender: str, amount_raw: int, token_address: str | None = None) -> str:
    """Async: ERC-20 approve off the event loop."""
    return await asyncio.to_thread(_approve_token_sync, spender, amount_raw, token_address)


def _send_usdc_sync(to_address: str, amount_micro: int) -> str:
    """Internal sync send USDC from hot wallet. Returns tx hash. Raises on failure.

    Thin wrapper over the generic ERC-20 send (same lock, same fee logic).
    """
    return _send_token_sync(to_address, amount_micro, None)


async def send_usdc(to_address: str, amount_micro: int) -> str:
    """Async wrapper: send USDC from hot wallet without blocking the event loop.

    Runs the synchronous web3 transaction building + signing + sending in a
    separate thread via asyncio.to_thread, so the bot's event loop stays
    responsive during the ~10s RPC call.
    """
    return await asyncio.to_thread(_send_usdc_sync, to_address, amount_micro)


def withdraw_fee(amount_micro: int) -> int:
    """Withdrawal fee (config, default 1%), at least 1 micro-unit."""
    fee = (Decimal(amount_micro) * config.WITHDRAW_FEE_PCT).to_integral_value(rounding=ROUND_CEILING)
    return max(int(fee), 1)
