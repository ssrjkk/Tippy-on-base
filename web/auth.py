"""Login for the personal dashboard: Telegram widget or wallet signature.

Two ways to prove identity, both verified with real crypto:

- **Telegram Login Widget**: HMAC-SHA256 over the widget's data-check-string,
  keyed with SHA256(BOT_TOKEN) — the official algorithm from
  https://core.telegram.org/widgets/login#checking-authorization.
- **Wallet connect**: EIP-191 ``personal_sign`` recovered with eth_account and
  matched against ``wallet_links`` (the same table the bot's /link flow
  writes), so a web login is impossible for a wallet the bot doesn't know.

Sessions are stateless signed cookies: ``base64(tg_id:expiry).hmac``. No
server-side session store; tampering breaks the HMAC; expiry bounds replay.
"""

import base64
import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from bot import config
from bot.ledger import ledger

router = APIRouter()

COOKIE_NAME = "tippy_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
# A Telegram widget payload older than a day is rejected (replay window).
TG_AUTH_DATE_TTL = 24 * 3600
# A wallet login message must be signed within this window.
WALLET_MSG_TTL = 600


def _sign(payload: str) -> str:
    return hmac.new(
        config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def make_session(tg_id: int, ttl: int = SESSION_TTL_SECONDS) -> str:
    payload = base64.urlsafe_b64encode(
        f"{tg_id}:{int(time.time()) + ttl}".encode()
    ).decode()
    return f"{payload}.{_sign(payload)}"


def parse_session(token: str | None) -> int | None:
    """Return tg_id for a valid, unexpired session cookie, else None."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        raw = base64.urlsafe_b64decode(payload.encode()).decode()
        tg_id_s, exp_s = raw.split(":")
        tg_id, exp = int(tg_id_s), int(exp_s)
    except Exception:
        return None
    if exp < time.time():
        return None
    return tg_id


def verify_telegram(params: dict[str, str]) -> int:
    """Validate Telegram Login Widget fields, return the tg_id."""
    received_hash = params.get("hash", "")
    if not received_hash:
        raise HTTPException(403, "missing hash")
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items()) if k != "hash"
    )
    secret_key = hashlib.sha256(config.BOT_TOKEN.encode()).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(403, "bad signature")
    try:
        auth_date = int(params.get("auth_date", "0"))
    except ValueError:
        raise HTTPException(403, "bad auth_date") from None
    if time.time() - auth_date > TG_AUTH_DATE_TTL:
        raise HTTPException(403, "stale auth_date")
    try:
        return int(params["id"])
    except (KeyError, ValueError):
        raise HTTPException(403, "missing id") from None


class WalletLogin(BaseModel):
    address: str
    message: str
    signature: str


def verify_wallet(body: WalletLogin) -> int:
    """Recover the signer and map it to a linked bot user."""
    lines = body.message.strip().splitlines()
    exp = next((ln for ln in lines if ln.startswith("Expires:")), None)
    nonce = next((ln for ln in lines if ln.startswith("Nonce:")), None)
    if not exp or not nonce or len(nonce.split(": ", 1)) < 2:
        raise HTTPException(403, "malformed login message")
    try:
        expires = int(exp.split(": ", 1)[1])
    except ValueError:
        raise HTTPException(403, "malformed expiry") from None
    now = time.time()
    if not (now < expires <= now + WALLET_MSG_TTL):
        raise HTTPException(403, "expired login message")

    from eth_account import Account
    from eth_account.messages import encode_defunct

    try:
        recovered = Account.recover_message(
            encode_defunct(text=body.message), signature=body.signature
        )
    except Exception:
        raise HTTPException(403, "bad signature") from None
    if recovered.lower() != body.address.lower():
        raise HTTPException(403, "signature does not match address")

    tg_id = ledger.tg_id_of_address(recovered)
    if tg_id is None:
        raise HTTPException(
            404,
            "wallet is not linked to any Tippy user - link it in the bot first "
            f"(t.me/{config.BOT_USERNAME})",
        )
    return tg_id


def _login_page() -> str:
    bot = config.BOT_USERNAME or "tippy_on_base_bot"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Tippy — Login</title>
<link rel="stylesheet" href="/style.css"/>
<style>
  .login-card {{ max-width: 420px; margin: 8vh auto; padding: 32px;
    border-radius: 16px; background: #0a0b0d; border: 1px solid #1f2125; }}
  .login-card h1 {{ font-size: 22px; margin: 0 0 6px; }}
  .login-card p {{ color: #9aa0a6; margin: 0 0 24px; font-size: 14px; }}
  .or {{ text-align: center; color: #555; margin: 18px 0; font-size: 13px; }}
  .btn-wallet {{ width: 100%; padding: 14px; border-radius: 10px; border: 1px solid #0052ff;
    background: transparent; color: #fff; font-size: 15px; cursor: pointer; }}
  .btn-wallet:hover {{ background: #0052ff22; }}
  #tg-widget {{ display: flex; justify-content: center; min-height: 40px; }}
  .err {{ color: #ff6b6b; font-size: 13px; margin-top: 14px; min-height: 16px; }}
</style>
</head>
<body>
<div class="orb orb-a" aria-hidden="true"></div>
<div class="login-card">
  <h1>Sign in to Tippy</h1>
  <p>Access your personal dashboard — balance, positions and deposits.</p>

  <div id="tg-widget">
    <script async src="https://telegram.org/js/telegram-widget.js?22"
      data-telegram-login="{bot}"
      data-size="large"
      data-auth-url="/api/auth/telegram"
      data-request-access="write"></script>
  </div>

  <div class="or">— or —</div>

  <button class="btn-wallet" id="connect">🦊 Connect Wallet</button>
  <div class="err" id="err"></div>
</div>
<script>
const err = (m) => document.getElementById('err').textContent = m;
document.getElementById('connect').onclick = async () => {{
  if (!window.ethereum) return err('No EVM wallet found (install MetaMask)');
  try {{
    const [addr] = await ethereum.request({{method: 'eth_requestAccounts'}});
    const msg = 'Tippy login\\nAddress: ' + addr +
      '\\nNonce: ' + crypto.getRandomValues(new Uint8Array(16)).reduce((s,b)=>s+b.toString(16).padStart(2,'0'),'') +
      '\\nExpires: ' + Math.floor(Date.now()/1000 + 600);
    const sig = await ethereum.request({{method: 'personal_sign',
      params: [msg, addr]}});
    const r = await fetch('/api/auth/wallet', {{method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{address: addr, message: msg, signature: sig}})}});
    if (r.ok) location.href = '/me';
    else err((await r.json()).detail || 'Login failed');
  }} catch (e) {{ err(e.message); }}
}};
</script>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse, tags=["auth"])
async def login_page() -> HTMLResponse:
    return HTMLResponse(_login_page())


def _session_response(tg_id: int, redirect: str | None = None) -> JSONResponse:
    resp: JSONResponse | RedirectResponse
    if redirect:
        resp = RedirectResponse(redirect, status_code=303)
    else:
        resp = JSONResponse({"ok": True, "tg_id": tg_id})
    resp.set_cookie(
        COOKIE_NAME,
        make_session(tg_id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return resp


@router.get("/api/auth/telegram", include_in_schema=False)
async def auth_telegram(request: Request):
    params = dict(request.query_params)
    tg_id = verify_telegram(params)
    username = params.get("username", "")
    ledger.ensure_user(tg_id, username or None)
    return _session_response(tg_id, redirect="/me")


@router.post("/api/auth/wallet", tags=["auth"])
async def auth_wallet(body: WalletLogin):
    tg_id = verify_wallet(body)
    return _session_response(tg_id)


@router.get("/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/api/me", tags=["users"])
async def api_me(request: Request):
    tg_id = parse_session(request.cookies.get(COOKIE_NAME))
    if tg_id is None:
        raise HTTPException(401, "not logged in")
    if not ledger.user_exists(tg_id):
        raise HTTPException(401, "unknown user")
    bal = ledger.balance(tg_id)
    positions = [
        {
            "market_id": p["market_id"],
            "question": p["question"],
            "option": p["option"],
            "shares": float(p["shares"]) / 1e6,
            "cost_usdc": float(p["cost"]) / 1e6,
            "price": float(p["price"]),
            "value_usdc": float(p["value"]) / 1e6,
        }
        for p in ledger.user_market_positions(tg_id)
    ]
    return {
        "tg_id": tg_id,
        "balance_usdc": float(bal),
        "linked_address": ledger.linked_address(tg_id),
        "positions": positions,
        "bot_link": f"https://t.me/{config.BOT_USERNAME}",
    }
