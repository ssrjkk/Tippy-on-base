"""Telegram webhook endpoint inside the FastAPI app.

With WEBHOOK_URL set, bot/main.py registers the webhook with Telegram and
runs only the background watchers; Telegram delivers updates to
POST {WEBHOOK_PATH} (verified by the secret token header), and the
dispatcher feeds them through the same handler router as long polling.
"""

import hashlib
import hmac
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from bot import config
from bot.handlers import router as handlers_router

log = logging.getLogger("tipbot.hook")

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
# One aiogram Dispatcher may own a router, and only one. The bot process
# (bot/main.py) attaches handlers.router to its own dispatcher; this webhook
# process attaches it to `dp` — except in tests, where both run in one
# process and the router may already belong to another dispatcher. In that
# case we feed updates through the router's current owner instead of
# creating a second owner (aiogram raises on double-attach).
dp = Dispatcher()
router = APIRouter()

# Telegram updates never exceed a few KB; reject anything larger.
WEBHOOK_MAX_BODY: int = 100_000


def _dispatcher() -> Dispatcher:
    parent = handlers_router.parent_router
    if parent is not None:
        return parent
    dp.include_router(handlers_router)
    return dp


def webhook_secret() -> str:
    """Telegram Bot API secret token: explicit WEBHOOK_SECRET or a stable
    derivation from the bot token (so no extra env var is required)."""
    if config.WEBHOOK_SECRET:
        return config.WEBHOOK_SECRET
    return hashlib.sha256(config.BOT_TOKEN.encode()).hexdigest()[:32]


async def telegram_webhook(request: Request) -> Response:
    # Telegram updates are small; refuse oversized bodies so the endpoint
    # can't be used as a memory sink.
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > WEBHOOK_MAX_BODY:
        return JSONResponse(status_code=413, content={"detail": "payload too large"})
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not provided or not hmac.compare_digest(provided, webhook_secret()):
        return JSONResponse(status_code=403, content={"detail": "forbidden"})
    try:
        update = await request.json()
    except Exception:
        # Not JSON (scanner noise, misconfigured proxy): 200 so Telegram does
        # not retry forever, nothing is fed to the dispatcher.
        return Response(status_code=200)
    try:
        await _dispatcher().feed_webhook_update(bot, update)
    except Exception:
        # Never let Telegram redeliver the update forever; the watchers and
        # the ledger stay consistent because handlers are idempotent.
        log.exception("webhook update failed")
    return Response(status_code=200)


router.add_api_route(
    config.WEBHOOK_PATH, telegram_webhook, methods=["POST"], include_in_schema=False
)
