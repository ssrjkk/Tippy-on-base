"""Web dashboard: public stats, markets, leaderboard, wallet transparency.

Run:  python -m web.server
"""
import base64
import json
import logging
import os
import re
import secrets
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from bot import base, config
from bot import qr as qrlib
from bot.base import hot_balance, hot_wallet, vault_balance
from bot.ledger import async_ledger as ledger
from web.auth import COOKIE_NAME, parse_session
from web.auth import router as auth_router
from web.frame import router as frame_router
from web.hook import router as tg_webhook
from web.mini import router as mini_router
from web.x402 import x402_paywall, x402_tip

_ENABLE_OPENAPI: bool = os.environ.get('ENABLE_OPENAPI', '0') == '1'

# Validate critical secrets at import time — NOT just __main__. Every uvicorn
# / launch.py deployment imports this module, so validate() here prevents the
# silent "session key derived from BOT_TOKEN" fallback (attackers forge cookies
# for any tg_id including ADMIN).
from bot.config import validate as _validate_config

_validate_config()
log = logging.getLogger("web.server")
app = FastAPI(title='Tippy API', version='1.1.0',
    description='Public API of **Tippy** — a community economy in USDC on Base.\n\nFeatures: instant tips, Polymarket-style prediction markets (LMSR AMM), paywalled content, and **x402 HTTP payments for AI agents** (`POST /api/x402/tip`, `POST /api/x402/paywall`).\n\n* All amounts are USDC; `_usdc` fields are human-readable floats, `_micro` fields are integer micro-units (1e6 = 1 USDC).\n* `/api/solvency` is the Proof of Reserves: bot liabilities vs on-chain USDC (TipBotVault contract when deployed, else hot wallet).\n* Rate-limited per IP to protect the RPC quota.',
    contact={'name': 'ssrjkk', 'url': 'https://github.com/ssrjkk/Tippy-on-base'},
    license_info={'name': 'MIT', 'url': 'https://github.com/ssrjkk/Tippy-on-base/blob/main/LICENSE'},
    openapi_tags=[{'name': 'stats', 'description': 'Volume, users, fees, health'}, {'name': 'markets', 'description': 'Parimutuel polls and LMSR prediction markets'}, {'name': 'users', 'description': 'Leaderboards and public profiles'}, {'name': 'treasury', 'description': 'Proof of Reserves and wallet transparency'}, {'name': 'x402', 'description': 'HTTP 402 payment handshake for AI agents'}],
    docs_url='/docs' if _ENABLE_OPENAPI else None,
    redoc_url='/redoc' if _ENABLE_OPENAPI else None,
    openapi_url='/openapi.json' if _ENABLE_OPENAPI else None,
)
app.include_router(tg_webhook)
app.include_router(frame_router)
app.include_router(auth_router)
app.include_router(mini_router)
STATIC = Path(__file__).resolve().parent / 'static'
MICRO = 10 ** config.USDC_DECIMALS
WEB_RATE_LIMIT: int = int(os.environ.get('WEB_RATE_LIMIT', '60'))
WEB_RATE_WINDOW: int = int(os.environ.get('WEB_RATE_WINDOW', '60'))
WEB_RATE_MAX_CLIENTS: int = int(os.environ.get('WEB_RATE_MAX_CLIENTS', '10000'))
_rl_state: dict[str, list[float]] = {}
_RL_DISABLED: bool = os.environ.get('TESTING', '') == '1'
_ask_cooldown: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    """Rate-limiter identity for a request. By default the TCP peer IP, which
    the client cannot spoof. Only when a trusted reverse proxy is guaranteed
    in front (config.TRUST_PROXY_XFF=1) do we honour the RIGHTMOST
    X-Forwarded-For entry (the real client); the leftmost is client-supplied
    and spoofable, so it is never trusted."""
    client = request.client.host if request.client else 'unknown'
    if config.TRUST_PROXY_XFF:
        xff = request.headers.get('X-Forwarded-For')
        if xff:
            parts = [p.strip() for p in xff.split(',') if p.strip()]
            if parts:
                client = parts[-1]
    return client


_CSP_SCRIPT_WHITELIST = "https://esm.sh https://cdn.jsdelivr.net"


def _nonce_inject(html: str, nonce: str) -> str:
    """Add a CSP nonce attribute to every inline <script>/<style> tag so the
    strict 'nonce-…' directive can replace 'unsafe-inline' on HTML pages.

    Tags that already carry a nonce are left alone; external <script src=…>
    tags are NOT touched (their origins are source-whitelisted instead).
    """
    tag_re = re.compile(r'<\s*(script|style)\b([^>]*)>', re.IGNORECASE)

    def _repl(m: re.Match) -> str:
        tag_name, attrs = m.group(1), m.group(2)
        if 'nonce=' in attrs or re.search(r'\bsrc\s*=', attrs):
            return m.group(0)
        return f'<{tag_name} nonce="{nonce}"{attrs}>'

    return tag_re.sub(_repl, html)


@app.middleware('http')
async def rate_limit(request: Request, call_next):
    path = request.url.path
    if not _RL_DISABLED and (path.startswith('/api/') or path in ('/qr', '/metrics', '/tos', config.WEBHOOK_PATH)):
        client = _client_ip(request)
        now = time.time()
        cutoff = now - WEB_RATE_WINDOW
        window = _rl_state.setdefault(client, [])
        _rl_state[client] = [t for t in window if t > cutoff]
        if len(_rl_state[client]) >= WEB_RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={'detail': 'rate limit exceeded'},
                headers={
                    'Retry-After': str(WEB_RATE_WINDOW),
                    'X-Content-Type-Options': 'nosniff',
                    'Referrer-Policy': 'no-referrer',
                },
            )
        _rl_state[client].append(now)
        if len(_rl_state) > WEB_RATE_MAX_CLIENTS:
            for ip, hits in list(_rl_state.items()):
                if not any(t > cutoff for t in hits):
                    del _rl_state[ip]
    response = await call_next(request)
    # No X-Frame-Options here on purpose: the Mini App runs inside Telegram's
    # iframe and must stay framable. These four are safe everywhere.
    nonce = base64.b64encode(secrets.token_bytes(16)).decode()
    ctype = response.headers.get('content-type', '')
    body = None
    if ctype.startswith('text/html'):
        try:
            chunks = [c async for c in response.body_iterator]
            body = b''.join(chunks)
        except Exception:
            body = None
        if body:
            try:
                body = _nonce_inject(body.decode('utf-8', 'replace'), nonce).encode('utf-8')
            except Exception:
                body = None
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault('Cache-Control', 'no-store, no-cache, must-revalidate')
    response.headers.setdefault('Content-Security-Policy',
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}' {_CSP_SCRIPT_WHITELIST}; "
        f"style-src 'self' 'nonce-{nonce}'; img-src 'self' data: https:; "
        "connect-src 'self'; frame-ancestors 'self' https://web.telegram.org")
    try:
        await ledger.rollback()
    except Exception:
        log.warning("ledger.rollback() failed after request to %s", path, exc_info=True)
    if body is not None:
        return StreamingResponse(iter([body]), status_code=response.status_code,
                                 headers=dict(response.headers), media_type=ctype or 'text/html')
    return response

def _usdc(micro: int) -> float:
    return round(micro / MICRO, 2)

def _require_admin(request: Request) -> None:
    """403 unless the request carries a session cookie for the owner."""
    admin_id = int(config.ADMIN_TG_ID) if config.ADMIN_TG_ID else 0
    session_id = parse_session(request.cookies.get(COOKIE_NAME))
    if not admin_id or session_id != admin_id:
        raise HTTPException(status_code=403, detail='admin only')

async def _safe_hot_balance() -> float | None:
    try:
        return round(await hot_balance(), 2)
    except Exception:
        log.warning("hot_balance() RPC failed", exc_info=True)
        return None

async def _safe_vault_balance() -> float | None:
    try:
        bal = await vault_balance()
        return round(bal, 2) if bal is not None else None
    except Exception:
        log.warning("vault_balance() RPC failed", exc_info=True)
        return None

@app.get('/api/stats', tags=['stats'])
async def api_stats() -> dict:
    s = await ledger.global_stats()
    return {**s, 'volume_usdc': _usdc(s['volume_micro']), 'volume_30d_usdc': _usdc(s['volume_30d_micro']), 'tips_usdc': _usdc(s['tips_micro']), 'deposits_usdc': _usdc(s['deposits_micro']), 'bets_usdc': _usdc(s['bets_micro']), 'fees_usdc': _usdc(s['fees_micro'])}

@app.get('/api/volume_history', tags=['stats'])
async def api_volume_history(days: int=14) -> list[dict]:
    days = min(max(int(days), 1), 30)
    return [{**r, 'volume_usdc': _usdc(r['volume_micro'])} for r in await ledger.volume_history(days)]

@app.get('/api/markets', tags=['markets'])
async def api_markets(status: str='open') -> list[dict]:
    from bot import tip_targets
    bets = await ledger.bets_by_status(status, 20)
    bet_ids = [int(b['id']) for b in bets]
    views = await ledger.bulk_market_views(bet_ids)
    for view in views:
        if not view['creator'].get('username'):
            try:
                bn = await tip_targets.display_name_for(view['creator']['id'])
            except Exception:
                bn = None
            if bn:
                view['creator']['username'] = bn
        view['pot_usdc'] = _usdc(view['pot'])
        for o in view['options']:
            o['pool_usdc'] = _usdc(o['pool'])
    return views

@app.get('/api/market/{bet_id}', tags=['markets'])
async def api_market(bet_id: int) -> dict:
    from bot import tip_targets
    view = await ledger.market_view(bet_id)
    if not view:
        raise HTTPException(status_code=404, detail='Market not found')
    if not view['creator'].get('username'):
        try:
            bn = await tip_targets.display_name_for(view['creator']['id'])
        except Exception:
            bn = None
        if bn:
            view['creator']['username'] = bn
    view['pot_usdc'] = _usdc(view['pot'])
    for o in view['options']:
        o['pool_usdc'] = _usdc(o['pool'])
    return view

@app.get('/api/predictions', tags=['markets'])
async def api_predictions(status: str='open') -> list[dict]:
    """LMSR AMM prediction markets with live odds (Polymarket-style)."""
    out = []
    for m in await ledger.open_markets(20) if status == 'open' else []:
        view = await ledger.amm_market_view(int(m['id']))
        if view:
            view['liquidity_usdc'] = _usdc(view['liquidity_micro'])
            for o in view['options']:
                o.pop('shares', None)
            out.append(view)
    return out

@app.get('/api/prediction/{market_id}', tags=['markets'])
async def api_prediction(market_id: int) -> dict:
    view = await ledger.amm_market_view(market_id)
    if not view:
        raise HTTPException(status_code=404, detail='Prediction market not found')
    view['liquidity_usdc'] = _usdc(view['liquidity_micro'])
    return view

@app.get('/api/leaderboard', tags=['users'])
async def api_leaderboard() -> list[dict]:
    from bot import tip_targets
    out = []
    for r in await ledger.leaderboard(10):
        row = {**r, 'total_usdc': _usdc(r['total_micro'])}
        if not row.get('username'):
            # No Telegram username — surface the user's primary Basename.
            try:
                basename = await tip_targets.display_name_for(int(r['tg_id']))
            except Exception:
                basename = None
            if basename:
                row['username'] = basename
        out.append(row)
    return out

@app.get('/api/user/{tg_id}', tags=['users'])
async def api_user(tg_id: int, request: Request) -> dict:
    if not await ledger.user_exists(tg_id):
        raise HTTPException(status_code=404, detail='User not found')
    admin_id = int(config.ADMIN_TG_ID or 0)
    session_id = parse_session(request.cookies.get(COOKIE_NAME))
    is_owner = session_id in (tg_id, admin_id)
    v = await ledger.user_view(tg_id)
    # Public profile endpoint: expose only non-sensitive stats. The requested
    # user's own full data (balances, positions, tx history) is returned ONLY
    # to that same user or the owner; a stranger enumerating tg_ids gets the
    # public aggregate only (no per-tx amounts, no live balance, no positions).
    if is_owner:
        positions = [{**p, 'stake_usdc': _usdc(p['stake_micro']), 'potential_usdc': _usdc(p['potential_micro'])} for p in await ledger.user_positions(tg_id)]
        history = [{'kind': r['kind'], 'amount_usdc': _usdc(r['amount']), 'created_at': r['created_at']} for r in await ledger.history(tg_id, 12)]
        return {
            **v,
            'balance_usdc': _usdc(v['balance_micro']),
            'tips_sent_usdc': _usdc(v['tips_sent_micro']),
            'tips_received_usdc': _usdc(v['tips_received_micro']),
            'bets_won_usdc': _usdc(v['bets_won_micro']),
            'bets_placed_usdc': _usdc(v['bets_placed_micro']),
            'creator_fees_usdc': _usdc(v['creator_fees_micro']),
            'positions': positions,
            'history': history,
            'deposit_address': str(hot_wallet()),
            'is_owner': True,
        }
    # Public view: totals only (no live balance, positions, or per-tx history).
    username = v.get('username')
    if not username:
        try:
            from bot import tip_targets
            bn = await tip_targets.display_name_for(tg_id)
        except Exception:
            bn = None
        if bn:
            username = bn
    return {
        'username': username,
        'tg_username': v.get('username'),
        'tips_sent_usdc': _usdc(v['tips_sent_micro']),
        'tips_received_usdc': _usdc(v['tips_received_micro']),
        'bets_won_usdc': _usdc(v['bets_won_micro']),
        'bets_placed_usdc': _usdc(v['bets_placed_micro']),
        'creator_fees_usdc': _usdc(v['creator_fees_micro']),
        'deposit_address': str(hot_wallet()),
        'is_owner': False,
    }

@app.get('/api/agent/status', tags=['agent'])
async def api_agent_status(request: Request) -> dict:
    """Agent PnL dashboard — owner-only. Exposes agent reasoning/markets, so it
    must not be public (could be front-run via its public markets)."""
    _require_admin(request)
    import json
    import pathlib

    from agent.caps import get_status
    tg_id = int(config.AGENT_TG_ID) if hasattr(config, 'AGENT_TG_ID') else 0
    if not tg_id:
        return {'error': 'AGENT_TG_ID not configured'}
    if not await ledger.user_exists(tg_id):
        return {'error': 'Agent user not found'}
    v = await ledger.user_view(tg_id)
    caps_status = get_status()
    audit_file = pathlib.Path('agent_audit.jsonl')
    market_count = 0
    bet_count = 0
    if audit_file.exists():
        for line in audit_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn last line (concurrent append) — skip it
            if entry.get('action_type') == 'create_market' or 'market_id' in entry:
                market_count += 1
            if entry.get('bet_amount_usdc', 0) > 0:
                bet_count += 1
    return {
        'balance_usdc': _usdc(v['balance_micro']),
        'markets_created': market_count,
        'bets_placed': bet_count,
        'caps': caps_status,
    }

@app.get('/api/agent/audit', tags=['agent'])
async def api_agent_audit(request: Request) -> list[dict]:
    """Agent audit trail — last 50 actions from local JSONL log (owner-only)."""
    _require_admin(request)
    import json
    import pathlib
    audit_file = pathlib.Path('agent_audit.jsonl')
    if not audit_file.exists():
        return []
    entries = []
    for line in audit_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # torn last line (concurrent append) — skip it
    return entries[-50:]

@app.get('/api/onchain/markets', tags=['markets'])
async def api_onchain_markets() -> list[dict]:
    """On-chain OutcomeMarket markets (Polymarket-style) with live LMSR prices.

    Returns [] when the contract is not configured. Prices are read straight
    from the contract on Base; labels/questions come from the bot registry.
    """
    from bot import onchain_market as om
    return await om.market_views(12)

@app.get('/api/onchain/market/{market_id}', tags=['markets'])
async def api_onchain_market(market_id: int) -> dict:
    """One on-chain market: registry labels + live on-chain state/prices."""
    from bot import onchain_market as om
    m = await ledger.get_onchain_market(market_id)
    if not m:
        raise HTTPException(status_code=404, detail='On-chain market not found')
    options = json.loads(m['options'])
    prices = await om.market_prices(market_id, len(options))
    info = await om.get_market_info(market_id)
    return {
        'id': market_id,
        'question': m['question'],
        'close_at': m['close_at'],
        'b': info.get('b'),
        'escrow_micro': info.get('escrow_micro'),
        'resolved': info.get('resolved'),
        'disputed': info.get('disputed'),
        'cancelled': info.get('cancelled'),
        'winner': info.get('winning_outcome'),
        'options': [
            {'index': i, 'label': o, 'price_pct': float(round(prices[i] * 100, 2))}
            for i, o in enumerate(options)
        ],
    }

@app.get('/api/info', tags=['stats'])
def api_info() -> dict:
    return {'bot_username': config.BOT_USERNAME}

@app.get('/api/health', tags=['stats'])
async def api_health() -> dict:
    """Liveness + deposit-scanner health + DB connectivity."""
    head = None
    try:
        head = base.w3.eth.block_number
    except Exception:
        pass
    last = await ledger.last_block()
    db_ok = True
    try:
        await ledger.ping()
    except Exception:
        db_ok = False
    status = 'ok' if db_ok else 'degraded'
    return {
        'status': status,
        'hot_wallet': str(hot_wallet()),
        'chain_head': head,
        'last_scanned_block': last,
        'deposit_lag': head - last if head is not None else None,
        'db': 'ok' if db_ok else 'down',
    }

@app.post('/api/x402/tip', tags=['x402'])
async def api_x402_tip(request: Request) -> Response:
    """x402 payment handshake: agents pay USDC tips to Telegram users over HTTP.

    First call (no `x-402-payment` header) -> 402 with the invoice headers.
    After paying on-chain, repeat with `x-402-payment: <tx_hash>` -> 200 and
    the tip lands on the recipient's balance. Replay of the same tx -> 409.
    """
    return await x402_tip(request)

@app.post('/api/x402/paywall', tags=['x402'])
async def api_x402_paywall(request: Request) -> Response:
    """x402 payment handshake for paywall content.

    POST /api/x402/paywall?item=<id>&amount=<usdc> — without the payment
    header you get a 402 invoice; after paying, repeat with
    `x-402-payment: <tx_hash>` and the content is returned in the 200 body.
    Replay of the same tx -> 409.
    """
    return await x402_paywall(request)

@app.get('/api/solvency', tags=['treasury'])
async def api_solvency() -> dict:
    """Transparency: every user balance is a claim on the treasury.

    owed = internal balances + unclaimed pending deposits. Primary reserves
    come from the TipBotVault contract when it is deployed (on-chain proof of
    reserves, readable by anyone); otherwise the hot wallet is the reserve.
    """
    liabilities = await ledger.total_liabilities()
    pending = await ledger.pending_deposit_total()
    owed_usdc = _usdc(liabilities + pending)
    bal = await _safe_hot_balance()
    vault_bal = await _safe_vault_balance()
    vault_addr = config.VAULT_ADDRESS
    reserves = vault_bal if vault_addr else bal
    return {'hot_wallet': str(hot_wallet()), 'vault_address': vault_addr, 'vault_balance_usdc': vault_bal, 'reserves_source': 'vault' if vault_addr else 'hot_wallet', 'hot_wallet_balance_usdc': bal, 'liabilities_usdc': _usdc(liabilities), 'pending_deposits_usdc': _usdc(pending), 'owed_usdc': owed_usdc, 'reserve_usdc': round(reserves - owed_usdc, 2) if reserves is not None else None, 'solvent': None if reserves is None else reserves >= owed_usdc}

@app.get('/qr', tags=['treasury'])
async def api_qr(data: str, size: int=220) -> Response:
    """Render a QR PNG locally (no external service). Used by /u pages."""
    if not data or len(data) > 1024:
        raise HTTPException(status_code=400, detail='data must be 1..1024 chars')
    if size < 64 or size > 1024:
        raise HTTPException(status_code=400, detail='size must be 64..1024')
    try:
        return Response(content=await qrlib.qr_bytes(data, size=size), media_type='image/png', headers={'Cache-Control': 'public, max-age=86400'})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


class AskRequest(BaseModel):
    question: str


@app.post('/api/ask', tags=['markets'])
async def api_ask(body: AskRequest, request: Request) -> dict:
    """Same assistant as Telegram's /ask — tool-calling, so answers about
    specific markets are grounded in real current odds, not guessed. Shares
    the global per-IP rate limiter above; no separate throttle here."""
    from bot import ai
    if not ai.ai_enabled():
        raise HTTPException(status_code=503, detail='AI is not configured')
    question = (body.question or '').strip()
    if not question:
        raise HTTPException(status_code=400, detail='question must not be empty')
    if len(question) > config.AI_MAX_QUESTION_LEN:
        raise HTTPException(status_code=400, detail=f'question must be under {config.AI_MAX_QUESTION_LEN} chars')
    now = time.time()
    # Cooldown keyed per client IP (same identity the rate limiter uses): a
    # single 'global' key would let one user 429 everyone else. Bounded by
    # WEB_RATE_MAX_CLIENTS so the dict cannot grow forever.
    _ip = _client_ip(request)
    last = _ask_cooldown.get(_ip, 0.0)
    if now - last < config.AI_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail='please wait before asking again')
    if len(_ask_cooldown) > WEB_RATE_MAX_CLIENTS:
        for ip, ts in list(_ask_cooldown.items()):
            if now - ts >= config.AI_COOLDOWN_SECONDS:
                del _ask_cooldown[ip]
    _ask_cooldown[_ip] = now
    try:
        answer = await ai.ask_about_markets(question)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    if len(answer) > config.AI_MAX_ANSWER_CHARS:
        answer = answer[:config.AI_MAX_ANSWER_CHARS - 1] + '…'
    return {'answer': answer}

@app.get('/api/wallet', tags=['treasury'])
async def api_wallet() -> dict:
    return {'address': str(hot_wallet()), 'balance_usdc': await _safe_hot_balance()}

@app.get('/u/{tg_id}')
async def user_page(tg_id: int) -> FileResponse:
    return FileResponse(STATIC / 'user.html')

@app.get('/m/{bet_id}')
async def market_page(bet_id: int) -> FileResponse:
    return FileResponse(STATIC / 'market.html')

@app.get('/m/oc/{market_id}')
async def onchain_market_page(market_id: int) -> FileResponse:
    """Shareable page for an ON-CHAIN market (OutcomeMarket ERC-1155)."""
    return FileResponse(STATIC / 'oc.html')

@app.get('/me')
async def me_page() -> FileResponse:
    return FileResponse(STATIC / 'me.html')

@app.get('/app', include_in_schema=False)
async def mini_app():
    """The Mini App page, with Base-App embed meta tags rendered against the
    public URL (the Base App crawls these tags to render the launch card)."""
    from web.mini import public_base_url
    html = (STATIC / 'app.html').read_text(encoding='utf-8')
    base_url = public_base_url()
    html = html.replace('__PUBLIC_URL__', base_url)
    html = html.replace('__PUBLIC_HOST__', base_url.split('//')[-1])
    return Response(content=html, media_type='text/html')

@app.post('/api/webhook-miniaction', include_in_schema=False)
async def miniapp_webhook(request: Request):
    """Base App mini app webhook (notification clicks, app events).

    No money moves through this endpoint — it exists so the manifest can
    declare a webhookUrl. Events are logged for the operator; the response
    is always 200 so the platform does not retry indefinitely.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = None
    log.info('miniapp webhook event: %s', payload)
    return Response(status_code=200)

@app.get('/.well-known/farcaster.json', include_in_schema=False)
async def farcaster_manifest():
    """Base App / Farcaster mini app manifest.

    The operator signs the accountAssociation payload in the Base Build
    portal (or with their Farcaster custody key) and saves the full JSON as
    `deploy/farcaster_manifest.json` — see docs/DEPLOY.md, "Base App mini
    app". Missing file -> 404 (the mini app is simply not discoverable yet).
    """
    from fastapi.responses import JSONResponse
    manifest = ROOT / 'deploy' / 'farcaster_manifest.json'
    if not manifest.exists():
        return JSONResponse(status_code=404, content={'detail': 'farcaster manifest not configured'})
    return Response(content=manifest.read_text(encoding='utf-8'), media_type='application/json')

@app.get('/tos', tags=['legal'])
async def tos() -> Response:
    """Terms of Service page."""
    from fastapi.responses import PlainTextResponse
    tos_file = STATIC / 'tos.md'
    if tos_file.exists():
        return PlainTextResponse(tos_file.read_text(encoding='utf-8'))
    return PlainTextResponse("Terms of Service not found.", status_code=404)

@app.get('/metrics', tags=['monitoring'])
async def metrics(request: Request) -> Response:
    from fastapi.responses import PlainTextResponse

    from .metrics import collect_metrics

    if config.METRICS_TOKEN:
        import hmac as _hmac
        auth = request.headers.get("Authorization", "")
        if not _hmac.compare_digest(auth.encode(), f"Bearer {config.METRICS_TOKEN}".encode()):
            return PlainTextResponse("unauthorized", status_code=401)
    return PlainTextResponse(await collect_metrics())

app.mount('/', StaticFiles(directory=str(STATIC), html=True), name='static')
if __name__ == '__main__':
    import uvicorn
    config.validate()
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
