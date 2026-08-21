import hashlib
import os
import re
from decimal import Decimal

from dotenv import load_dotenv

# .env lives next to the project root (not the CWD): this keeps `python -m
# bot.main` working from any directory and is a no-op inside Docker (env_file).
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
BASE_RPC_URL: str = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
HOT_WALLET_KEY: str = os.environ["HOT_WALLET_KEY"]

# Signing key for web login sessions (/login -> /me). Empty -> derived from
# BOT_TOKEN, so it never needs to be provisioned separately and never leaves
# the server. Set SECRET_KEY explicitly to rotate sessions independently.
SECRET_KEY: str = (
    os.environ.get("SECRET_KEY", "").strip()
    or hashlib.sha256(f"tippy-session:{BOT_TOKEN}".encode()).hexdigest()
)
POLL_SECONDS: int = int(os.environ.get("POLL_SECONDS", "15"))
# RPC request timeout in seconds (guards watchers/web against a hung provider)
RPC_TIMEOUT_SECONDS: int = int(os.environ.get("RPC_TIMEOUT_SECONDS", "10"))

DATABASE_URL: str = os.environ.get(
    # 5433: the compose db service maps to 5433 so it never collides with a
    # locally installed postgres on 5432. In compose this is overridden to db:5432.
    "DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5433/tipbot"
)

# Web dashboard
WEB_HOST: str = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT: int = int(os.environ.get("WEB_PORT", "8000"))

# Telegram webhook (instead of long polling). Set WEBHOOK_URL (public https
# URL, e.g. https://tipbot.example.com/telegram-webhook) to enable it. The
# secret is the Telegram Bot API secret token (empty -> derived from BOT_TOKEN).
WEBHOOK_URL: str | None = os.environ.get("WEBHOOK_URL", "").strip() or None
WEBHOOK_PATH: str = os.environ.get("WEBHOOK_PATH", "/telegram-webhook")
WEBHOOK_SECRET: str | None = os.environ.get("WEBHOOK_SECRET", "").strip() or None

# Bot username without leading "@" (used for web links, optional)
BOT_USERNAME: str = os.environ.get("BOT_USERNAME", "")

# Business model
# 1% on withdrawals (covers gas + operations), 2% on bet winnings (net profit).
WITHDRAW_FEE_PCT: Decimal = Decimal(os.environ.get("WITHDRAW_FEE_PCT", "0.01"))
WIN_FEE_PCT: Decimal = Decimal(os.environ.get("WIN_FEE_PCT", "0.02"))

# Abuse protection / gas griefing
MIN_WITHDRAW_USDC: Decimal = Decimal(os.environ.get("MIN_WITHDRAW_USDC", "1"))
MAX_WITHDRAWS_PER_DAY: int = int(os.environ.get("MAX_WITHDRAWS_PER_DAY", "5"))
MAX_TIP_USDC: Decimal = Decimal(os.environ.get("MAX_TIP_USDC", "1000"))
MAX_BET_USDC: Decimal = Decimal(os.environ.get("MAX_BET_USDC", "500"))
LINK_NONCE_TTL_SECONDS: int = int(os.environ.get("LINK_NONCE_TTL_SECONDS", "3600"))
MAX_OPTION_LEN: int = int(os.environ.get("MAX_OPTION_LEN", "60"))
MONEY_CMD_COOLDOWN_SECONDS: int = int(os.environ.get("MONEY_CMD_COOLDOWN_SECONDS", "5"))

# Owner/announcements (/broadcast) — Telegram numeric ID
ADMIN_TG_ID: int | None = int(os.environ.get("ADMIN_TG_ID", "0")) or None

# Group rain (/rain <amount> [count]) — giveaway, pure transfers
RAIN_MAX_USDC: Decimal = Decimal(os.environ.get("RAIN_MAX_USDC", "100"))
RAIN_MAX_RECIPIENTS: int = int(os.environ.get("RAIN_MAX_RECIPIENTS", "25"))
RAIN_MIN_RECIPIENTS: int = int(os.environ.get("RAIN_MIN_RECIPIENTS", "3"))

# Deposit scanning robustness
DEPOSIT_SCAN_LOOKBACK_BLOCKS: int = int(os.environ.get("DEPOSIT_SCAN_LOOKBACK_BLOCKS", "2000"))
DEPOSIT_CONFIRM_BLOCKS: int = int(os.environ.get("DEPOSIT_CONFIRM_BLOCKS", "10"))

# Withdrawal lifecycle: if a withdraw tx is not mined within this window it is
# considered stuck/dropped and the user is refunded automatically.
WITHDRAW_STUCK_TIMEOUT_SECONDS: int = int(os.environ.get("WITHDRAW_STUCK_TIMEOUT_SECONDS", "600"))

# Dead-market protection: after close_at + grace, anyone can refund a market.
MARKET_GRACE_HOURS: int = int(os.environ.get("MARKET_GRACE_HOURS", "72"))
GRACE_WARN_BEFORE_HOURS: int = int(os.environ.get("GRACE_WARN_BEFORE_HOURS", "12"))

# Prediction markets v2 (Polymarket-style LMSR AMM, see bot/ledger.py).
# The creator deposits a subsidy; b = subsidy / ln(n_options) guarantees the
# AMM can always cover the worst-case payout (b*ln(n) funding theorem).
MARKET_MIN_SUBSIDY_USDC: Decimal = Decimal(os.environ.get("MARKET_MIN_SUBSIDY_USDC", "10"))
MARKET_MAX_SUBSIDY_USDC: Decimal = Decimal(os.environ.get("MARKET_MAX_SUBSIDY_USDC", "1000"))
MARKET_MAX_TRADE_USDC: Decimal = Decimal(os.environ.get("MARKET_MAX_TRADE_USDC", "500"))

# AI assistant (/ask): any OpenAI-compatible chat-completions endpoint works
# (OpenAI, OpenRouter, local llama/vLLM server, ...). Empty key disables /ask.
AI_API_URL: str = os.environ.get("AI_API_URL", "https://api.groq.com/openai/v1").rstrip("/")
AI_API_KEY: str | None = os.environ.get("AI_API_KEY", "").strip() or None
AI_MODEL: str = os.environ.get("AI_MODEL", "llama-3.3-70b-versatile")
AI_TIMEOUT_SECONDS: int = int(os.environ.get("AI_TIMEOUT_SECONDS", "45"))
AI_COOLDOWN_SECONDS: int = int(os.environ.get("AI_COOLDOWN_SECONDS", "15"))
AI_MAX_QUESTION_LEN: int = int(os.environ.get("AI_MAX_QUESTION_LEN", "1000"))
AI_MAX_ANSWER_CHARS: int = int(os.environ.get("AI_MAX_ANSWER_CHARS", "3500"))

# Block explorer for tx links (Base mainnet)
BASESCAN_URL: str = os.environ.get("BASESCAN_URL", "https://basescan.org").rstrip("/")

# Reaction tips (emoji -> USDC amount). Requires bot admin in the group and
# privacy mode off (BotFather -> /setprivacy -> Disable) to index messages.
REACTION_TIPS: dict[str, Decimal] = {
    "🔥": Decimal("1"),
    "❤️": Decimal("2"),
    "⚡": Decimal("5"),
    "👏": Decimal("10"),
    "🎉": Decimal("25"),
}

# Paywall abuse protection: per-user caps on paid content/channels
PAYWALL_MAX_ITEMS_PER_USER: int = int(os.environ.get("PAYWALL_MAX_ITEMS_PER_USER", "50"))
PAYWALL_MAX_CHANNELS_PER_USER: int = int(os.environ.get("PAYWALL_MAX_CHANNELS_PER_USER", "5"))
PAYWALL_MAX_TITLE_LEN: int = int(os.environ.get("PAYWALL_MAX_TITLE_LEN", "120"))
PAYWALL_MAX_CONTENT_LEN: int = int(os.environ.get("PAYWALL_MAX_CONTENT_LEN", "4000"))

# Reaction-tip message index retention: rows older than this are pruned by
# the daily housekeeping watcher so the DB stays bounded in active groups.
MESSAGE_INDEX_RETENTION_SECONDS: int = int(os.environ.get("MESSAGE_INDEX_RETENTION_SECONDS", str(90 * 86400)))

# USDC on Base mainnet (well-audited, no custom contracts in MVP)
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6

# On-chain treasury (TipBotVault, see contracts/). Users deposit into the
# vault contract; the dashboard reads its USDC balance as the primary
# proof-of-reserves. Leave empty to keep the hot wallet as the sole reserve.
VAULT_ADDRESS: str | None = os.environ.get("VAULT_ADDRESS", "").strip() or None


def validate() -> None:
    """Fail fast on misconfigured secrets before the bot touches the chain.

    Called from bot/main.py and web/server.py entrypoints (not at import time,
    so tests and other consumers stay unaffected).
    """
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{35}", BOT_TOKEN):
        raise ValueError("BOT_TOKEN is malformed (expects <bot_id>:<api_hash>)")
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", HOT_WALLET_KEY):
        raise ValueError(
            "HOT_WALLET_KEY must be the wallet's 0x + 64-hex private key "
            "(not a seed phrase, not a public address)"
        )
    if not BASE_RPC_URL.startswith(("http://", "https://")):
        raise ValueError(f"BASE_RPC_URL is not a valid http(s) URL: {BASE_RPC_URL!r}")
    if WEBHOOK_URL and not re.fullmatch(r"https://[^\s/]+[^\s]*", WEBHOOK_URL):
        raise ValueError(f"WEBHOOK_URL must be a public https URL: {WEBHOOK_URL!r}")

# Standard ERC-20 ABI subset we need
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
]
