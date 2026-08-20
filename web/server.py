"""Web dashboard: public stats, markets, leaderboard, wallet transparency.

Run:  python -m web.server
"""

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# Allow imports of the bot package when run from the project root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import base, config  # noqa: E402
from bot import qr as qrlib  # noqa: E402
from bot.base import hot_balance, hot_wallet, vault_balance  # noqa: E402
from bot.ledger import ledger  # noqa: E402
from web.frame import router as frame_router  # noqa: E402
from web.hook import router as tg_webhook  # noqa: E402
from web.x402 import x402_paywall, x402_tip  # noqa: E402

app = FastAPI(title="Tippy", version="1.0.0", description="Community economy on Base")
app.include_router(tg_webhook)
app.include_router(frame_router)

STATIC = Path(__file__).resolve().parent / "static"
MICRO = 10**config.USDC_DECIMALS

# Public dashboards are fully exposed by design (transparency), so the JSON/QR
# endpoints are rate-limited per IP. Most of them hit the RPC (solvency, wallet)
# or burn CPU (QR) — without this, anyone could drain the RPC quota.
WEB_RATE_LIMIT: int = int(os.environ.get("WEB_RATE_LIMIT", "60"))
WEB_RATE_WINDOW: int = int(os.environ.get("WEB_RATE_WINDOW", "60"))
# Upper bound on tracked clients: beyond this we prune stale entries so a
# public dashboard never leaks memory from a scraper rotation.
WEB_RATE_MAX_CLIENTS: int = int(os.environ.get("WEB_RATE_MAX_CLIENTS", "10000"))
_rl_state: dict[str, list[float]] = {}


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") or path == "/qr":
        client = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - WEB_RATE_WINDOW
        window = _rl_state.setdefault(client, [])
        _rl_state[client] = [t for t in window if t > cutoff]
        if len(_rl_state[client]) >= WEB_RATE_LIMIT:
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        _rl_state[client].append(now)
        if len(_rl_state) > WEB_RATE_MAX_CLIENTS:
            for ip, hits in list(_rl_state.items()):
                if not any(t > cutoff for t in hits):
                    del _rl_state[ip]
    response = await call_next(request)
    # Close any read transaction left open by ledger queries so the shared
    # connection never pins table locks for long (this blocked schema DDL
    # from other processes). Rollback is safe: writes commit in ledger.
    try:
        ledger.rollback()
    except Exception:
        pass
    return response


def _usdc(micro: int) -> float:
    return round(micro / MICRO, 2)


def _safe_hot_balance() -> float | None:
    try:
        return round(hot_balance(), 2)
    except Exception:
        return None


def _safe_vault_balance() -> float | None:
    try:
        bal = vault_balance()
        return round(bal, 2) if bal is not None else None
    except Exception:
        return None


@app.get("/api/stats")
def api_stats() -> dict:
    s = ledger.global_stats()
    return {
        **s,
        "volume_usdc": _usdc(s["volume_micro"]),
        "volume_30d_usdc": _usdc(s["volume_30d_micro"]),
        "tips_usdc": _usdc(s["tips_micro"]),
        "deposits_usdc": _usdc(s["deposits_micro"]),
        "bets_usdc": _usdc(s["bets_micro"]),
        "fees_usdc": _usdc(s["fees_micro"]),
    }


@app.get("/api/volume_history")
def api_volume_history(days: int = 14) -> list[dict]:
    days = min(max(int(days), 1), 30)
    return [
        {**r, "volume_usdc": _usdc(r["volume_micro"])}
        for r in ledger.volume_history(days)
    ]


@app.get("/api/markets")
def api_markets(status: str = "open") -> list[dict]:
    out = []
    for b in ledger.bets_by_status(status, 20):
        view = ledger.market_view(b["id"])
        if view:
            view["pot_usdc"] = _usdc(view["pot"])
            for o in view["options"]:
                o["pool_usdc"] = _usdc(o["pool"])
            out.append(view)
    return out


@app.get("/api/market/{bet_id}")
def api_market(bet_id: int) -> dict:
    view = ledger.market_view(bet_id)
    if not view:
        raise HTTPException(status_code=404, detail="Market not found")
    view["pot_usdc"] = _usdc(view["pot"])
    for o in view["options"]:
        o["pool_usdc"] = _usdc(o["pool"])
    return view


@app.get("/api/leaderboard")
def api_leaderboard() -> list[dict]:
    return [
        {**r, "total_usdc": _usdc(r["total_micro"])} for r in ledger.leaderboard(10)
    ]


@app.get("/api/user/{tg_id}")
def api_user(tg_id: int) -> dict:
    if not ledger.user_exists(tg_id):
        raise HTTPException(status_code=404, detail="User not found")
    v = ledger.user_view(tg_id)
    positions = [
        {
            **p,
            "stake_usdc": _usdc(p["stake_micro"]),
            "potential_usdc": _usdc(p["potential_micro"]),
        }
        for p in ledger.user_positions(tg_id)
    ]
    history = [
        {
            "kind": r["kind"],
            "amount_usdc": _usdc(r["amount"]),
            "counterparty": r["counterparty"],
            "note": r["note"],
            "created_at": r["created_at"],
        }
        for r in ledger.history(tg_id, 12)
    ]
    return {
        **v,
        "balance_usdc": _usdc(v["balance_micro"]),
        "tips_sent_usdc": _usdc(v["tips_sent_micro"]),
        "tips_received_usdc": _usdc(v["tips_received_micro"]),
        "bets_won_usdc": _usdc(v["bets_won_micro"]),
        "bets_placed_usdc": _usdc(v["bets_placed_micro"]),
        "creator_fees_usdc": _usdc(v["creator_fees_micro"]),
        "positions": positions,
        "history": history,
        "deposit_address": str(hot_wallet()),
    }


@app.get("/api/info")
def api_info() -> dict:
    return {"bot_username": config.BOT_USERNAME}


@app.get("/api/health")
def api_health() -> dict:
    """Liveness + deposit-scanner health. If the scanner falls behind the chain
    head, deposits would be picked up late — `deposit_lag` makes that visible."""
    head = None
    try:
        head = base.w3.eth.block_number
    except Exception:
        pass
    last = ledger.last_block()
    return {
        "status": "ok",
        "hot_wallet": str(hot_wallet()),
        "chain_head": head,
        "last_scanned_block": last,
        "deposit_lag": (head - last) if head is not None else None,
    }


@app.post("/api/x402/tip")
async def api_x402_tip(request: Request) -> Response:
    """x402 payment handshake: agents pay USDC tips to Telegram users over HTTP.

    First call (no `x-402-payment` header) -> 402 with the invoice headers.
    After paying on-chain, repeat with `x-402-payment: <tx_hash>` -> 200 and
    the tip lands on the recipient's balance. Replay of the same tx -> 409.
    """
    return await x402_tip(request)


@app.post("/api/x402/paywall")
async def api_x402_paywall(request: Request) -> Response:
    """x402 payment handshake for paywall content.

    POST /api/x402/paywall?item=<id>&amount=<usdc> — without the payment
    header you get a 402 invoice; after paying, repeat with
    `x-402-payment: <tx_hash>` and the content is returned in the 200 body.
    Replay of the same tx -> 409.
    """
    return await x402_paywall(request)


@app.get("/api/solvency")
def api_solvency() -> dict:
    """Transparency: every user balance is a claim on the treasury.

    owed = internal balances + unclaimed pending deposits. Primary reserves
    come from the TipBotVault contract when it is deployed (on-chain proof of
    reserves, readable by anyone); otherwise the hot wallet is the reserve.
    """
    liabilities = ledger.total_liabilities()
    pending = ledger.pending_deposit_total()
    owed_usdc = _usdc(liabilities + pending)
    bal = _safe_hot_balance()
    vault_bal = _safe_vault_balance()
    vault_addr = config.VAULT_ADDRESS
    reserves = vault_bal if vault_addr else bal
    return {
        "hot_wallet": str(hot_wallet()),
        "vault_address": vault_addr,
        "vault_balance_usdc": vault_bal,
        "reserves_source": "vault" if vault_addr else "hot_wallet",
        "hot_wallet_balance_usdc": bal,
        "liabilities_usdc": _usdc(liabilities),
        "pending_deposits_usdc": _usdc(pending),
        "owed_usdc": owed_usdc,
        "reserve_usdc": round(reserves - owed_usdc, 2) if reserves is not None else None,
        "solvent": None if reserves is None else reserves >= owed_usdc,
    }


@app.get("/qr")
def api_qr(data: str, size: int = 220) -> Response:
    """Render a QR PNG locally (no external service). Used by /u pages."""
    if not data or len(data) > 1024:
        raise HTTPException(status_code=400, detail="data must be 1..1024 chars")
    try:
        return Response(
            content=qrlib.qr_bytes(data, size=size),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/wallet")
def api_wallet() -> dict:
    return {
        "address": str(hot_wallet()),
        "balance_usdc": _safe_hot_balance(),
    }


@app.get("/u/{tg_id}")
async def user_page(tg_id: int) -> FileResponse:
    return FileResponse(STATIC / "user.html")


@app.get("/m/{bet_id}")
async def market_page(bet_id: int) -> FileResponse:
    return FileResponse(STATIC / "market.html")


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    config.validate()
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
