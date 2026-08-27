"""Deposit intake & withdrawal safety sweeps (chain <-> ledger bridge).

This is the one deliberate place where the chain layer touches the ledger
singleton: deposit crediting and stuck-withdrawal refunds are business
decisions driven by on-chain facts. All reads go through core failover.
"""

import asyncio
import time

from web3 import Web3

from .. import config
from ..ledger import ledger
from . import core, transfers

# Address "0x0000...0000" = mint events; "0x0000...0001" = burn (weird EdgeCase)
EDGE_1 = Web3.to_checksum_address("0x0000000000000000000000000000000000000001")

_transfer_topic = core.w3.keccak(text="Transfer(address,address,uint256)").hex()


def _scan_deposits(from_block: int, to_block: int) -> list[dict]:
    """Return incoming USDC transfers to the hot wallet.

    Tries every configured provider in order, so a single provider outage
    can't silently drop deposits.
    """
    filter_params = {
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": core.USDC,
        "topics": [_transfer_topic, None, f"0x{'0'*24}{core.HOT_WALLET[2:].lower()}"],
    }
    last_err = None
    for provider in core._w3_providers:
        try:
            logs = provider.eth.get_logs(filter_params)
            break
        except Exception as e:
            last_err = e
            continue
    else:
        raise RuntimeError(f"all RPC providers failed for get_logs: {last_err}")
    out = []
    for log in logs:
        try:
            event = core.usdc.events.Transfer().process_log(log)
        except Exception:
            continue
        args = event["args"]
        if args["from"].lower() == EDGE_1.lower():
            continue  # skip mints
        out.append(
            {
                "sender": args["from"],
                "amount_micro": args["value"],
                "tx_hash": "0x" + log["transactionHash"].hex(),
            }
        )
    return out


def _poll_deposits_sync() -> list[dict]:
    """One sweep: record new deposits; auto-credit ones from linked wallets.

    Returns a list of newly credited deposits so the caller can notify users:
    [{"tg_id", "amount_micro", "tx_hash"}, ...].

    - Cold start (no checkpoint): backfills DEPOSIT_SCAN_LOOKBACK_BLOCKS so
      deposits that arrived during downtime are not missed.
    - Steady state: the newest DEPOSIT_CONFIRM_BLOCKS are re-scanned on every
      sweep, so a chain reorg can't make us skip a deposit.
    - record_pending / claim_for_sender are idempotent (tx_hash PK, claimed=0),
      so overlapping scans never double-credit.
    """
    credited: list[dict] = []
    current = core.w3.eth.block_number
    last = ledger.last_block()
    if last == 0:
        last = current - config.DEPOSIT_SCAN_LOOKBACK_BLOCKS
    if current <= last:
        return credited
    start = max(1, min(last + 1, current - config.DEPOSIT_CONFIRM_BLOCKS))
    # Public RPCs reject wide eth_getLogs ranges with 413, so walk the gap in
    # bounded chunks, checkpointing after each one. Idempotency of
    # record_pending/claim_for_sender makes re-scans safe.
    max_end = current
    swept = 0
    while start <= max_end and swept < config.DEPOSIT_SCAN_MAX_CHUNKS_PER_SWEEP:
        chunk_end = min(start + config.DEPOSIT_SCAN_CHUNK_BLOCKS - 1, max_end)
        for dep in _scan_deposits(start, chunk_end):
            if ledger.x402_paid(dep["tx_hash"]):
                continue  # already credited via /api/x402 — never double-credit
            ledger.record_pending(dep["tx_hash"], dep["sender"], dep["amount_micro"])
            owner = ledger.tg_id_of_address(dep["sender"])
            if owner:
                for c in ledger.claim_for_sender(owner, dep["sender"]):
                    credited.append(
                        {
                            "tg_id": owner,
                            "amount_micro": c["amount_micro"],
                            "tx_hash": c["tx_hash"],
                        }
                    )
        ledger.set_last_block(chunk_end)
        start = chunk_end + 1
        swept += 1
    return credited


async def poll_deposits() -> list[dict]:
    """Async: run one deposit sweep off the event loop."""
    return await asyncio.to_thread(_poll_deposits_sync)


def _check_pending_withdrawn_sync() -> None:
    """Refund withdrawals that never confirmed (stuck/replaced/reverted/crash).

    - status NULL (legacy rows) with a tx_hash were recorded after a successful
      send -> mark done without a receipt check (never refund a paid tx).
    - 'pending' with tx_hash=NULL = crash between debit-commit and send.
    - 'pending' with a receipt status=0 (reverted) -> refund immediately.
    - 'pending' still not mined after WITHDRAW_STUCK_TIMEOUT_SECONDS -> refund.
    """
    now = int(time.time())
    for row in ledger.pending_withdraws():
        wd_id = int(row["id"])
        amount_micro = int(row["amount"])
        total_micro = amount_micro + transfers.withdraw_fee(amount_micro)
        status = row["status"]
        tx_hash = row["tx_hash"]
        if status is None:
            ledger.mark_withdraw_done(wd_id, tx_hash or "")
            continue
        if tx_hash is None:
            if now - int(row["created_at"]) > config.WITHDRAW_STUCK_TIMEOUT_SECONDS:
                ledger.refund_withdraw(wd_id, int(row["tg_id"]), total_micro)
            continue
        try:
            receipt = core.w3.eth.get_transaction_receipt(tx_hash)
            rpc_ok = True
        except Exception:
            receipt = None
            rpc_ok = False
        if receipt is None:
            if not rpc_ok:
                # RPC unreachable: we cannot determine the tx state. Do NOT
                # refund — a successful on-chain tx would otherwise be
                # double-paid (user keeps the funds and gets a refund). Leave
                # the row pending; the next sweep re-checks once RPC recovers.
                continue
            if now - int(row["created_at"]) > config.WITHDRAW_STUCK_TIMEOUT_SECONDS:
                ledger.refund_withdraw(wd_id, int(row["tg_id"]), total_micro)
        elif bool(receipt.get("status")):
            ledger.mark_withdraw_done(wd_id, tx_hash)
        else:
            ledger.refund_withdraw(wd_id, int(row["tg_id"]), total_micro)


async def check_pending_withdraws() -> None:
    """Async: run the pending-withdrawal refund sweep off the event loop."""
    await asyncio.to_thread(_check_pending_withdrawn_sync)
