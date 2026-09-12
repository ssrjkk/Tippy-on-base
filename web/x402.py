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
PAYMENT_TTL_SECONDS = 300
_TX_HASH_RE = re.compile('^0x[0-9a-f]{64}$')


def _invoice_key() -> str:
    """Signing key the per-invoice pay addresses are derived from.

    Empty -> derive from HOT_WALLET_KEY, so each derived address' private key
    can be recomputed for the sweep (a per-invoice address is an EOA: keccak
    of the seed + bind)."""
    return (config.X402_INVOICE_KEY or config.HOT_WALLET_KEY).strip()


def _invoice_key_valid() -> bool:
    try:
        base.w3.eth.account.from_key(_invoice_key())
        return True
    except Exception:
        return False


def _derive_invoice_address(invoice_id: str) -> str:
    """Deterministically derive a unique EOA pay address for an invoice.

    address = keccak(_invoice_seed() + b':x402-invoice:' + invoice_id)[12:]
    The private key is recoverable from the seed, so the sweep can move funds
    off it. Same invoice_id -> same address (idempotent invoice issuance)."""
    from eth_account import Account
    from web3 import Web3

    seed = _invoice_seed()
    digest = Web3.keccak(seed + b':x402-invoice:' + invoice_id.encode('ascii'))
    return Account.from_key(digest).address


def _invoice_seed() -> bytes:
    key = _invoice_key()
    return bytes.fromhex(key[2:])


def _invoice_private_key(invoice_id: str) -> str:
    """Recover the private key of an invoice's derived pay address.

    Only used by the sweep (and internal tests); the client-facing code never
    needs it."""
    from web3 import Web3

    seed = _invoice_seed()
    digest = Web3.keccak(seed + b':x402-invoice:' + invoice_id.encode('ascii'))
    return '0x' + digest.hex()


def _x402_receive_address() -> str | None:
    """Return the dedicated x402 receive address.

    Must differ from the deposit hot wallet to prevent deposit-tx replay.
    """
    addr = config.X402_RECEIVE_ADDRESS.strip()
    if addr and addr.lower() != hot_wallet().lower():
        return addr
    return None


def _invoice_headers(amount_micro: int, pay_addr: str | None = None, invoice_id: str = '') -> dict:
    receive = pay_addr or (_x402_receive_address() or hot_wallet())
    headers = {
        'x-402-recipient': receive,
        'x-402-amount': str(amount_micro),
        'x-402-expires-at': str(int(time.time()) + PAYMENT_TTL_SECONDS),
        'x-402-idempotency-key': str(uuid.uuid4()),
    }
    if invoice_id:
        headers['x-402-invoice-id'] = invoice_id
    return headers


def _invoice_id(recipient_tg: int, amount_micro: int, kind: str, ref_id: str) -> str:
    """Deterministic canonical invoice id for (recipient, amount, kind, ref).

    The per-invoice pay address is derived from this: same redemption -> same
    invoice -> same address, so a client that re-asks for an invoice it was
    already issued lands on the same address (idempotent)."""
    from web3 import Web3

    bound = f'{recipient_tg}:{amount_micro}:{kind}:{ref_id}'
    return '0x' + Web3.keccak(text=bound).hex()


async def _resolve_invoice(recipient_tg: int, amount_micro: int, kind: str, ref_id: str,
                           invoice_id: str | None) -> tuple[str, str] | None:
    """Resolve the per-invoice pay address for this redemption.

    The invoice is deterministic from its canonical id; its address is a
    derived EOA bound to exactly those values, so a payment to that address is
    only ever redeemable for this recipient at this amount. Returns
    (invoice_id, pay_addr) or None if the requested invoice_id's binding does
    not match the caller's parameters (a redirected/bound-mismatched payment)."""
    if invoice_id:
        # A paying client echoes the canonical invoice id. It MUST match the
        # current redemption's binding — otherwise the payer is trying to
        # redeem funds that were minted for a DIFFERENT invoice (frontrun).
        expect = _invoice_id(recipient_tg, amount_micro, kind, ref_id)
        if invoice_id.lower() != expect.lower():
            return None
    else:
        expect = _invoice_id(recipient_tg, amount_micro, kind, ref_id)
    iid = invoice_id or expect
    addr = _derive_invoice_address(iid)
    await ledger.create_x402_invoice(iid, addr, recipient_tg, amount_micro,
                                     kind, ref_id, pay_to=_x402_receive_address() or '')
    return iid, addr


def _verify_payment(tx_hash: str, expected_micro: int, pay_to: str) -> dict | None:
    """Read the USDC Transfer to the exact per-invoice pay address from the tx.

    Returns {"sender", "amount_micro"} or None when the payment is missing,
    reverted, too small, not made to `pay_to`, or the RPC is unreachable.

    `pay_to` is the unique derived address of THIS invoice; a payment made to
    any other address (the deposit hot wallet, the shared receive address, or
    a different invoice's address) never settles here.
    """
    try:
        receipt = base.w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return None
    if not receipt or not bool(receipt.get('status')):
        return None
    pay_to = pay_to.lower()
    total = 0
    sender = None
    for entry in receipt.get('logs', []):
        if str(entry.get('address', '')).lower() != config.USDC_ADDRESS.lower():
            continue
        try:
            ev = base.usdc.events.Transfer().process_log(entry)
        except Exception:
            continue
        args = ev['args']
        if args['to'].lower() == pay_to:
            total += int(args['value'])
            if sender is None:
                sender = args['from']
    if total != expected_micro or sender is None:
        if total > 0:
            # Real money hit this invoice's pay address but does not settle it
            # (wrong amount, or split across senders). Without a trace it would
            # be stuck forever with no reconciliation hint.
            log.warning(
                'x402 unmatched payment: tx=%s expected=%s got=%s sender=%s pay_to=%s '
                '(funds are in the invoice address — reconcile manually)',
                tx_hash, expected_micro, total, sender, pay_to,
            )
        return None
    return {'sender': sender, 'amount_micro': total}

def _parse_amount(raw_amount: str) -> int | None:
    """Parse a USDC amount from the query string into micro-units or None.

    Decimal (not float): no binary rounding surprises; fractional micros are
    rounded up like everywhere else in the bot (_to_micro). Capped by the SAME
    config.MAX_TIP_USDC the Telegram handlers use (an operator lowering the
    cap must not be silently bypassed on the HTTP path), and floored so a
    payment too small to cover its own settlement gas is never quoted.
    """
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > config.MAX_TIP_USDC:
        return None
    micro = int((amount * MICRO).to_integral_value(rounding=ROUND_CEILING))
    if micro < int(getattr(config, "X402_MIN_USDC", Decimal("0.01")) * MICRO):
        return None
    return micro

def _invoice_response(amount_micro: int, resource: str = '', description: str = '', error: str = 'payment required',
                      pay_addr: str | None = None, invoice_id: str = '', **extra) -> JSONResponse:
    receive = pay_addr or (_x402_receive_address() or hot_wallet())
    body = {'detail': error, 'amount_usdc': round(amount_micro / MICRO, 2), 'pay_to': str(receive), 'expires_in_seconds': PAYMENT_TTL_SECONDS, **extra}
    if invoice_id:
        body['x-402-invoice-id'] = invoice_id
    if resource:
        # Official x402 shape (v1, scheme "exact") alongside the legacy keys:
        # x402-spec agents read accepts[], first-generation clients keep
        # reading detail/pay_to.
        body.update(x402_spec.invoice_body(amount_micro, resource, description or resource,
                                           error=error, pay_to=receive))
    return JSONResponse(status_code=402, headers=_invoice_headers(amount_micro, receive, invoice_id), content=body)

def _payment_rejected_response(amount_micro: int, resource: str = '', reason: str = 'payment not found or too small',
                               pay_addr: str | None = None, invoice_id: str = '') -> JSONResponse:
    receive = pay_addr or (_x402_receive_address() or hot_wallet())
    body = {'detail': reason, 'expected_amount_usdc': round(amount_micro / MICRO, 2)}
    if invoice_id:
        body['x-402-invoice-id'] = invoice_id
    if resource:
        body.update(x402_spec.invoice_body(amount_micro, resource, resource, error=reason, pay_to=receive))
    return JSONResponse(status_code=402, headers=_invoice_headers(amount_micro, receive, invoice_id), content=body)

async def _run_spec_payment(request, tg_id: int, amount_micro: int, resource: str,
                            settle_and_credit, pay_to: str, invoice_id: str = ''):
    """Official x402 flow (X-PAYMENT header, scheme "exact", EIP-3009).

    settle_and_credit(nonce, settlement_tx, payer, settled_value) -> bool runs
    the endpoint-specific crediting after a successful on-chain settlement.

    `pay_to` is the per-invoice unique pay address the authorization must be
    payable to — the EIP-3009 signature is only accepted for THIS invoice's
    address, so a settlement can never be redirected to another recipient.

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
    receive = str(pay_to or '')
    # Verify the signature BEFORE reserving: a garbage/malformed X-PAYMENT
    # must never create a DB row (each reserved row later costs an on-chain
    # authorizationState call during the reconcile sweep — an unauthenticated
    # endpoint must not let anyone spray that state).
    try:
        sender = await asyncio.to_thread(
            x402_spec.verify_eip3009, auth, signature, receive, amount_micro
        )
    except ValueError as e:
        body = x402_spec.invoice_body(amount_micro, resource, resource, error=str(e), pay_to=receive)
        return 402, body, {}
    reserved = await ledger.reserve_x402_auth(nonce, tg_id, amount_micro, sender, pay_to)
    if not reserved:
        return 409, {'detail': 'payment already processed'}, {}
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
            pay_to=receive,
        )
        return 402, body, {}
    except Exception as e:
        # Confirmed revert: no money moved, the on-chain nonce was NOT burned.
        await ledger.release_x402_auth(nonce)
        log.warning('x402 settlement failed: %s', e)
        body = x402_spec.invoice_body(amount_micro, resource, resource, error=f'settlement failed: {e}', pay_to=receive)
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
    finalized = 0
    for row in stale:
        payer = row['sender']
        try:
            nonce = bytes.fromhex(row['tx_hash'].split(':', 1)[1])
        except (ValueError, IndexError):
            await ledger.release_x402_auth(row['tx_hash'])
            continue
        receive = str(row.get('pay_to') or _x402_receive_address() or '')
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
        # Route the finalize by the invoice bound to this unique pay address:
        # a paywall reservation must ALSO drop a paywall_purchases row (and
        # credit the owner); a tip reservation just credits the recipient.
        # Credit is capped at the QUOTED amount (like the endpoint path): an
        # overpay in a stale-then-reconciled settlement must not over-credit.
        inv = await ledger.x402_invoice_by_addr(receive)
        owed = int(row['amount_micro'])
        credit_value = min(int(found['value']), owed)
        if inv and inv.get('kind') == 'paywall' and inv.get('ref_id') is not None:
            ok = await ledger.finalize_x402_paywall(
                row['tx_hash'], found['tx'], int(row['recipient_tg']), int(inv['ref_id']),
                credit_value, payer, receive,
            )
        else:
            # The row key is the STRING 'auth:<hex>' — tx_hash is TEXT; passing
            # raw bytes here would compare text = bytea and match nothing.
            ok = await ledger.finalize_x402_credit(
                row['tx_hash'], found['tx'], int(row['recipient_tg']), credit_value, payer, receive
            )
        if ok:
            if inv:
                # The endpoint credit path normally flips credited=true, which
                # arms the sweep for this derived address. This reservation was
                # reconciled WITHOUT that path, so flip it here or the USDC in
                # the pay address is never consolidated.
                await ledger.mark_x402_invoice_credited(inv['invoice_id'])
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

    resource = f'/api/x402/tip?recipient={recipient}&amount={amount_micro / MICRO:g}'

    # The per-invoice unique pay address: minted for (recipient, amount, kind)
    # and echoed back to the client. A payment to this address can only ever be
    # redeemed for this exact tip, so a submitter can never redirect it.
    resolved = await _resolve_invoice(tg_id, amount_micro, 'tip', '',
                                      (request.headers.get('x-402-invoice-id') or '').strip() or None)
    if resolved is None:
        # A client tried to redeem against an invoice id that does not match
        # this recipient+amount — funds minted for a different invoice (the
        # frontrun this per-invoice design closes). Refuse WITHOUT an invoice:
        # an invoice here would point the payer at the shared unbound receive
        # address and money sent there would be unredeemable (bound to no
        # invoice, never verified, never swept).
        return JSONResponse(status_code=400,
                             content={'detail': 'invoice id does not match this request'})
    invoice_id, pay_addr = resolved

    # Official x402 (X-PAYMENT header, scheme "exact"): verify + settle + credit.
    def invoice(error='payment required'):
        return _invoice_response(amount_micro, resource=resource, description=resource,
                                 error=error, pay_addr=pay_addr, invoice_id=invoice_id)

    async def settle_and_credit(nonce, settlement_tx, payer, settled_value):
        # EIP-3009 signs for the AMOUNT THE CLIENT CHOSE, which may exceed the
        # quoted invoice price. Credit the QUOTE only: an overpay stays in the
        # receive pool instead of over-crediting the recipient just because a
        # hand-rolled client authorized too much.
        credit_amount = min(int(settled_value), amount_micro)
        if credit_amount != int(settled_value):
            log.warning('x402 overpay capped at invoice price: expected=%s settled=%s tx=%s', amount_micro, settled_value, settlement_tx)
        ok = await ledger.finalize_x402_credit(
            nonce, settlement_tx, tg_id, credit_amount, payer, pay_addr
        )
        if ok:
            await ledger.mark_x402_invoice_credited(invoice_id)
        return ok

    spec = await _run_spec_payment(request, tg_id, amount_micro, resource,
                                   settle_and_credit, pay_addr, invoice_id)
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
    verified = await asyncio.to_thread(_verify_payment, tx_hash, amount_micro, pay_addr)
    if verified is None:
        # Reject payments sent to the deposit hot wallet — those are regular
        # deposits, not x402 payments. Redirection to the shared receive
        # address is refused automatically: _verify_payment only accepts the
        # per-invoice address.
        hot = hot_wallet().lower()
        try:
            receipt = await asyncio.to_thread(base.w3.eth.get_transaction_receipt, tx_hash)
            for entry in (receipt or {}).get('logs', []):
                if str(entry.get('address', '')).lower() == config.USDC_ADDRESS.lower():
                    try:
                        ev = base.usdc.events.Transfer().process_log(entry)
                        if ev['args']['to'].lower() == hot:
                            return JSONResponse(status_code=400, content={'detail': 'tx is a deposit to the hot wallet, not an x402 payment'})
                    except Exception:
                        pass
        except Exception:
            pass
        return _payment_rejected_response(amount_micro, resource=resource, pay_addr=pay_addr, invoice_id=invoice_id)
    credited = await ledger.credit_x402(tg_id, tx_hash, amount_micro, verified['sender'], pay_addr)
    if not credited:
        return JSONResponse(status_code=409, content={'detail': 'payment already processed'})
    await ledger.mark_x402_invoice_credited(invoice_id)
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

    if amount_micro < price_micro:
        return _invoice_response(price_micro, resource=resource, item=raw_item)

    resolved = await _resolve_invoice(owner_tg, price_micro, 'paywall', raw_item,
                                      (request.headers.get('x-402-invoice-id') or '').strip() or None)
    if resolved is None:
        # Mismatched invoice id (see x402_tip): refuse WITHOUT an invoice so
        # no payment is ever directed at the shared unbound receive address.
        return JSONResponse(status_code=400,
                             content={'detail': 'invoice id does not match this request'})
    invoice_id, pay_addr = resolved

    def invoice(error='payment required', amount=price_micro):
        return _invoice_response(amount, resource=resource, description=resource,
                                 error=error, item=raw_item, pay_addr=pay_addr, invoice_id=invoice_id)

    async def settle_and_credit(nonce, settlement_tx, payer, settled_value):
        # EIP-3009 signs for the AMOUNT THE CLIENT CHOSE, which may exceed the
        # quoted invoice price. Credit the QUOTE only: an overpay stays in the
        # receive pool instead of over-crediting the owner just because a
        # hand-rolled client authorized too much.
        credit_amount = min(int(settled_value), price_micro)
        if credit_amount != int(settled_value):
            log.warning('x402 paywall overpay capped at invoice price: expected=%s settled=%s tx=%s', price_micro, settled_value, settlement_tx)
        ok = await ledger.finalize_x402_paywall(
            nonce, settlement_tx, owner_tg, int(raw_item), credit_amount, payer, pay_addr
        )
        if ok:
            await ledger.mark_x402_invoice_credited(invoice_id)
        return ok

    spec = await _run_spec_payment(request, owner_tg, price_micro, resource,
                                   settle_and_credit, pay_addr, invoice_id)
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
    verified = await asyncio.to_thread(_verify_payment, tx_hash, price_micro, pay_addr)
    if verified is None:
        return _payment_rejected_response(price_micro, resource=resource, pay_addr=pay_addr, invoice_id=invoice_id)
    res = await ledger.x402_paywall_purchase(owner_tg, int(raw_item), tx_hash, price_micro, verified['sender'], pay_addr)
    if res == 'replay':
        return JSONResponse(status_code=409, content={'detail': 'payment already processed'})
    await ledger.mark_x402_invoice_credited(invoice_id)
    return JSONResponse(status_code=200, content={'status': 'ok', 'item': {'id': int(raw_item), 'title': item['title'], 'amount_usdc': round(verified['amount_micro'] / MICRO, 2), 'sender': verified['sender'], 'tx_hash': tx_hash}, 'content': item['content']})

async def _resolve_recipient(recipient: str) -> int | None:
    # Basenames first (`name.base.eth` -> address -> the owning Tippy user).
    bn_id, _err = await tip_targets.resolve_tip_target(recipient)
    if bn_id is not None:
        return bn_id
    if recipient.isdigit():
        return int(recipient) if await ledger.user_exists(int(recipient)) else None
    return await ledger.find_by_username(recipient.lstrip('@'))
