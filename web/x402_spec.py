"""Official x402 protocol (v1, scheme "exact") compatibility layer.

Coinbase's x402 (https://x402.org) is the payment protocol their agentic
stack speaks: Agentic Wallets, Payments MCP and third-party x402 agents all
send an `X-PAYMENT` header carrying an EIP-3009 `transferWithAuthorization`
signature for the exact invoice amount, and expect the invoice itself as a
structured 402 body (`x402Version`, `accepts[]`) plus an
`X-PAYMENT-RESPONSE` receipt header on success.

This module adapts our /api/x402/* endpoints to that protocol:

    invoice  -> official-shaped 402 body (accepts: scheme "exact",
                networkId "base", payTo, asset = native USDC, extra EIP-712
                domain name/version for FiatTokenV2)
    payment  -> decode X-PAYMENT (base64 JSON), verify the EIP-712
                authorization (signer, payTo, amount, validity window),
                settle it OURSELVES as facilitator: the hot wallet executes
                USDC.transferWithAuthorization, which moves the funds to the
                receive address and burns the nonce on-chain.

Replay protection is three layers deep: the on-chain nonce (AuthorizationUsed),
a reserved auth-nonce row in x402_payments (DB, race-proof across processes),
and the settlement tx hash as the ledger PK.

The asset name/version defaults match native USDC on Base (FiatTokenV2);
tests override them for the MiniUSDC stand-in.
"""

import base64
import json
import logging
import time

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from bot import config

log = logging.getLogger("web.x402.spec")

X402_VERSION = 1
SCHEME = "exact"
NETWORK_ID = "base"
MAX_TIMEOUT_SECONDS = 60

# EIP-3009 typehash used by FiatTokenV2 (native USDC on Base).
TRANSFER_WITH_AUTHORIZATION_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}


def _asset() -> tuple[str, str]:
    """EIP-712 domain name/version of the settlement asset.

    Defaults match native USDC on Base (FiatTokenV2). Tests override for the
    MiniUSDC stand-in via env (read at call time so monkeypatching works).
    """
    import os

    return (
        os.environ.get("X402_ASSET_NAME", "USD Coin"),
        os.environ.get("X402_ASSET_VERSION", "2"),
    )


def invoice_accepts(amount_micro: int, resource: str, description: str) -> list[dict]:
    name, version = _asset()
    return [
        {
            "scheme": SCHEME,
            "networkId": NETWORK_ID,
            "maxAmountRequired": str(amount_micro),
            "resource": resource,
            "description": description,
            "mimeType": "application/json",
            "payTo": str(config.X402_RECEIVE_ADDRESS or "").strip(),
            "asset": config.USDC_ADDRESS,
            "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
            "extra": {"name": name, "version": version},
        }
    ]


def invoice_body(amount_micro: int, resource: str, description: str, error: str = "payment required") -> dict:
    """Official-shaped 402 JSON body. Legacy detail keys (detail/pay_to/...)
    are kept alongside so first-generation clients keep working."""
    return {
        "x402Version": X402_VERSION,
        "error": error,
        "accepts": invoice_accepts(amount_micro, resource, description),
        "x402_pay_to": str(config.X402_RECEIVE_ADDRESS or "").strip(),
        "x402_amount_micro": str(amount_micro),
    }


def decode_payment_header(raw: str) -> dict:
    """Decode the official X-PAYMENT header (base64 JSON) and validate the
    envelope. Raises ValueError with a short reason on any deviation."""
    try:
        decoded = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
    except Exception as e:
        raise ValueError("X-PAYMENT is not valid base64 JSON") from e
    if not isinstance(decoded, dict):
        raise ValueError("X-PAYMENT must be a JSON object")
    if decoded.get("x402Version") != X402_VERSION:
        raise ValueError(f"unsupported x402Version: {decoded.get('x402Version')}")
    if decoded.get("scheme") != SCHEME:
        raise ValueError(f"unsupported scheme: {decoded.get('scheme')}")
    if str(decoded.get("networkId", "")).lower() != NETWORK_ID:
        raise ValueError(f"unsupported networkId: {decoded.get('networkId')}")
    payload = decoded.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("missing payload")
    auth = payload.get("authorization")
    if not isinstance(auth, dict):
        raise ValueError("missing payload.authorization")
    signature = payload.get("signature")
    if not isinstance(signature, str) or not signature.startswith("0x") or len(signature) < 130:
        raise ValueError("missing or malformed payload.signature")
    for field in ("from", "to", "nonce"):
        if not isinstance(auth.get(field), str) or not auth[field]:
            raise ValueError(f"missing authorization.{field}")
    for field in ("value", "validAfter", "validBefore"):
        try:
            auth[field] = int(auth[field])
        except (TypeError, ValueError) as e:
            raise ValueError(f"authorization.{field} is not an integer") from e
    try:
        auth["nonce"] = bytes.fromhex(auth["nonce"].removeprefix("0x")).rjust(32, b"\x00")[-32:]
    except Exception as e:
        raise ValueError("authorization.nonce is not hex") from e
    if auth["nonce"] == b"\x00" * 32:
        raise ValueError("authorization.nonce must not be zero")
    return {"payment": decoded, "auth": auth, "signature": signature}


def verify_eip3009(auth: dict, signature: str, pay_to: str, expected_micro: int) -> str:
    """Verify the EIP-3009 authorization against the invoice. Returns the
    payer address. Raises ValueError with a short reason on any mismatch.

    The EIP-712 domain is built for the settlement asset (native USDC on
    Base by default: name "USD Coin", version "2", chainId, verifyingContract).
    """
    receive = str(pay_to).lower()
    if str(auth["to"]).lower() != receive:
        raise ValueError("authorization.to is not the x402 receive address")
    if int(auth["value"]) < expected_micro:
        raise ValueError(
            f"authorization.value {auth['value']} is below the invoice amount {expected_micro}"
        )
    now = int(time.time())
    if now <= int(auth["validAfter"]):
        raise ValueError("authorization is not yet valid")
    if now >= int(auth["validBefore"]):
        raise ValueError("authorization has expired")
    if int(auth["validBefore"]) - now > MAX_TIMEOUT_SECONDS + 300:
        raise ValueError("authorization validity window is too long")

    name, version = _asset()
    chain_id = getattr(config, "EXPECTED_CHAIN_ID", 8453) or 8453
    # Accept both the decoded form (nonce as bytes) and a raw hex string.
    nonce = auth["nonce"]
    if isinstance(nonce, str):
        nonce = bytes.fromhex(nonce.removeprefix("0x")).rjust(32, b"\x00")[-32:]
    try:
        typed = encode_typed_data(
            domain_data={
                "name": name,
                "version": version,
                "chainId": chain_id,
                "verifyingContract": Web3.to_checksum_address(config.USDC_ADDRESS),
            },
            message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
            message_data={
                "from": auth["from"],
                "to": auth["to"],
                "value": int(auth["value"]),
                "validAfter": int(auth["validAfter"]),
                "validBefore": int(auth["validBefore"]),
                "nonce": "0x" + nonce.hex(),
            },
        )
        signer = Account.recover_message(typed, signature=signature)
    except Exception as e:
        raise ValueError(f"signature verification failed: {e}") from e
    if signer.lower() != str(auth["from"]).lower():
        raise ValueError("signature does not match authorization.from")
    return signer


def _normalize_signature(signature: str) -> str:
    return signature if signature.startswith("0x") else "0x" + signature


# transferWithAuthorization(v,r,s) — the FiatTokenV2 overload. Not part of
# the bot's ERC20_ABI, so the settlement builds its own handle.
EIP3009_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
            {"name": "v", "type": "uint8"},
            {"name": "r", "type": "bytes32"},
            {"name": "s", "type": "bytes32"},
        ],
        "name": "transferWithAuthorization",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


class UncertainSettlement(Exception):
    """send_raw_transaction failed AMBIGUOUSLY (timeout after the node
    accepted, connection drop). The tx hash is known; the settlement may or
    may not land. The caller MUST NOT free the authorization reservation —
    the payment may still be settled on-chain and crediting it later is the
    only correct outcome."""

    def __init__(self, tx_hash: str):
        super().__init__(f"settlement broadcast uncertain for tx {tx_hash}")
        self.tx_hash = tx_hash


def settle_eip3009(auth: dict, signature: str, pay_to: str) -> dict:
    """Facilitate the payment: the hot wallet executes
    USDC.transferWithAuthorization, moving `value` to the receive address and
    burning the nonce on-chain.

    Returns {"tx": settlement_hash, "value": actually_transferred_micro}.
    `value` can exceed the invoice amount (hand-rolled clients may overpay) —
    the caller must credit the ACTUAL transferred amount, never less.

    Raises UncertainSettlement when the broadcast result is ambiguous (the
    caller must keep the reservation and reconcile), and RuntimeError on a
    confirmed revert (nonce burned already / blacklisted payer — no money
    moved, the reservation can be freed).

    Runs on the SAME w3 the rest of the bot uses (base.w3), so tests can
    rebind it to a local EVM with a MiniUSDC stand-in.
    """
    from bot import base

    base.assert_base_chain_sync()
    w3 = base.w3
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(config.USDC_ADDRESS), abi=EIP3009_ABI
    )
    acct = w3.eth.account.from_key(config.HOT_WALLET_KEY)
    nonce = w3.eth.get_transaction_count(acct.address, "pending")
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    priority = w3.to_wei("0.01", "gwei")
    tx = usdc.functions.transferWithAuthorization(
        Web3.to_checksum_address(auth["from"]),
        Web3.to_checksum_address(auth["to"]),
        int(auth["value"]),
        int(auth["validAfter"]),
        int(auth["validBefore"]),
        "0x" + auth["nonce"].hex(),
        # The signature is a packed 65-byte rsv string; split for the (v,r,s)
        # FiatTokenV2 overload. EIP-155 v is normalised to 27/28 for ecrecover.
        _v(signature),
        _r(signature),
        _s(signature),
    ).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "gas": 120_000,
        "maxFeePerGas": base_fee * 2 + priority,
        "maxPriorityFeePerGas": priority,
        "chainId": w3.eth.chain_id,
    })
    signed = acct.sign_transaction(tx)
    # Pre-compute the hash from the signed payload: deterministically known
    # even if the broadcast result is not.
    tx_hash = "0x" + Web3.keccak(signed.raw_transaction).hex()
    from bot.base import _send_lock  # shared hot-wallet send lock

    with _send_lock:
        try:
            w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception:
            raise UncertainSettlement(tx_hash) from None
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if not receipt.get("status"):
        raise RuntimeError("settlement reverted (nonce already used, or blacklisted payer)")
    # The settled value is what the token actually moved to the receive
    # address — parse the receipt instead of trusting the authorization.
    receive = pay_to.lower()
    settled = 0
    for lg in receipt.get("logs", []):
        if str(lg.get("address", "")).lower() != config.USDC_ADDRESS.lower():
            continue
        try:
            ev = w3.eth.contract(
                address=Web3.to_checksum_address(config.USDC_ADDRESS), abi=TRANSFER_EVENT_ABI
            ).events.Transfer().process_log(lg)
        except Exception:
            continue
        if str(ev["args"]["to"]).lower() == receive:
            settled += int(ev["args"]["value"])
    if settled == 0:
        raise RuntimeError("settlement confirmed but no USDC moved to the receive address")
    return {"tx": tx_hash, "value": settled}


TRANSFER_EVENT_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    }
]


# The 65-byte signature arrives as "0x" + 130 hex chars:
#   [2:66]   = r (32 bytes)
#   [66:130] = s (32 bytes)
#   [130:132]= v (1 byte, 27/28)
# All three slices MUST be taken from the FULL string — dropping the "0x"
# prefix first shifts every slice by one byte and ecrecover silently
# recovers a wrong address (every settlement would revert).
def _r(signature: str) -> bytes:
    return bytes.fromhex(signature[2:66])


def _s(signature: str) -> bytes:
    return bytes.fromhex(signature[66:130])


def _v(signature: str) -> int:
    v = int(signature[130:132], 16)
    if v not in (27, 28):
        v = v % 27 + 27  # normalise EIP-155 / 0-1 forms for ecrecover
    return v


AUTHORIZATION_USED_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "authorizer", "type": "address"},
            {"indexed": True, "name": "nonce", "type": "bytes32"},
        ],
        "name": "AuthorizationUsed",
        "type": "event",
    }
]


def find_settlement_by_nonce(payer: str, nonce: bytes, pay_to: str,
                             from_block: int | None = None) -> dict | None:
    """Reconciliation: locate an already-burned authorization's settlement.

    After an UNCERTAIN broadcast the reservation row stays keyed
    'auth:<nonce>' with no tx hash. This scans AuthorizationUsed events for
    (payer, nonce) and parses the USDC Transfer in the same receipt.

    Returns {"tx", "value"} or None when the nonce is NOT burned (the payer
    may re-sign). Raises nothing — RPC failures return None and the next
    sweep retries.
    """
    from bot import base

    try:
        w3 = base.w3
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(config.USDC_ADDRESS),
            abi=EIP3009_ABI + AUTHORIZATION_USED_ABI + TRANSFER_EVENT_ABI,
        )
        latest = w3.eth.get_block("latest")["number"]
        start = from_block or max(0, latest - 200_000)
        payer_topic = "0x" + Web3.to_checksum_address(payer).lower().replace("0x", "").rjust(64, "0")
        nonce_topic = "0x" + nonce.hex()
        logs = w3.eth.get_logs({
            "fromBlock": start,
            "toBlock": latest,
            "address": Web3.to_checksum_address(config.USDC_ADDRESS),
            "topics": [
                w3.solidity_keccak(["string"], ["AuthorizationUsed(address,address,bytes32)"]).hex(),
                payer_topic,
                nonce_topic,
            ],
        })
        for lg in logs:
            rcpt = w3.eth.wait_for_transaction_receipt(lg["transactionHash"], timeout=30)
            if not rcpt.get("status"):
                continue
            settled = 0
            for tl in rcpt.get("logs", []):
                if str(tl.get("address", "")).lower() != config.USDC_ADDRESS.lower():
                    continue
                try:
                    ev = usdc.events.Transfer().process_log(tl)
                except Exception:
                    continue
                if str(ev["args"]["to"]).lower() == pay_to.lower():
                    settled += int(ev["args"]["value"])
            if settled:
                return {"tx": "0x" + lg["transactionHash"].hex(), "value": settled}
        return None
    except Exception as e:
        log.warning("x402 reconciliation scan failed: %s", e)
        return None


AUTH_STATE_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "authorizer", "type": "address"},
            {"name": "nonce", "type": "bytes32"},
        ],
        "name": "authorizationState",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def authorization_burned(payer: str, nonce: bytes) -> bool | None:
    """True/False when the on-chain state is readable, None when the RPC
    fails (the caller must treat None as 'do not release')."""
    from bot import base

    try:
        w3 = base.w3
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(config.USDC_ADDRESS),
            abi=EIP3009_ABI + AUTH_STATE_ABI,
        )
        return usdc.functions.authorizationState(
            Web3.to_checksum_address(payer), nonce
        ).call()
    except Exception as e:
        log.warning("authorizationState read failed: %s", e)
        return None


def payment_response(settlement_tx: str, payer: str) -> str:
    """Official X-PAYMENT-RESPONSE receipt header (base64 JSON)."""
    return base64.b64encode(json.dumps({
        "success": True,
        "transaction": settlement_tx,
        "network": NETWORK_ID,
        "payer": payer,
    }).encode("utf-8")).decode("utf-8")
