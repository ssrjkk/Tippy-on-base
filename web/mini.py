"""Telegram Mini App backend: authenticated actions from inside Telegram.

The app opens in a Telegram webview; the client sends ``Telegram.WebApp.initData``
to ``POST /api/mini/auth``, which verifies the WebAppData HMAC (the official
algorithm from https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app),
then issues the same signed session cookie the dashboard uses. All further
``/api/mini/*`` calls are authorized by that cookie only.
"""
import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
        return int(data['user'].split('"id":')[1].split(',')[0].strip('"'))
    except (KeyError, IndexError, ValueError):
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
        import json as _json
        u = _json.loads(raw_user)
        username = u.get('username')
    except Exception:
        pass
    await ledger.ensure_user(tg_id, username)
    resp = JSONResponse({'ok': True, 'tg_id': tg_id})
    resp.set_cookie(COOKIE_NAME, make_session(tg_id), max_age=SESSION_TTL_SECONDS,
                    httponly=True, samesite='lax', secure=request.url.scheme == 'https')
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
    top = [{'username': r.get('username') or f"id{r['tg_id']}", 'total_usdc': _fmt(r['total_micro'])} for r in await ledger.leaderboard(5)]
    lang = (await ledger.get_settings(tg_id)).get('lang', 'ru')
    return {'tg_id': tg_id, 'username': await ledger.username_of(tg_id), 'balance_usdc': float(await ledger.balance(tg_id)), 'deposit_address': str(base.hot_wallet()), 'linked_address': await ledger.linked_address(tg_id), 'lang': lang, 'markets': markets, 'bets': bets, 'history': history, 'top': top}

class TipBody(BaseModel):
    to: str
    amount: float
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
    if target is None:
        raise HTTPException(404, f'user @{to} not found — they must open the bot first')
    if target == tg_id:
        raise HTTPException(400, 'cannot tip yourself')
    if not await ledger.transfer(tg_id, target, micro):
        raise HTTPException(400, 'insufficient balance')
    return {'ok': True, 'new_balance': float(await ledger.balance(tg_id))}

class TradeBody(BaseModel):
    market_id: int
    option: int
    amount: float

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
    amount: float

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
