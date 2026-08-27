"""Transaction lifecycle: receipts, quick status, USDC-transfer decoding."""

import asyncio
import time

from web3 import Web3

from . import core


def wait_for_tx_sync(tx_hash: str, timeout: float = 60.0, poll: float = 2.0) -> dict | None:
    """Poll until `tx_hash` is mined (or timeout). Returns receipt summary.

    {'status': bool, 'block_number': int, 'gas_used': int,
     'effective_gas_price_gwei': float} — None on timeout / unknown hash.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = core.w3.eth.get_transaction_receipt(tx_hash)
            gas_price = int(r.get("effectiveGasPrice") or 0)
            return {
                "status": bool(r.get("status")),
                "block_number": int(r["blockNumber"]),
                "gas_used": int(r["gasUsed"]),
                "effective_gas_price_gwei": gas_price / 1e9,
            }
        except Exception:
            time.sleep(poll)
    return None


async def wait_for_tx(tx_hash: str, timeout: float = 60.0, poll: float = 2.0) -> dict | None:
    """Async: wait for a receipt off the event loop."""
    return await asyncio.to_thread(wait_for_tx_sync, tx_hash, timeout, poll)


def _tx_status_sync(tx_hash: str) -> str:
    try:
        r = core.w3.eth.get_transaction_receipt(tx_hash)
        return "success" if r.get("status") else "failed"
    except Exception:
        pass
    try:
        core.w3.eth.get_transaction(tx_hash)
        return "pending"  # known but not mined yet
    except Exception:
        return "unknown"


async def tx_status(tx_hash: str) -> str:
    """Async quick status: 'pending' | 'success' | 'failed' | 'unknown'."""
    return await asyncio.to_thread(_tx_status_sync, tx_hash)


def _tx_info_sync(tx_hash: str) -> dict | None:
    """Fetch a transaction and decode a USDC transfer out of its input data.

    Returns {'hash', 'from', 'to', 'status', 'value_micro', 'usdc_to'} —
    value/usdc_to are None when the tx is not a plain USDC transfer, status
    is None while the tx is not mined yet.
    """
    try:
        tx = core.w3.eth.get_transaction(tx_hash)
    except Exception:
        return None
    receipt = None
    try:
        receipt = core.w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        pass  # not mined yet
    out = {
        "hash": tx_hash,
        "from": str(tx["from"]),
        "to": str(tx["to"]) if tx["to"] else None,
        "status": bool(receipt.get("status")) if receipt is not None else None,
        "value_micro": None,
        "usdc_to": None,
    }
    # decode transfer(address,uint256) selector 0xa9059cbb
    raw = tx.get("input") or b""
    data = bytes(raw).hex() if isinstance(raw, (bytes, bytearray)) else str(raw)
    data = data.lower().removeprefix("0x")
    if data.startswith("a9059cbb") and len(data) >= 8 + 64 * 2 and out["to"] and out["to"].lower() == core.USDC.lower():
        to_addr = "0x" + data[8 + 24 : 8 + 64][-40:]
        value = int(data[8 + 64 : 8 + 128], 16)
        out["usdc_to"] = Web3.to_checksum_address(to_addr)
        out["value_micro"] = value
    return out


async def tx_info(tx_hash: str) -> dict | None:
    """Async: decode a transaction (off the event loop)."""
    return await asyncio.to_thread(_tx_info_sync, tx_hash)
