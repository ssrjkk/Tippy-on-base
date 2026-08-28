"""x402 endpoint: AI agents pay USDC tips over HTTP.

Protocol (https://github.com/coinbase/x402):
  1. Agent POSTs /api/x402/tip?recipient=<user>&amount=<usdc> with no
     payment header -> 402 Payment Required + x-402-* headers describing
     the invoice (recipient address, amount in micro-units, expiry).
  2. Agent sends USDC on Base to that address.
  3. Agent repeats the request with `x-402-payment: <tx_hash>`.
  4. We verify the Transfer on-chain (USDC -> our address), credit the tip
     to the Telegram recipient, and return 200. Same tx_hash twice -> 409.

The tx hash is the PK of x402_payments and the deposit scanner skips it,
so a payment is never credited twice and liabilities stay exact.
"""
import logging
import re
import time
import uuid
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from fastapi import Request
from fastapi.responses import JSONResponse

from bot import base, config
from bot.base import hot_wallet

log = logging.getLogger('web.x402')
from bot.ledger import async_ledger as ledger

MICRO = 10 ** config.USDC_DECIMALS
MAX_TIP_USDC = 1000
PAYMENT_TTL_SECONDS = 300
_TX_HASH_RE = re.compile('^0x[0-9a-f]{64}$')


def _x402_receive_address() -> str | None:
    """Return the dedicated x402 receive address.

    Must differ from the deposit hot wallet to prevent deposit-tx replay.
    """
    addr = config.X402_RECEIVE_ADDRESS.strip()
    if addr and addr.lower() != hot_wallet().lower():
        return addr
    return None


def _invoice_headers(amount_micro: int) -> dict:
    receive = _x402_receive_address() or hot_wallet()
    return {'x-402-recipient': receive, 'x-402-amount': str(amount_micro), 'x-402-expires-at': str(int(time.time()) + PAYMENT_TTL_SECONDS), 'x-402-idempotency-key': str(uuid.uuid4())}

def _verify_payment(tx_hash: str, expected_micro: int) -> dict | None:
    """Read the USDC Transfer to our x402 receive address from the tx receipt.

    Returns {"sender", "amount_micro"} or None when the payment is missing,
    reverted, too small, sent to the deposit hot wallet (not x402), or the
    RPC is unreachable.
    """
    try:
        receipt = base.w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return None
    if not receipt or not bool(receipt.get('status')):
        return None
    pay_to = (_x402_receive_address() or hot_wallet()).lower()
    total = 0
    sender = None
    for log in receipt.get('logs', []):
        if str(log.get('address', '')).lower() != config.USDC_ADDRESS.lower():
            continue
        try:
            ev = base.usdc.events.Transfer().process_log(log)
        except Exception:
            continue
        args = ev['args']
        if args['to'].lower() == pay_to:
            total += int(args['value'])
            if sender is None:
                sender = args['from']
    if total != expected_micro or sender is None:
        if total > 0:
            # Real money hit the receive address but does not settle this
            # invoice (wrong amount, or split across senders). Without a
            # trace it would be stuck forever with no reconciliation hint.
            log.warning(
                'x402 unmatched payment: tx=%s expected=%s got=%s sender=%s '
                '(funds are in the receive address — reconcile manually)',
                tx_hash, expected_micro, total, sender,
            )
        return None
    return {'sender': sender, 'amount_micro': total}

def _parse_amount(raw_amount: str) -> int | None:
    """Parse a USDC amount from the query string into micro-units or None.

    Decimal (not float): no binary rounding surprises; fractional micros are
    rounded up like everywhere else in the bot (_to_micro).
    """
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > MAX_TIP_USDC:
        return None
    return int((amount * MICRO).to_integral_value(rounding=ROUND_CEILING))

def _invoice_response(amount_micro: int, **extra) -> JSONResponse:
    return JSONResponse(status_code=402, headers=_invoice_headers(amount_micro), content={'detail': 'payment required', 'amount_usdc': round(amount_micro / MICRO, 2), 'pay_to': str(_x402_receive_address() or hot_wallet()), 'expires_in_seconds': PAYMENT_TTL_SECONDS, **extra})

def _payment_rejected_response(amount_micro: int) -> JSONResponse:
    return JSONResponse(status_code=402, headers=_invoice_headers(amount_micro), content={'detail': 'payment not found or too small', 'expected_amount_usdc': round(amount_micro / MICRO, 2)})

async def x402_tip(request: Request) -> JSONResponse:
    """POST /api/x402/tip?recipient=<username|tg_id>&amount=<usdc>"""
    if not config.X402_ENABLED or _x402_receive_address() is None:
        return JSONResponse(status_code=503, content={'detail': 'x402 payments are disabled (set X402_RECEIVE_ADDRESS)'})
    q = request.query_params
    recipient = (q.get('recipient') or '').strip()
    amount_micro = _parse_amount((q.get('amount') or '').strip())
    if not recipient or amount_micro is None:
        return JSONResponse(status_code=400, content={'detail': 'recipient and amount are required'})
    tg_id = await _resolve_recipient(recipient)
    if tg_id is None:
        return JSONResponse(status_code=404, content={'detail': 'unknown recipient'})
    tx_hash = (request.headers.get('x-402-payment') or '').strip().lower()
    if not tx_hash:
        return _invoice_response(amount_micro, recipient=recipient)
    if not _TX_HASH_RE.match(tx_hash):
        return JSONResponse(status_code=400, content={'detail': 'invalid x-402-payment header'})
    if await ledger.x402_paid(tx_hash):
        return JSONResponse(status_code=409, content={'detail': 'payment already processed'})
    if await ledger.pending_deposit_exists(tx_hash):
        return JSONResponse(status_code=400, content={'detail': 'transaction is a deposit, not an x402 payment'})
    verified = _verify_payment(tx_hash, amount_micro)
    if verified is None:
        # Reject payments sent to the deposit hot wallet — those are regular
        # deposits, not x402 payments. This closes the redirect/race drain.
        hot = hot_wallet().lower()
        try:
            receipt = base.w3.eth.get_transaction_receipt(tx_hash)
            for log in (receipt or {}).get('logs', []):
                if str(log.get('address', '')).lower() == config.USDC_ADDRESS.lower():
                    try:
                        ev = base.usdc.events.Transfer().process_log(log)
                        if ev['args']['to'].lower() == hot:
                            return JSONResponse(status_code=400, content={'detail': 'tx is a deposit to the hot wallet, not an x402 payment'})
                    except Exception:
                        pass
        except Exception:
            pass
        return _payment_rejected_response(amount_micro)
    credited = await ledger.credit_x402(tg_id, tx_hash, amount_micro, verified['sender'])
    if not credited:
        return JSONResponse(status_code=409, content={'detail': 'payment already processed'})
    return JSONResponse(status_code=200, content={'status': 'ok', 'tip': {'recipient': recipient, 'amount_usdc': round(verified['amount_micro'] / MICRO, 2), 'sender': verified['sender'], 'tx_hash': tx_hash}})

async def x402_paywall(request: Request) -> JSONResponse:
    if not config.X402_ENABLED or _x402_receive_address() is None:
        return JSONResponse(status_code=503, content={'detail': 'x402 payments are disabled (set X402_RECEIVE_ADDRESS)'})
    """POST /api/x402/paywall?item=<id>&amount=<usdc>

    x402 handshake for paywall items: an agent pays the invoice on-chain and
    receives the content in the 200 response. Replay of the same tx -> 409.
    """
    q = request.query_params
    raw_item = (q.get('item') or '').strip()
    amount_micro = _parse_amount((q.get('amount') or '').strip())
    if not raw_item.isdigit() or amount_micro is None:
        return JSONResponse(status_code=400, content={'detail': 'item and amount are required'})
    item = await ledger.paywall_item(int(raw_item))
    if item is None:
        return JSONResponse(status_code=404, content={'detail': 'unknown item'})
    owner_tg = int(item['owner_tg'])
    price_micro = int(item['price_micro'])
    if amount_micro < price_micro:
        return _invoice_response(price_micro, item=raw_item)
    tx_hash = (request.headers.get('x-402-payment') or '').strip().lower()
    if not tx_hash:
        return _invoice_response(price_micro, item=raw_item)
    if not _TX_HASH_RE.match(tx_hash):
        return JSONResponse(status_code=400, content={'detail': 'invalid x-402-payment header'})
    if await ledger.x402_paid(tx_hash):
        return JSONResponse(status_code=409, content={'detail': 'payment already processed'})
    if await ledger.pending_deposit_exists(tx_hash):
        return JSONResponse(status_code=400, content={'detail': 'transaction is a deposit, not an x402 payment'})
    verified = _verify_payment(tx_hash, price_micro)
    if verified is None:
        return _payment_rejected_response(price_micro)
    res = await ledger.x402_paywall_purchase(owner_tg, int(raw_item), tx_hash, price_micro, verified['sender'])
    if res == 'replay':
        return JSONResponse(status_code=409, content={'detail': 'payment already processed'})
    return JSONResponse(status_code=200, content={'status': 'ok', 'item': {'id': int(raw_item), 'title': item['title'], 'amount_usdc': round(verified['amount_micro'] / MICRO, 2), 'sender': verified['sender'], 'tx_hash': tx_hash}, 'content': item['content']})

async def _resolve_recipient(recipient: str) -> int | None:
    if recipient.isdigit():
        return int(recipient) if await ledger.user_exists(int(recipient)) else None
    return await ledger.find_by_username(recipient.lstrip('@'))
