"""Smart Wallet (ERC-4337) management for Tippy users.

Replaces raw-EOA per-user wallets with Smart Accounts:
  - Deterministic addresses via CREATE2 (factory + tg_id)
  - Gasless: paymaster sponsors gas (bot relayer key signs)
  - User doesn't need ETH or manage private keys
  - Bot signs UserOperations on behalf of user

Flow:
  1. Bot creates SmartAccount for user via SmartAccountFactory
  2. User trades: bot builds UserOperation, signs with relayer key
  3. Paymaster validates relayer signature → sponsors gas
  4. EntryPoint bundles and executes on-chain
  5. After execution, USDC is transferred from SmartAccount (if needed)

Requires: config.SMART_WALLET_ENTRYPOINT, SMART_WALLET_FACTORY_ADDRESS,
          SMART_WALLET_PAYMASTER_ADDRESS, SMART_WALLET_RELAYER_KEY,
          USDC_ADDRESS.
"""
import asyncio
import json
import logging

from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from . import config

log = logging.getLogger("tipbot.smart_wallet")

MICRO = 10 ** config.USDC_DECIMALS

# ---------------------------------------------------------------------------
# ABIs (minimal — just the functions we call)
# ---------------------------------------------------------------------------

_ENTRYPOINT_ABI = json.loads("""[
    {"inputs":[
        {"components":[
            {"name":"sender","type":"address"},
            {"name":"nonce","type":"uint256"},
            {"name":"initCode","type":"bytes"},
            {"name":"callData","type":"bytes"},
            {"name":"callGasLimit","type":"uint256"},
            {"name":"verificationGasLimit","type":"uint256"},
            {"name":"preVerificationGas","type":"uint256"},
            {"name":"maxFeePerGas","type":"uint256"},
            {"name":"maxPriorityFeePerGas","type":"uint256"},
            {"name":"paymasterAndData","type":"bytes"},
            {"name":"signature","type":"bytes"}
        ],"name":"userOp","type":"tuple"},
        {"name":"beneficiary","type":"address"}
    ],"name":"handleOps","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"userOp","type":"bytes"}],"name":"getUserOpHash","outputs":[{"name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"getNonce","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[
        {"components":[
            {"name":"sender","type":"address"},
            {"name":"nonce","type":"uint256"},
            {"name":"initCode","type":"bytes"},
            {"name":"callData","type":"bytes"},
            {"name":"callGasLimit","type":"uint256"},
            {"name":"verificationGasLimit","type":"uint256"},
            {"name":"preVerificationGas","type":"uint256"},
            {"name":"maxFeePerGas","type":"uint256"},
            {"name":"maxPriorityFeePerGas","type":"uint256"},
            {"name":"paymasterAndData","type":"bytes"},
            {"name":"signature","type":"bytes"}
        ],"name":"userOp","type":"tuple"}
    ],"name":"simulateValidation","outputs":[],"stateMutability":"nonpayable","type":"function"}
]""")

_SMART_ACCOUNT_ABI = json.loads("""[
    {"inputs":[],"name":"owner","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"nonce","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[
        {"name":"dest","type":"address"},
        {"name":"value","type":"uint256"},
        {"name":"data","type":"bytes"}
    ],"name":"execute","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[
        {"name":"dest1","type":"address"},
        {"name":"data1","type":"bytes"},
        {"name":"dest2","type":"address"},
        {"name":"data2","type":"bytes"}
    ],"name":"executeBatch","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"usdcBalance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
]""")

_SMART_ACCOUNT_FACTORY_ABI = json.loads("""[
    {"inputs":[{"name":"tgId","type":"uint256"},{"name":"owner","type":"address"}],"name":"createAccount","outputs":[{"name":"account","type":"address"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"tgId","type":"uint256"}],"name":"getAddress","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"tgId","type":"uint256"}],"name":"isDeployed","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"}
]""")

_PAYMASTER_ABI = json.loads("""[
    {"inputs":[{"name":"owner","type":"address"}],"name":"setOwner","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"owner","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"}
]""")

_ERC20_ABI = json.loads("""[
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transferFrom","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}
]""")

# Per-operation calldata prefix for the SmartAccount.execute() selector.
# keccak256("execute(address,uint256,bytes)")[:4]
_ENCODE_EXECUTE_SELECTOR = "b61d27f6"
_EXECUTE_SELECTOR = bytes.fromhex(_ENCODE_EXECUTE_SELECTOR)
# keccak256("executeBatch(address,bytes,address,bytes)")[:4]
_ENCODE_EXECUTE_BATCH_SELECTOR = "7c7652c8"
_EXECUTE_BATCH_SELECTOR = bytes.fromhex(_ENCODE_EXECUTE_BATCH_SELECTOR)


def _get_w3() -> Web3:
    """Get the Web3 instance from the base layer."""
    from . import base
    return base.w3


def _entrypoint():
    w3 = _get_w3()
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.SMART_WALLET_ENTRYPOINT),
        abi=_ENTRYPOINT_ABI,
    )


def _factory():
    w3 = _get_w3()
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.SMART_WALLET_FACTORY_ADDRESS),
        abi=_SMART_ACCOUNT_FACTORY_ABI,
    )


def _smart_account(address: str):
    w3 = _get_w3()
    return w3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=_SMART_ACCOUNT_ABI,
    )


def _paymaster():
    w3 = _get_w3()
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.SMART_WALLET_PAYMASTER_ADDRESS),
        abi=_PAYMASTER_ABI,
    )


def _usdc():
    w3 = _get_w3()
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.USDC_ADDRESS),
        abi=_ERC20_ABI,
    )


# ---------------------------------------------------------------------------
# Address prediction
# ---------------------------------------------------------------------------

def predict_address(tg_id: int) -> str:
    """Compute the deterministic SmartAccount address for tg_id (no on-chain call)."""
    f = _factory()
    addr = f.functions.getAddress(tg_id).call()
    return Web3.to_checksum_address(addr)


def is_deployed(tg_id: int) -> bool:
    """Check if the SmartAccount is already deployed on-chain."""
    f = _factory()
    return f.functions.isDeployed(tg_id).call()


# ---------------------------------------------------------------------------
# Account creation
# ---------------------------------------------------------------------------

def create_account_sync(tg_id: int) -> str:
    """Deploy a SmartAccount for tg_id via the factory (sync, from_key).

    Returns the deployed address. The relayer (hot wallet) pays gas.
    """
    w3 = _get_w3()
    acct = w3.eth.account.from_key(config.HOT_WALLET_KEY)
    f = _factory()
    # The SmartAccount owner is the relayer (bot hot wallet) that signs
    # UserOperations and executes handleOps. NOT the EntryPoint.
    owner_addr = Web3.to_checksum_address(acct.address)

    tx = f.functions.createAccount(tg_id, owner_addr).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "gas": 300_000,
        "maxFeePerGas": w3.eth.gas_price,
        "maxPriorityFeePerGas": w3.to_wei("0.01", "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt["status"] != 1:
        raise RuntimeError(f"SmartAccount deploy reverted: {tx_hash.hex()}")

    addr = predict_address(tg_id)
    log.info("SmartAccount deployed for tg_id=%s at %s (tx=%s)", tg_id, addr, tx_hash.hex())
    return addr


async def create_account(tg_id: int) -> str:
    """Async wrapper for create_account_sync."""
    return await asyncio.to_thread(create_account_sync, tg_id)


# ---------------------------------------------------------------------------
# UserOperation building
# ---------------------------------------------------------------------------

def _build_user_op(
    sender: str,
    call_data: bytes,
    paymaster_and_data: bytes,
    *,
    call_gas_limit: int = 200_000,
    verification_gas_limit: int = 100_000,
    pre_verification_gas: int = 50_000,
) -> dict:
    """Build a UserOperation dict."""
    w3 = _get_w3()
    block = w3.eth.get_block("latest")
    base_fee = block.get("baseFeePerGas", w3.to_wei("0.01", "gwei"))
    priority = w3.to_wei("0.01", "gwei")
    max_fee = base_fee * 2 + priority

    return {
        "sender": Web3.to_checksum_address(sender),
        "nonce": 0,  # will be updated
        "initCode": b"",
        "callData": call_data,
        "callGasLimit": call_gas_limit,
        "verificationGasLimit": verification_gas_limit,
        "preVerificationGas": pre_verification_gas,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
        "paymasterAndData": paymaster_and_data,
        "signature": b"",
    }


def _encode_execute(dest: str, value: int, data: bytes) -> bytes:
    """Encode SmartAccount.execute(dest, value, data)."""
    return _EXECUTE_SELECTOR + abi_encode(
        ["address", "uint256", "bytes"],
        [Web3.to_checksum_address(dest), value, data],
    )


def _encode_execute_batch(dest1: str, data1: bytes, dest2: str, data2: bytes) -> bytes:
    """Encode SmartAccount.executeBatch(dest1, data1, dest2, data2)."""
    return _EXECUTE_BATCH_SELECTOR + abi_encode(
        ["address", "bytes", "address", "bytes"],
        [
            Web3.to_checksum_address(dest1), data1,
            Web3.to_checksum_address(dest2), data2,
        ],
    )


# ---------------------------------------------------------------------------
# UserOperation signing
# ---------------------------------------------------------------------------

def _sign_user_op(user_op: dict, key_hex: str) -> bytes:
    """Sign a UserOperation with the given private key."""
    ep = _entrypoint()
    user_op_hash = ep.functions.getUserOpHash(
        _pack_user_op(user_op)
    ).call()
    # Sign as an EIP-191 "Ethereum Signed Message" so SmartAccount.validateUserOp
    # (which prefixes with \x19Ethereum Signed Message:\n32) recovers the owner.
    signed = Account.sign_message(
        encode_defunct(primitive=user_op_hash), key_hex
    )
    return signed.signature


def _pack_user_op(op: dict) -> tuple:
    """Pack UserOperation for EntryPoint calls."""
    return (
        op["sender"],
        op["nonce"],
        op["initCode"],
        op["callData"],
        op["callGasLimit"],
        op["verificationGasLimit"],
        op["preVerificationGas"],
        op["maxFeePerGas"],
        op["maxPriorityFeePerGas"],
        op["paymasterAndData"],
        op["signature"],
    )


def _user_op_hash_input(user_op: dict) -> tuple:
    """Pack UserOperation for getUserOpHash (without signature)."""
    return (
        user_op["sender"],
        user_op["nonce"],
        user_op["initCode"],
        user_op["callData"],
        user_op["callGasLimit"],
        user_op["verificationGasLimit"],
        user_op["preVerificationGas"],
        user_op["maxFeePerGas"],
        user_op["maxPriorityFeePerGas"],
        user_op["paymasterAndData"],
    )


# ---------------------------------------------------------------------------
# Paymaster signature
# ---------------------------------------------------------------------------

def _sign_paymaster(user_op_hash: bytes, tg_id: int, key_hex: str) -> bytes:
    """Sign userOpHash for the paymaster (relayer key)."""
    signed = Account.sign_message(
        encode_defunct(primitive=user_op_hash), key_hex
    )
    return signed.signature


def _build_paymaster_data(tg_id: int, relayer_sig: bytes) -> bytes:
    """Build paymasterAndData: 20 bytes paymaster addr + 32 bytes context + 65 bytes sig."""
    paymaster_addr = bytes.fromhex(
        Web3.to_checksum_address(config.SMART_WALLET_PAYMASTER_ADDRESS)[2:]
    )
    tg_id_bytes = tg_id.to_bytes(32, "big")
    return paymaster_addr + tg_id_bytes + relayer_sig


# ---------------------------------------------------------------------------
# High-level operations
# ---------------------------------------------------------------------------

def approve_and_trade_sync(
    tg_id: int,
    market_address: str,
    approve_amount: int,
    trade_data: bytes,
) -> str:
    """Build and send a UserOp that approves USDC + executes a trade.

    Uses executeBatch: approve(market, amount) + execute(market, 0, tradeData).
    Returns the tx hash from handleOps.
    """
    w3 = _get_w3()
    acct = w3.eth.account.from_key(config.HOT_WALLET_KEY)

    smart_addr = predict_address(tg_id)
    usdc_addr = Web3.to_checksum_address(config.USDC_ADDRESS)
    market_addr = Web3.to_checksum_address(market_address)
    relayer_key = config.SMART_WALLET_RELAYER_KEY or config.HOT_WALLET_KEY

    # Build approve calldata (USDC.approve(market, amount))
    approve_data = _usdc().functions.approve(
        market_addr, approve_amount
    ).build_transaction({"from": smart_addr})["data"]

    # Build batch: approve(market) then trade(market) — both are calls made
    # BY the SmartAccount, dispatched through executeBatch.
    batch_data = _encode_execute_batch(
        usdc_addr, approve_data,
        market_addr, trade_data,
    )

    # Nonce: the SmartAccount's own sequential nonce (storage, starts at 0).
    nonce = _smart_account(smart_addr).functions.nonce().call()

    # Build paymaster data
    # For now, empty paymaster (direct execution without paymaster sponsorship)
    paymaster_data = b""

    user_op = _build_user_op(
        sender=smart_addr,
        call_data=batch_data,
        paymaster_and_data=paymaster_data,
        call_gas_limit=400_000,
        verification_gas_limit=150_000,
    )
    user_op["nonce"] = nonce

    # Sign with relayer key
    user_op["signature"] = _sign_user_op(user_op, relayer_key)

    # Send via bundler (or direct handleOps for now)
    ep = _entrypoint()
    tx = ep.functions.handleOps(
        [_pack_user_op(user_op)],
        acct.address,
    ).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "gas": 1_000_000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei("0.01", "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        raise RuntimeError(f"UserOp reverted: {tx_hash.hex()}")

    return "0x" + tx_hash.hex()


async def approve_and_trade(
    tg_id: int,
    market_address: str,
    approve_amount: int,
    trade_data: bytes,
) -> str:
    """Async wrapper."""
    return await asyncio.to_thread(
        approve_and_trade_sync, tg_id, market_address, approve_amount, trade_data
    )


# ---------------------------------------------------------------------------
# Balance queries
# ---------------------------------------------------------------------------

def smart_balance(tg_id: int) -> int:
    """USDC balance (micro) of the user's SmartAccount."""
    smart_addr = predict_address(tg_id)
    return _usdc().functions.balanceOf(Web3.to_checksum_address(smart_addr)).call()


def smart_nonce(tg_id: int) -> int:
    """Current on-chain nonce of the SmartAccount for tg_id."""
    smart_addr = predict_address(tg_id)
    return _smart_account(smart_addr).functions.nonce().call()
