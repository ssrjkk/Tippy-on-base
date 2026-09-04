"""Telegram Mini App backend: authenticated actions from inside Telegram.

The app opens in a Telegram webview; the client sends ``Telegram.WebApp.initData``
to ``POST /api/mini/auth``, which verifies the WebAppData HMAC (the official
algorithm from https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app),
then issues the same signed session cookie the dashboard uses. All further
``/api/mini/*`` calls are authorized by that cookie only.
"""
import asyncio
import hashlib
import hmac
import json as _json
import logging
import time
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bot import base, config
from bot.ledger import async_ledger as ledger
from web.auth import COOKIE_NAME, SESSION_TTL_SECONDS, make_session, parse_session

router = APIRouter()
log = logging.getLogger('web.mini')
MICRO = 10 ** config.USDC_DECIMALS
INIT_DATA_TTL = 24 * 3600

def verify_init_data(init_data: str) -> int:
    """Validate Telegram Mini App initData, return tg_id."""
    if not init_data or '=' not in init_data:
        raise HTTPException(403, 'missing initData')
    pairs = [p.split('=', 1) for p in init_data.split('&') if '=' in p]
    data: dict[str, str] = dict(pairs)
    received_hash = data.pop('hash', '')
    if not received_hash:
        raise HTTPException(403, 'missing hash')
    check_string = '\n'.join(f'{k}={data[k]}' for k in sorted(data))
    secret = hmac.new(b'WebAppData', config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(403, 'bad signature')
    try:
        auth_date = int(data.get('auth_date', '0'))
    except ValueError:
        raise HTTPException(403, 'bad auth_date') from None
    if time.time() - auth_date > INIT_DATA_TTL:
        raise HTTPException(403, 'stale initData')
    try:
        # initData is form-urlencoded: the `user` value is a percent-encoded
        # JSON object ({"id":123,...} never appears literally). The HMAC above
        # stays over the raw encoded string, exactly as Telegram signs it.
        user = _json.loads(urllib.parse.unquote(data['user']))
        return int(user['id'])
    except (KeyError, IndexError, ValueError, TypeError):
        raise HTTPException(403, 'missing user') from None

class InitAuth(BaseModel):
    initData: str

async def _user(request: Request) -> int:
    tg_id = parse_session(request.cookies.get(COOKIE_NAME))
    if tg_id is None:
        raise HTTPException(401, 'not logged in')
    await ledger.ensure_user(tg_id, None)
    return tg_id

def _fmt(micro: int) -> float:
    return round(micro / MICRO, 2)

@router.post('/api/mini/auth', tags=['auth'])
async def mini_auth(body: InitAuth, request: Request):
    tg_id = verify_init_data(body.initData)
    username = None
    try:
        raw_user = next((v for k, v in (p.split('=', 1) for p in body.initData.split('&')) if k == 'user'))
        u = _json.loads(urllib.parse.unquote(raw_user))
        username = u.get('username')
    except Exception:
        pass
    await ledger.ensure_user(tg_id, username)
    resp = JSONResponse({'ok': True, 'tg_id': tg_id})
    resp.set_cookie(COOKIE_NAME, make_session(tg_id), max_age=SESSION_TTL_SECONDS,
                    httponly=True, samesite='lax',
                    secure=request.url.scheme == 'https'
                    or request.headers.get('x-forwarded-proto', '').lower() == 'https')
    return resp

@router.get('/api/mini/state', tags=['users'])
async def mini_state(request: Request) -> dict:
    """Everything the main screen needs, in one call."""
    tg_id = await _user(request)
    markets = []
    for m in await ledger.open_markets(6):
        view = await ledger.amm_market_view(int(m['id']))
        if view:
            markets.append({'id': view['id'], 'question': view['question'], 'close_at': view['close_at'], 'traders': view['traders'], 'options': [{'index': o['index'], 'label': o['label'], 'price_pct': o['price_pct']} for o in view['options']]})
    bets = []
    for b in await ledger.bets_by_status('open', 6):
        totals = await ledger.bet_totals(int(b['id']))
        import json as _json
        options = _json.loads(b['options'])
        pot = sum(totals.values())
        bets.append({'id': int(b['id']), 'question': b['question'], 'creator': int(b['creator']), 'pot_usdc': _fmt(pot), 'options': [{'index': i, 'label': lbl, 'pool_usdc': _fmt(totals.get(i, 0)), 'chance_pct': round(100 * totals.get(i, 0) / pot, 1) if pot else 0.0} for i, lbl in enumerate(options)]})
    history = [{'kind': r['kind'], 'amount': _fmt(r['amount']), 'note': r['note'] or '', 'counterparty': r['counterparty'] or '', 'created_at': r['created_at']} for r in await ledger.history(tg_id, 10)]
    from bot import tip_targets
    top_rows = await ledger.leaderboard(5)
    top = []
    for r in top_rows:
        name = r.get('username')
        if not name:
            try:
                name = await tip_targets.display_name_for(int(r['tg_id']))
            except Exception:
                name = None
        top.append({'username': name or f"id{r['tg_id']}", 'total_usdc': _fmt(r['total_micro'])})
    lang = (await ledger.get_settings(tg_id)).get('lang', 'ru')
    from bot import onchain_market as om
    onchain = await om.market_views(8)
    return {'tg_id': tg_id, 'username': await ledger.username_of(tg_id), 'balance_usdc': float(await ledger.balance(tg_id)), 'deposit_address': str(base.hot_wallet()), 'linked_address': await ledger.linked_address(tg_id), 'lang': lang, 'markets': markets, 'bets': bets, 'onchain_markets': onchain, 'history': history, 'top': top}

def _smart_wallet_enabled() -> bool:
    """True when the ERC-4337 stack is fully configured (factory + paymaster)."""
    return (
        config.SMART_WALLET_ENABLED
        and bool(config.SMART_WALLET_FACTORY_ADDRESS)
        and bool(config.SMART_WALLET_PAYMASTER_ADDRESS)
    )

@router.get('/api/mini/smartwallet', tags=['wallet'])
async def mini_smartwallet(request: Request) -> dict:
    """P2: the user's deterministic SmartAccount (ERC-4337) and on-chain USDC.

    Returns the counterfactual address, whether it is deployed, the on-chain
    USDC balance (0 when not deployed), and the paymaster sponsorship flag.
    Gated: only when SMART_WALLET_ENABLED and factory+paymaster are configured;
    otherwise a 503 so the Mini App can hide the section.
    """
    tg_id = await _user(request)
    if not _smart_wallet_enabled():
        raise HTTPException(503, 'smart wallet not enabled')
    from bot import smart_wallet as sw

    address = await asyncio.to_thread(sw.predict_address, tg_id)
    deployed = await asyncio.to_thread(sw.is_deployed, tg_id)
    balance_micro = await asyncio.to_thread(sw.smart_balance, tg_id) if deployed else 0
    nonce = await asyncio.to_thread(sw.smart_nonce, tg_id) if deployed else 0
    return {
        'enabled': True,
        'address': address,
        'deployed': deployed,
        'balance_usdc': round(balance_micro / MICRO, 2),
        'nonce': nonce,
        'paymaster_sponsored': True,
        'deposit_address': address,
    }

class TipBody(BaseModel):
    to: str
    amount: float = Field(allow_inf_nan=False)
_ERR_MSG = {'closed': 'market closed', 'deadline': 'deadline passed', 'badopt': 'no such option', 'balance': 'insufficient balance'}

@router.post('/api/mini/tip', tags=['users'])
async def mini_tip(body: TipBody, request: Request) -> dict:
    tg_id = await _user(request)
    micro = round(body.amount * MICRO)
    if micro <= 0:
        raise HTTPException(400, 'amount must be positive')
    to = body.to.strip().lstrip('@')
    target = await ledger.find_by_username(to)
    if target is None and to.isdigit():
        target = int(to)
    if target is None or not await ledger.user_exists(target):
        # Never mint a phantom user for a mistyped id: transfer() would create
        # one and the funds would be locked there forever (and inflate the
        # public Proof-of-Reserves liabilities).
        raise HTTPException(404, f'user @{to} not found — they must open the bot first')
    if target == tg_id:
        raise HTTPException(400, 'cannot tip yourself')
    if not await ledger.transfer(tg_id, target, micro):
        raise HTTPException(400, 'insufficient balance')
    return {'ok': True, 'new_balance': float(await ledger.balance(tg_id))}

class TradeBody(BaseModel):
    market_id: int
    option: int
    amount: float = Field(allow_inf_nan=False)

@router.post('/api/mini/trade', tags=['markets'])
async def mini_trade(body: TradeBody, request: Request) -> dict:
    tg_id = await _user(request)
    micro = round(body.amount * MICRO)
    if micro <= 0:
        raise HTTPException(400, 'amount must be positive')
    status, info = await ledger.buy_shares(body.market_id, tg_id, body.option, micro)
    if status != 'ok':
        raise HTTPException(400, _ERR_MSG.get(status, status))
    bal = float(await ledger.balance(tg_id))
    pos = await ledger.user_market_position(body.market_id, tg_id) or {}
    return {'ok': True, 'info': info, 'new_balance': bal, 'position': pos}

class BetPlaceBody(BaseModel):
    bet_id: int
    option: int
    amount: float = Field(allow_inf_nan=False)

@router.post('/api/mini/betplace', tags=['markets'])
async def mini_betplace(body: BetPlaceBody, request: Request) -> dict:
    tg_id = await _user(request)
    micro = round(body.amount * MICRO)
    if micro <= 0:
        raise HTTPException(400, 'amount must be positive')
    res = await ledger.place_bet(body.bet_id, tg_id, body.option, micro)
    if res != 'ok':
        raise HTTPException(400, _ERR_MSG.get(res, res))
    return {'ok': True, 'new_balance': float(await ledger.balance(tg_id))}

@router.get('/api/mini/onchain/{market_id}', tags=['markets'])
async def mini_onchain_market(market_id: int, request: Request) -> dict:
    """On-chain market detail for the Mini App: registry labels, live prices,
    contract state AND the viewer's own ERC-1155 balances (read from their
    active wallet, auth required) so the UI can show personal positions."""
    tg_id = await _user(request)
    from bot import onchain_market as om
    m = await ledger.get_onchain_market(market_id)
    if not m:
        raise HTTPException(404, 'on-chain market not found')
    options = _json.loads(m['options'])
    prices = await om.market_prices(market_id, len(options))
    info = await om.get_market_info(market_id)
    w = await ledger.get_active_wallet(tg_id)
    balances: list[int] = []
    if w:
        def _bals():
            c = om._market_contract(om._w3())
            cs = om.Web3.to_checksum_address(w['address'])
            return [c.functions.balanceOf(cs, market_id * 256 + i).call() for i in range(len(options))]

        balances = await asyncio.to_thread(_bals)
    return {
        'id': market_id,
        'question': m['question'],
        'close_at': m['close_at'],
        'wallet_address': w['address'] if w else None,
        'resolved': bool(info['resolved']),
        'cancelled': bool(info['cancelled']),
        'disputed': bool(info['disputed']),
        'winner': info['winning_outcome'],
        'escrow_micro': int(info['escrow_micro']),
        'options': [
            {
                'index': i,
                'label': o,
                'price_pct': float(round(prices[i] * 100, 2)),
                'shares': balances[i] if i < len(balances) else 0,
            }
            for i, o in enumerate(options)
        ],
    }


class CreateBody(BaseModel):
    kind: str
    question: str
    options: list[str]
    hours: float | None = None
    subsidy_usdc: float = 10.0

def _parse_deadline(hours: float | None) -> int | None:
    if not hours or hours <= 0:
        return None
    return int(time.time() + min(hours, 30 * 24) * 3600)

@router.post('/api/mini/create', tags=['markets'])
async def mini_create(body: CreateBody, request: Request) -> dict:
    tg_id = await _user(request)
    question = body.question.strip()
    options = [o.strip() for o in body.options if o.strip()]
    if len(question) < 5 or len(options) < 2:
        raise HTTPException(400, 'question too short or fewer than 2 options')
    if len(options) > 4 or max(len(o) for o in options) > 64:
        raise HTTPException(400, 'max 4 options, 64 chars each')
    if len(question) > 200:
        raise HTTPException(400, 'question too long (max 200 chars)')
    close_at = _parse_deadline(body.hours)
    if body.kind == 'market':
        subsidy_micro = round(body.subsidy_usdc * MICRO)
        if subsidy_micro < 1:
            raise HTTPException(400, 'subsidy too small')
        mid = await ledger.create_market(tg_id, question, options, subsidy_micro, close_at=close_at)
        return {'ok': True, 'id': mid}
    if body.kind == 'bet':
        bid = await ledger.create_bet(tg_id, question, options, close_at=close_at)
        return {'ok': True, 'id': bid}
    raise HTTPException(400, 'kind must be market or bet')

class LangBody(BaseModel):
    lang: str

@router.post('/api/mini/lang', tags=['users'])
async def mini_lang(body: LangBody, request: Request) -> dict:
    tg_id = await _user(request)
    if body.lang not in ('ru', 'en', 'zh'):
        raise HTTPException(400, 'unsupported language')
    await ledger.set_setting(tg_id, 'lang', body.lang)
    return {'ok': True, 'lang': body.lang}

def public_base_url() -> str:
    """https://host part where the Mini App lives (used for WebApp buttons).

    Prefers MINI_APP_URL (dedicated, works in polling mode too), then
    WEBHOOK_URL. Falls back to a http://HOST:PORT that Telegram will reject,
    logging a clear warning so the misconfiguration is obvious.
    """
    for cand in (config.MINI_APP_URL, config.WEBHOOK_URL):
        if cand:
            base = '/'.join(str(cand).split('/')[:3]).rstrip('/')
            if base.startswith(('http://', 'https://')):
                return base
    log.warning(
        'MINI_APP_URL / WEBHOOK_URL not set — WebApp button will use '
        'http://%s:%s, which Telegram rejects (https required). Set '
        'MINI_APP_URL=https://your-public-host', config.WEB_HOST, config.WEB_PORT)
    return f'http://{config.WEB_HOST}:{config.WEB_PORT}'
