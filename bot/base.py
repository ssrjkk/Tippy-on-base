"""Base network layer: USDC balance, deposit scanning, withdrawals."""

import threading
import time
from decimal import ROUND_CEILING, Decimal

from eth_account.messages import encode_defunct
from eth_typing import ChecksumAddress
from web3 import Web3

from . import config
from .ledger import ledger

# RPC timeout: a hung provider must not freeze the deposit watcher (deposits
# would silently stop) or a dashboard request. web3 has no default timeout.
w3 = Web3(Web3.HTTPProvider(config.BASE_RPC_URL, request_kwargs={"timeout": config.RPC_TIMEOUT_SECONDS}))

HOT_WALLET = Web3.to_checksum_address(w3.eth.account.from_key(config.HOT_WALLET_KEY).address)
USDC = Web3.to_checksum_address(config.USDC_ADDRESS)
usdc = w3.eth.contract(address=USDC, abi=config.ERC20_ABI)

_send_lock = threading.Lock()

# Address "0x0000...0000" = mint events; "0x0000...0001" = burn (weird EdgeCase)
EDGE_1 = Web3.to_checksum_address("0x0000000000000000000000000000000000000001")

_transfer_topic = w3.keccak(text="Transfer(address,address,uint256)").hex()


def hot_wallet() -> ChecksumAddress:
    return HOT_WALLET


def hot_balance() -> float:
    micro = usdc.functions.balanceOf(HOT_WALLET).call()
    return micro / 10**config.USDC_DECIMALS


def vault_balance() -> float | None:
    """On-chain USDC held by the TipBotVault treasury, or None if not deployed.

    This is the on-chain proof-of-reserves: anyone can re-verify it directly
    on Base (USDC.balanceOf(vault) == totalReserves()).
    """
    if not config.VAULT_ADDRESS:
        return None
    micro = usdc.functions.balanceOf(Web3.to_checksum_address(config.VAULT_ADDRESS)).call()
    return micro / 10**config.USDC_DECIMALS


def _scan_deposits(from_block: int, to_block: int) -> list[dict]:
    """Return incoming USDC transfers to the hot wallet."""
    logs = w3.eth.get_logs(
        {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": USDC,
            "topics": [_transfer_topic, None, f"0x{'0'*24}{HOT_WALLET[2:].lower()}"],
        }
    )
    out = []
    for log in logs:
        try:
            event = usdc.events.Transfer().process_log(log)
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


def recover_signer(message: str, signature: str) -> str:
    """Recover the address that signed `message` (ETH personal_sign)."""
    return w3.eth.account.recover_message(
        encode_defunct(text=message), signature=signature
    )


def withdraw_fee(amount_micro: int) -> int:
    """Withdrawal fee (config, default 1%), at least 1 micro-unit."""
    fee = (Decimal(amount_micro) * config.WITHDRAW_FEE_PCT).to_integral_value(rounding=ROUND_CEILING)
    return max(int(fee), 1)


def poll_deposits() -> list[dict]:
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
    current = w3.eth.block_number
    last = ledger.last_block()
    if last == 0:
        last = current - config.DEPOSIT_SCAN_LOOKBACK_BLOCKS
    if current <= last:
        return credited
    start = max(1, min(last + 1, current - config.DEPOSIT_CONFIRM_BLOCKS))
    for dep in _scan_deposits(start, current):
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
    ledger.set_last_block(current)
    return credited


def check_pending_withdraws() -> None:
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
        total_micro = amount_micro + withdraw_fee(amount_micro)
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
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            receipt = None
        if receipt is None:
            if now - int(row["created_at"]) > config.WITHDRAW_STUCK_TIMEOUT_SECONDS:
                ledger.refund_withdraw(wd_id, int(row["tg_id"]), total_micro)
        elif bool(receipt.get("status")):
            ledger.mark_withdraw_done(wd_id, tx_hash)
        else:
            ledger.refund_withdraw(wd_id, int(row["tg_id"]), total_micro)


def send_usdc(to_address: str, amount_micro: int) -> str:
    """Send USDC from hot wallet. Returns tx hash. Raises on failure.

    Serialized by a process lock so two concurrent withdrawals never pick the
    same nonce (which would silently replace one tx with the other).
    """
    acct = w3.eth.account.from_key(config.HOT_WALLET_KEY)
    with _send_lock:
        nonce = w3.eth.get_transaction_count(HOT_WALLET, "pending")
        base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
        # Priority tip 0.01 gwei (Base's practical floor; 0.001 gwei can be too
        # low and leave a withdrawal stuck until the watcher refunds it). The
        # max fee leaves ~2x headroom over the current base fee so a short fee
        # spike right after building the tx doesn't orphan it.
        priority = w3.to_wei("0.01", "gwei")
        max_fee = base_fee * 2 + priority
        tx = usdc.functions.transfer(
            Web3.to_checksum_address(to_address), amount_micro
        ).build_transaction(
            {
                "from": HOT_WALLET,
                "nonce": nonce,
                "maxPriorityFeePerGas": priority,
                "maxFeePerGas": max_fee,
                "chainId": w3.eth.chain_id,
            }
        )
        signed = acct.sign_transaction(tx)
        return "0x" + w3.eth.send_raw_transaction(signed.raw_transaction).hex()


async def kick_expired_channel_subscriptions(bot) -> int:
    """Kick users whose paid channel access expired. Returns the number kicked.

    A subscription row is dropped ONLY when the user is provably gone (left,
    kicked, chat deleted) or is an admin of the channel. If the bot lost
    admin rights (or the network failed) the row is kept and the kick retries
    next cycle — otherwise a dropped row would leave the user inside the
    channel for free forever, and a re-purchase would silently re-arm access
    they already had.
    """
    from aiogram.exceptions import TelegramBadRequest

    now = time.time()
    kicked = 0
    for sub in ledger.active_channel_subscriptions():
        if int(sub["expires_at"]) > now:
            continue
        chat_id, tg_id = int(sub["chat_id"]), int(sub["tg_id"])
        try:
            member = await bot.get_chat_member(chat_id, tg_id)
            if member.status in ("administrator", "creator"):
                ledger.expire_channel_subscription(chat_id, tg_id)
                continue
        except Exception:
            pass  # probe may fail — the ban itself decides below
        try:
            await bot.ban_chat_member(chat_id, tg_id)
            await bot.unban_chat_member(chat_id, tg_id)
        except TelegramBadRequest as e:
            msg = str(getattr(e, "message", ""))
            if ("not found" in msg or "NOT_PARTICIPANT" in msg
                    or "chat not found" in msg or "CHAT_NOT_FOUND" in msg):
                # the user is no longer in the channel (or it is gone)
                ledger.expire_channel_subscription(chat_id, tg_id)
            # otherwise (bot lost admin rights, ...) — keep the row, retry later
            continue
        except Exception:
            continue  # network hiccup — keep the row, retry later
        kicked += 1
        ledger.expire_channel_subscription(chat_id, tg_id)
        try:
            await bot.send_message(
                tg_id,
                "🔑 Подписка на канал истекла, доступ закрыт.\n"
                "Продлить: /paywall channels",
            )
        except Exception:
            pass
    return kicked
