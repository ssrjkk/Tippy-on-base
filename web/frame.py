"""Farcaster Frame for paid posts: a shareable card that funnels Warpcast
users into the Telegram bot (or the raw x402 API).

GET /frame/<item_id> — the Frame HTML (OpenGraph + fc:frame meta tags).
The frame image is a static PNG served from /static/frame.png; buttons are
*link* buttons, so no frame server round-trip is required: users land either
on the t.me deep link (one-tap buy inside the bot) or on the x402 invoice URL
(agents / wallets can pay it directly).

Warpcast requires the frame image to be an absolute https URL, so the page
is only meaningful once the webhook URL is configured (after deploy).
"""

from html import escape

from fastapi import APIRouter

from bot import config
from bot import ledger as ledger_mod

router = APIRouter()

MICRO = 10**config.USDC_DECIMALS


def _public_base() -> str:
    base_url = (config.WEBHOOK_URL or "").rstrip("/")
    if not base_url:
        return "https://tipbot.example.invalid"
    return base_url


@router.get("/frame/{item_id}")
def frame_page(item_id: int) -> str:
    item = ledger_mod.ledger.paywall_item(item_id)
    if item is None:
        return "<html><body>Post not found</body></html>"
    base_url = _public_base()
    price = int(item["price_micro"]) / MICRO
    title = escape(item["title"])
    image_url = f"{base_url}/static/frame.png"
    bot_url = f"https://t.me/{config.BOT_USERNAME}?start=paywall_{item_id}"
    api_url = f"{base_url}/api/x402/paywall?item={item_id}&amount={price:g}"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta property="og:title" content="{title} — {price:g} USDC on Base">
<meta property="og:description" content="Paywalled USDC content via Tippy. Pay with x402 or inside Telegram.">
<meta property="og:image" content="{image_url}">
<meta property="fc:frame" content="vNext">
<meta property="fc:frame:image" content="{image_url}">
<meta property="fc:frame:button:1" content="Buy in Telegram">
<meta property="fc:frame:button:1:action" content="link">
<meta property="fc:frame:button:1:target" content="{bot_url}">
<meta property="fc:frame:button:2" content="x402 API (agents)">
<meta property="fc:frame:button:2:action" content="link">
<meta property="fc:frame:button:2:target" content="{api_url}">
</head>
<body>{title} — {price:g} USDC</body>
</html>
"""
    return html
