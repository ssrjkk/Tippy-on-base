"""Sweep USDC from per-invoice pay addresses to the x402 receive address.

Each x402 invoice derives a unique EOA that holds USDC after payment. The
sweep consolidates these funds to the shared X402_RECEIVE_ADDRESS so they are
visible to the same reconciliation logic the existing x402 flow uses.

Runs as a background task in main.py (parallel to create2_sweep_watcher).
Guarded by X402_ENABLED and X402_RECEIVE_ADDRESS being set.
"""

import asyncio
import logging

from web3 import Web3

from bot import config
from bot.chain import transfers
from bot.chain.network import eth_balance_sync
from bot.chain.tokens import token_balance_sync
from bot.ledger import async_ledger as ledger
from bot.ledger import ledger as sync_ledger

log = logging.getLogger("tipbot.x402_sweep")

# Share the gas-drip DAILY budget with on-chain market ops: the config value
# is the single cap (an operator lowering it must bound BOTH callers).
_DAILY_MAX = config.GAS_DRIP_DAILY_MAX

# Minimum ETH the derived address must receive to pay for a USDC transfer
# (~50k gas * 0.01 gwei priority = ~0.00005 ETH; add headroom).
_GAS_DRIP_WEI = int(config.GAS_DRIP_ETH * 10**18)
_GAS_NEEDED_WEI = int(config.GAS_DRIP_THRESHOLD_ETH * 10**18)


def _derive_private_key(invoice_id: str) -> str:
    from web3 import Web3
    key = (config.X402_INVOICE_KEY or config.HOT_WALLET_KEY).strip()
    seed = bytes.fromhex(key[2:])
    digest = Web3.keccak(seed + b":x402-invoice:" + invoice_id.encode("ascii"))
    return "0x" + digest.hex()


def _sweep_one(invoice_id: str, pay_addr: str, pay_to: str) -> bool:
    """Drip gas + transfer USDC from pay_addr to pay_to. Returns True on success."""
    from bot import base
    from bot.chain.core import w3

    base.assert_base_chain_sync()
    signer_key = _derive_private_key(invoice_id)
    derived_addr = Web3.to_checksum_address(pay_addr)

    # 1) Check USDC balance
    bal = token_balance_sync(derived_addr)
    if bal <= 0:
        return True  # nothing to sweep

    # 2) Drip gas if the derived address cannot pay
    eth_bal = eth_balance_sync(derived_addr)
    needed = _GAS_NEEDED_WEI
    if eth_bal * 10**18 < needed:
        if not sync_ledger.try_book_gas_drip(_DAILY_MAX):
            log.warning("x402 sweep: gas budget exhausted, skipping %s (USDC=%s)", invoice_id, bal)
            return False
        drip_hash = transfers._send_eth_sync(derived_addr, _GAS_DRIP_WEI)
        w3.eth.wait_for_transaction_receipt(drip_hash, timeout=60)

    # 3) Transfer USDC to the receive address using the derived key
    tx_hash = transfers._send_token_as_sync(signer_key, pay_to, bal)
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if not rcpt.get("status"):
        # Reverted/dropped: funds still in the derived address. Do NOT mark
        # swept or the invoice is never retried and the USDC parks forever.
        log.warning("x402 sweep: USDC transfer reverted for %s", invoice_id)
        return False
    log.info("x402 sweep: moved %s micro-USDC from %s -> %s", bal, pay_addr, pay_to)
    return True


async def sweep_all_invoices() -> int:
    """Sweep all credited but unswept x402 invoice addresses. Returns count swept."""
    if not config.X402_ENABLED or not config.X402_RECEIVE_ADDRESS:
        return 0
    receive = config.X402_RECEIVE_ADDRESS.strip()
    if not receive:
        return 0
    rows = await ledger.unswept_x402_invoices()
    swept = 0
    for row in rows:
        try:
            target = row.get("pay_to") or receive
            ok = await asyncio.to_thread(
                _sweep_one, row["invoice_id"], row["pay_addr"], target
            )
            if ok:
                await ledger.mark_x402_invoice_swept(row["invoice_id"])
                swept += 1
        except Exception:
            log.warning("x402 sweep failed for %s: %s", row["invoice_id"], exc_info=True)
    return swept
