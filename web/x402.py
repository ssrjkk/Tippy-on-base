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
import asyncio
import logging
import re
import time
import uuid
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from fastapi import Request
from fastapi.responses import JSONResponse

from bot import base, config, tip_targets
from bot.base import hot_wallet
from bot.ledger import async_ledger as ledger
from web import x402_spec

log = logging.getLogger('web.x402')

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

def _invoice_response(amount_micro: int, resource: str = '', description: str = '', error: str = 'payment required', **extra) -> JSONResponse:
    body = {'detail': error, 'amount_usdc': round(amount_micro / MICRO, 2), 'pay_to': str(_x402_receive_address() or hot_wallet()), 'expires_in_seconds': PAYMENT_TTL_SECONDS, **extra}
    if resource:
        # Official x402 shape (v1, scheme "exact") alongside the legacy keys:
        # x402-spec agents read accepts[], first-generation clients keep
        # reading detail/pay_to.
        body.update(x402_spec.invoice_body(amount_micro, resource, description or resource, error=error))
    return JSONResponse(status_code=402, headers=_invoice_headers(amount_micro), content=body)

def _payment_rejected_response(amount_micro: int, resource: str = '', reason: str = 'payment not found or too small') -> JSONResponse:
    body = {'detail': reason, 'expected_amount_usdc': round(amount_micro / MICRO, 2)}
    if resource:
        body.update(x402_spec.invoice_body(amount_micro, resource, resource, error=reason))
    return JSONResponse(status_code=402, headers=_invoice_headers(amount_micro), content=body)

async def _run_spec_payment(request, tg_id: int, amount_micro: int, resource: str, settle_and_credit):
    """Official x402 flow (X-PAYMENT header, scheme "exact", EIP-3009).

    settle_and_credit(nonce, settlement_tx, payer) -> bool runs the
    endpoint-specific crediting after a successful on-chain settlement.

    Returns (status_code, body, headers) or None when no X-PAYMENT header
    is present (the legacy tx-hash flow then applies)."""
    xp = (request.headers.get('X-PAYMENT') or '').strip()
    if not xp:
        return None
    try:
        decoded = x402_spec.decode_payment_header(xp)
    except ValueError as e:
        return 400, {'detail': str(e)}, {}
    auth = decoded['auth']
    signature = decoded['signature']
    nonce = 'auth:' + auth['nonce'].hex()
    if await ledger.x402_paid(nonce):
        return 409, {'detail': 'payment already processed'}, {}
    reserved = await ledger.reserve_x402_auth(nonce, tg_id, amount_micro, auth['from'])
    if not reserved:
        return 409, {'detail': 'payment already processed'}, {}
    receive = str(_x402_receive_address() or '')
    try:
        sender = x402_spec.verify_eip3009(auth, signature, receive, amount_micro)
    except ValueError as e:
        await ledger.release_x402_auth(nonce)
        body = x402_spec.invoice_body(amount_micro, resource, resource, error=str(e))
        return 402, body, {}
    try:
        settlement = await asyncio.to_thread(
            x402_spec.settle_eip3009, auth, signature, receive
        )
    except x402_spec.UncertainSettlement as e:
        # The broadcast result is ambiguous: the settlement tx may land. The
        # reservation row MUST stay so this payment cannot be re-signed and
        # re-settled; support reconciles by the known tx hash.
        log.error('x402 settlement UNCERTAIN tx=%s nonce=%s — reservation kept', e.tx_hash, nonce)
        body = x402_spec.invoice_body(
            amount_micro, resource, resource,
            error=f'settlement uncertain (tx {e.tx_hash}) — this payment must not be retried',
        )
        return 402, body, {}
    except Exception as e:
        # Confirmed revert: no money moved, the on-chain nonce was NOT burned.
        await ledger.release_x402_auth(nonce)
        log.warning('x402 settlement failed: %s', e)
        body = x402_spec.invoice_body(amount_micro, resource, resource, error=f'settlement failed: {e}')
        return 402, body, {}
    settlement_tx = settlement['tx']
    settled_value = settlement['value']
    if settled_value > amount_micro:
        log.info('x402 overpay: expected=%s settled=%s tx=%s', amount_micro, settled_value, settlement_tx)
    try:
        if not await settle_and_credit(nonce, settlement_tx, sender, settled_value):
            return 409, {'detail': 'payment already processed'}, {}
    except Exception as e:
        # The settlement tx is REAL on-chain money; a failed credit here is
        # the one case needing manual reconciliation — scream in the log.
        # The finalized row (keyed by the settlement tx) is deliberately kept.
        log.error('x402 credit failed AFTER settlement tx=%s: %s', settlement_tx, e)
        return 502, {'detail': 'settled on-chain, credit failed — contact support with this message'}, {}
    receipt = x402_spec.payment_response(settlement_tx, sender)
    return 200, {'status': 'ok', 'settlement': {'transaction': settlement_tx, 'payer': sender, 'amount_micro': settled_value}}, {'X-PAYMENT-RESPONSE': receipt}

async def reconcile_stale_x402(older_than_seconds: int = 600) -> int:
    """Sweep stale EIP-3009 reservations ('auth:<nonce>' rows).

    - Settlement landed on-chain (AuthorizationUsed burned the nonce): the
      row is finalized with the ACTUAL settled value and the recipient is
      credited — this repairs the 502 'settled but credit failed' case and
      the uncertain-broadcast case.
    - On-chain state says the nonce was never burned: the reservation is
      released and the payer may re-sign.
    - Unknown (RPC failure): the row is kept for the next sweep.

    Returns the number of finalized rows."""
    stale = await ledger.x402_auth_reservations(older_than_seconds)
    print('DEBUG reconcile: ledger=', type(ledger).__module__, 'stale=', len(stale))
    finalized = 0
    for row in stale:
        payer = row['sender']
        try:
            nonce = bytes.fromhex(row['tx_hash'].split(':', 1)[1])
        except (ValueError, IndexError):
            await ledger.release_x402_auth(row['tx_hash'])
            continue
        receive = str(_x402_receive_address() or '')
        if not receive:
            continue
        burned = await asyncio.to_thread(x402_spec.authorization_burned, payer, nonce)
        if burned is None:
            continue  # RPC down — retry next sweep
        if not burned:
            # Never settled on-chain: free the payer to re-sign.
            await ledger.release_x402_auth(row['tx_hash'])
            continue
        found = await asyncio.to_thread(
            x402_spec.find_settlement_by_nonce, payer, nonce, receive
        )
        if not found:
            # Nonce burned but the settlement tx predates our scan window —
            # keep the row; a wider scan can be run manually.
            log.warning('x402 reconcile: nonce burned but tx not found for %s', row['tx_hash'])
            continue
        # The row key is the STRING 'auth:<hex>' — tx_hash is TEXT; passing
        # raw bytes here would compare text = bytea and match nothing.
        ok = await ledger.finalize_x402_credit(
            row['tx_hash'], found['tx'], int(row['recipient_tg']), found['value'], payer
        )
        if ok:
            finalized += 1
            log.info('x402 reconcile: finalized %s -> %s (%s micro)', row['tx_hash'], found['tx'], found['value'])
    return finalized


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

    # Official x402 (X-PAYMENT header, scheme "exact"): verify + settle + credit.
    resource = f'/api/x402/tip?recipient={recipient}&amount={amount_micro / MICRO:g}'
    invoice = lambda error='payment required': _invoice_response(
        amount_micro, resource=resource, description=resource, error=error
    )

    async def settle_and_credit(nonce, settlement_tx, payer, settled_value):
        return await ledger.finalize_x402_credit(
            nonce, settlement_tx, tg_id, settled_value, payer
        )

    spec = await _run_spec_payment(request, tg_id, amount_micro, resource, settle_and_credit)
    if spec is not None:
        status, body, headers = spec
        headers.setdefault('X-CONTENT-TYPE-OPTIONS', 'nosniff')
        return JSONResponse(status_code=status, content=body, headers=headers)

    tx_hash = (request.headers.get('x-402-payment') or '').strip().lower()
    if not tx_hash:
        return invoice()
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
        return _payment_rejected_response(amount_micro, resource=resource)
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
    # Official x402 (X-PAYMENT header, scheme "exact").
    resource = f'/api/x402/paywall?item={raw_item}&amount={amount_micro / MICRO:g}'
    invoice = lambda error='payment required', amount=price_micro: _invoice_response(
        amount, resource=resource, description=resource, error=error, item=raw_item
    )
    if amount_micro < price_micro:
        return _invoice_response(price_micro, item=raw_item)

    async def settle_and_credit(nonce, settlement_tx, payer, settled_value):
        return await ledger.finalize_x402_paywall(
            nonce, settlement_tx, owner_tg, int(raw_item), settled_value, payer
        )

    spec = await _run_spec_payment(request, owner_tg, price_micro, resource, settle_and_credit)
    if spec is not None:
        status, body, headers = spec
        if status == 200:
            # x402 agents pay for the CONTENT: the 200 must carry it.
            body['content'] = item['content']
            body['item'] = {'id': int(raw_item), 'title': item['title'],
                            'amount_usdc': round(price_micro / MICRO, 2)}
        headers.setdefault('X-CONTENT-TYPE-OPTIONS', 'nosniff')
        return JSONResponse(status_code=status, content=body, headers=headers)

    tx_hash = (request.headers.get('x-402-payment') or '').strip().lower()
    if not tx_hash:
        return invoice()
    if not _TX_HASH_RE.match(tx_hash):
        return JSONResponse(status_code=400, content={'detail': 'invalid x-402-payment header'})
    if await ledger.x402_paid(tx_hash):
        return JSONResponse(status_code=409, content={'detail': 'payment already processed'})
    if await ledger.pending_deposit_exists(tx_hash):
        return JSONResponse(status_code=400, content={'detail': 'transaction is a deposit, not an x402 payment'})
    verified = _verify_payment(tx_hash, price_micro)
    if verified is None:
        return _payment_rejected_response(price_micro, resource=resource)
    res = await ledger.x402_paywall_purchase(owner_tg, int(raw_item), tx_hash, price_micro, verified['sender'])
    if res == 'replay':
        return JSONResponse(status_code=409, content={'detail': 'payment already processed'})
    return JSONResponse(status_code=200, content={'status': 'ok', 'item': {'id': int(raw_item), 'title': item['title'], 'amount_usdc': round(verified['amount_micro'] / MICRO, 2), 'sender': verified['sender'], 'tx_hash': tx_hash}, 'content': item['content']})

async def _resolve_recipient(recipient: str) -> int | None:
    # Basenames first (`name.base.eth` -> address -> the owning Tippy user).
    bn_id, _err = await tip_targets.resolve_tip_target(recipient)
    if bn_id is not None:
        return bn_id
    if recipient.isdigit():
        return int(recipient) if await ledger.user_exists(int(recipient)) else None
    return await ledger.find_by_username(recipient.lstrip('@'))
