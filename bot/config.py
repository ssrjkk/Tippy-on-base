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
# Comma-separated fallback RPC URLs (tried in order if primary fails).
# Example: https://base-mainnet.g.alchemy.com/v2/KEY,https://base.infura.io/v3/KEY
BASE_RPC_FALLBACK_URLS: str = os.environ.get("BASE_RPC_FALLBACK_URLS", "")
HOT_WALLET_KEY: str = os.environ["HOT_WALLET_KEY"]

# Signing key for web login sessions (/login -> /me). Empty -> derived from
# BOT_TOKEN, so it never needs to be provisioned separately and never leaves
# the server. Set SECRET_KEY explicitly to rotate sessions independently.
SECRET_KEY: str = (
    os.environ.get("SECRET_KEY", "").strip()
    or hashlib.sha256(f"tippy-session:{BOT_TOKEN}".encode()).hexdigest()
)
# Deny-by-default real-client-IP resolution for the rate limiter. The web
# server binds 0.0.0.0 and, unless a trusted reverse proxy is guaranteed in
# front, an attacker can forge X-Forwarded-For to rotate identities and bypass
# per-IP limits. Set TRUST_PROXY_XFF=1 ONLY when every request arrives through
# your proxy (cloudflared / Koyeb edge / nginx), never for a directly exposed
# port. False (default) uses the TCP peer IP, which cannot be spoofed.
TRUST_PROXY_XFF: bool = os.environ.get("TRUST_PROXY_XFF", "0") == "1"
POLL_SECONDS: int = int(os.environ.get("POLL_SECONDS", "15"))
# Pin api.telegram.org to a reachable Telegram DC IP when DNS is poisoned/blocked.
TELEGRAM_API_IP: str = os.environ.get("TELEGRAM_API_IP", "").strip()
# HTTP(S)/SOCKS proxy for Bot API calls when api.telegram.org is blocked at
# the network level (e.g. regional censorship). Examples:
#   http://user:pass@host:8080   socks5://host:1080   socks5h://host:1080
TELEGRAM_API_PROXY: str = os.environ.get("TELEGRAM_API_PROXY", "").strip()
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

# x402 HTTP-402 agent payments. DISABLED by default: the endpoint shares the
# deposit hot-wallet address with no per-invoice binding, so an attacker could
# reuse any deposit tx to drain it. Enable only with a dedicated x402 receive
# address that is NOT the deposit hot wallet.
X402_ENABLED: bool = os.environ.get("X402_ENABLED") == "1"
# Dedicated receive address for x402 payments. MUST differ from the deposit
# hot wallet — otherwise any deposit tx can be replayed as an x402 payment
# and the real depositor loses funds. Empty -> x402 stays disabled even if
# X402_ENABLED=1.
X402_RECEIVE_ADDRESS: str = os.environ.get("X402_RECEIVE_ADDRESS", "").strip()

# Metrics endpoint protection. Empty (default) -> /metrics is open so a local
# Prometheus can scrape it directly. Set METRICS_TOKEN to require
# `Authorization: Bearer <token>`, keeping operational data (liabilities, user
# counts, volume) off the public internet.
METRICS_TOKEN: str = os.environ.get("METRICS_TOKEN", "").strip()

# Telegram webhook (instead of long polling). Set WEBHOOK_URL (public https
# URL, e.g. https://tipbot.example.com/telegram-webhook) to enable it. The
# secret is the Telegram Bot API secret token (empty -> derived from BOT_TOKEN).
WEBHOOK_URL: str | None = os.environ.get("WEBHOOK_URL", "").strip() or None
WEBHOOK_PATH: str = os.environ.get("WEBHOOK_PATH", "/telegram-webhook")
WEBHOOK_SECRET: str | None = os.environ.get("WEBHOOK_SECRET", "").strip() or None

# Public base URL of the Mini App / web dashboard. Must be https for Telegram
# WebApp buttons. Kept separate from WEBHOOK_URL so the bot can run in long
# polling mode while still advertising a public https Mini App URL (e.g. a
# cloudflared tunnel or a fixed domain). Empty -> falls back to WEBHOOK_URL,
# then a (broken) http://HOST:PORT that logs a warning. The launch script
# auto-populates this from the cloudflared tunnel URL.
MINI_APP_URL: str | None = os.environ.get("MINI_APP_URL", "").strip() or None

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
MAX_WALLETS_PER_USER: int = int(os.environ.get("MAX_WALLETS_PER_USER", "10"))

# AML thresholds
WITHDRAW_LARGE_USDC_THRESHOLD: int = int(os.environ.get("WITHDRAW_LARGE_USDC_THRESHOLD", "500"))

# Owner/announcements (/broadcast) — Telegram numeric ID
ADMIN_TG_ID: int | None = int(os.environ.get("ADMIN_TG_ID", "0") or "0") or None

# Group rain (/rain <amount> [count]) — giveaway, pure transfers
RAIN_MAX_USDC: Decimal = Decimal(os.environ.get("RAIN_MAX_USDC", "100"))
RAIN_MAX_RECIPIENTS: int = int(os.environ.get("RAIN_MAX_RECIPIENTS", "25"))
RAIN_MIN_RECIPIENTS: int = int(os.environ.get("RAIN_MIN_RECIPIENTS", "3"))

# Deposit scanning robustness
DEPOSIT_SCAN_LOOKBACK_BLOCKS: int = int(os.environ.get("DEPOSIT_SCAN_LOOKBACK_BLOCKS", "2000"))
DEPOSIT_CONFIRM_BLOCKS: int = int(os.environ.get("DEPOSIT_CONFIRM_BLOCKS", "10"))
# Public RPCs reject eth_getLogs over wide block ranges (HTTP 413), so the
# deposit sweep walks the chain in bounded chunks. MAX_CHUNKS_PER_SWEEP caps
# how far a single poll may catch up (chunks * chunk_size blocks).
DEPOSIT_SCAN_CHUNK_BLOCKS: int = int(os.environ.get("DEPOSIT_SCAN_CHUNK_BLOCKS", "1500"))
DEPOSIT_SCAN_MAX_CHUNKS_PER_SWEEP: int = int(os.environ.get("DEPOSIT_SCAN_MAX_CHUNKS_PER_SWEEP", "40"))

# Withdrawal lifecycle: if a withdraw tx is not mined within this window it is
# considered stuck/dropped and the user is refunded automatically.
WITHDRAW_STUCK_TIMEOUT_SECONDS: int = int(os.environ.get("WITHDRAW_STUCK_TIMEOUT_SECONDS", "600"))

# Withdrawal batching (P1): /withdraw enqueues; a watcher flushes the queue in
# a single TipBotVault.batchDistribute tx to save gas (one on-chain tx for many
# recipients instead of N). The queue flushes when ANY threshold is hit.
#   WITHDRAW_BATCH_FLUSH_SECONDS - max age of the oldest queued withdraw
#   WITHDRAW_BATCH_FLUSH_COUNT   - max queued withdrawals in one batch
#   WITHDRAW_BATCH_FLUSH_USDC    - max total queued value (USDC)
# If VAULT_ADDRESS is unset (no vault), the watcher falls back to sending each
# queued row via the direct hot-wallet transfer (no batch, no gas saving).
WITHDRAW_BATCH_FLUSH_SECONDS: int = int(os.environ.get("WITHDRAW_BATCH_FLUSH_SECONDS", "60"))
WITHDRAW_BATCH_FLUSH_COUNT: int = int(os.environ.get("WITHDRAW_BATCH_FLUSH_COUNT", "20"))
WITHDRAW_BATCH_FLUSH_USDC: Decimal = Decimal(os.environ.get("WITHDRAW_BATCH_FLUSH_USDC", "50"))
WITHDRAW_BATCH_FALLBACK_DIRECT: bool = os.environ.get("WITHDRAW_BATCH_FALLBACK_DIRECT", "1") == "1"

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
AI_MODEL: str = os.environ.get("AI_MODEL", "openai/gpt-oss-120b")
AI_TIMEOUT_SECONDS: int = int(os.environ.get("AI_TIMEOUT_SECONDS", "45"))
AI_COOLDOWN_SECONDS: int = int(os.environ.get("AI_COOLDOWN_SECONDS", "15"))
AI_MAX_QUESTION_LEN: int = int(os.environ.get("AI_MAX_QUESTION_LEN", "1000"))
AI_MAX_ANSWER_CHARS: int = int(os.environ.get("AI_MAX_ANSWER_CHARS", "3500"))

# Agent: Telegram user ID for the autonomous agent (0 = disabled)
AGENT_TG_ID: int = int(os.environ.get("AGENT_TG_ID", "0") or "0")

# Solvency alerting: Telegram chat/user ID for emergency alerts
SOLVENCY_ALERT_CHAT_ID: int = int(
    (os.environ.get("SOLVENCY_ALERT_CHAT_ID", "") or os.environ.get("ADMIN_TG_ID", "") or "0").strip() or "0"
)

# CREATE2 factory contract address (empty = disabled)
CREATE2_FACTORY_ADDRESS: str = os.environ.get("CREATE2_FACTORY_ADDRESS", "")
# Deployed USDCForwarder implementation the factory's proxies delegatecall.
CREATE2_FACTORY_FORWARDER: str = os.environ.get("CREATE2_FACTORY_FORWARDER", "")
# Full EIP-1167 proxy creation bytecode (prefix + forwarder + suffix). Must
# match what Create2Factory deploys so offline address derivation matches.
CREATE2_PROXY_BYTECODE: str = os.environ.get("CREATE2_PROXY_BYTECODE", "").lower()
# Operator opt-in (see bot/create2.py): CREATE2 stays disabled until set to "1"
# AND a factory + forwarder are configured.
CREATE2_SAFE_DEPOSITS: bool = os.environ.get("CREATE2_SAFE_DEPOSITS", "") == "1"

# Oracle for on-chain prediction markets (address + private key)
ORACLE_ADDRESS: str = os.environ.get("ORACLE_ADDRESS", "")
ORACLE_PRIVATE_KEY: str = os.environ.get("ORACLE_PRIVATE_KEY", "")

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

# USDC on Base mainnet (well-audited, no custom contracts in MVP).
# Override for testnet (Base Sepolia: 0x036CbD53842c5426634e7929541eC2318f3dCF7e).
USDC_ADDRESS: str = os.environ.get(
    "USDC_ADDRESS", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
)
USDC_DECIMALS = 6

# Wrapped ETH on Base (canonical address shared with the OP stack).
WETH_ADDRESS = "0x4200000000000000000000000000000000000006"

# Expected Base chain id (8453). Set 0 to disable the chain-identity guard
# (not recommended in production — it exists to stop a misconfigured RPC
# from moving hot-wallet funds onto another network).
EXPECTED_CHAIN_ID: int = int(os.environ.get("EXPECTED_CHAIN_ID", "8453"))

# Chainlink oracle feeds on Base (price reads / L2 availability).
# ETH/USD aggregator and USDC/USD aggregator, plus the Base sequencer uptime
# feed. Adjust to the current published address set for your deployment.
CHAINLINK_ETH_USD_FEED: str = os.environ.get(
    "CHAINLINK_ETH_USD_FEED",
    "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",  # ETH/USD (Base)
)
CHAINLINK_USDC_USD_FEED: str = os.environ.get(
    "CHAINLINK_USDC_USD_FEED",
    "0x7e860098F58bBFC8648a4311b374B1D669aB2f0f",  # USDC/USD (Base)
)
CHAINLINK_L2_SEQUENCER_FEED: str = os.environ.get(
    "CHAINLINK_L2_SEQUENCER_FEED",
    "0x7A94057f40E4d9c4E5a9E2A90Aeaf7A428C0BbC2",  # Base sequencer uptime
)
# Oracle staleness windows: ETH updates per block (short), USDC on a long
# heartbeat (wider), sequencer uptime (generous).
PRICE_FEED_MAX_AGE_SECONDS: int = int(os.environ.get("PRICE_FEED_MAX_AGE_SECONDS", "3600"))
USDC_PRICE_FEED_MAX_AGE_SECONDS: int = int(os.environ.get("USDC_PRICE_FEED_MAX_AGE_SECONDS", str(24 * 3600)))
PRICE_CACHE_SECONDS: int = int(os.environ.get("PRICE_CACHE_SECONDS", "60"))

# Aerodrome DEX (largest AMM on Base) — router + factory for executable quotes.
AERODROME_ROUTER_ADDRESS: str = os.environ.get(
    "AERODROME_ROUTER_ADDRESS",
    "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",  # Aerodrome V2 router (Base)
)
AERODROME_FACTORY_ADDRESS: str = os.environ.get(
    "AERODROME_FACTORY_ADDRESS",
    "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",  # Aerodrome factory (Base)
)

# Basenames (Base name service) on-chain contracts.
BASE_L2_RESOLVER_ADDRESS: str = os.environ.get(
    "BASE_L2_RESOLVER_ADDRESS",
    "0xC6d566A56A1aFf6508b41f6c90ff131615583BCD",  # Base L2 public resolver
)
BASE_REVERSE_REGISTRAR_ADDRESS: str = os.environ.get(
    "BASE_REVERSE_REGISTRAR_ADDRESS",
    "0x0AfD16d8D0B5a7b4C0AA13a7e7F0BB6d2b52A4AC",  # ENSIP-19 reverse registrar
)
BASE_REGISTRY_ADDRESS: str = os.environ.get(
    "BASE_REGISTRY_ADDRESS",
    "0x4cCb0BB02FCABA27e82a56646E81d3c12C45C903",  # Basenames registry
)

# On-chain treasury (TipBotVault, see contracts/). Users deposit into the
# vault contract; the dashboard reads its USDC balance as the primary
# proof-of-reserves. Leave empty to keep the hot wallet as the sole reserve.
VAULT_ADDRESS: str | None = os.environ.get("VAULT_ADDRESS", "").strip() or None

# On-chain LMSR markets (contracts/OutcomeMarket.sol, see bot/onchain_market.py).
# Empty -> on-chain markets are simply off; the existing off-chain ones in
# ledger.py are unaffected either way.
OUTCOME_MARKET_ADDRESS: str | None = os.environ.get("OUTCOME_MARKET_ADDRESS", "").strip() or None
# A fresh custodial wallet has 0 ETH, so its first on-chain tx would fail
# outright — onchain_market tops it up from the hot wallet when it dips below
# the threshold. Both in whole ETH (Base gas is cheap; this is a few cents).
GAS_DRIP_ETH: Decimal = Decimal(os.environ.get("GAS_DRIP_ETH", "0.0002"))
GAS_DRIP_THRESHOLD_ETH: Decimal = Decimal(os.environ.get("GAS_DRIP_THRESHOLD_ETH", "0.00005"))
# Global safety cap: max gas drips the hot wallet performs per UTC day across
# ALL user wallets (blocks drip-farming with many fresh wallets).
GAS_DRIP_DAILY_MAX: int = int(os.environ.get("GAS_DRIP_DAILY_MAX", "50"))
# Daily cap on TOTAL on-chain market subsidies (/oc_create) across ALL
# creators — protects the treasury from market-creation spam.
MARKET_SUBSIDY_DAILY_MAX_USDC: Decimal = Decimal(os.environ.get("MARKET_SUBSIDY_DAILY_MAX_USDC", "2000"))


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

    # A dedicated, random SECRET_KEY is REQUIRED in production. Without it the
    # session HMAC key is derived from BOT_TOKEN; anyone who ever sees BOT_TOKEN
    # could then forge a signed session cookie for ANY Telegram user and drain
    # funds via the authenticated /api/mini/* money endpoints.
    if not os.environ.get("SECRET_KEY", "").strip():
        raise ValueError(
            "SECRET_KEY must be set explicitly (independent of BOT_TOKEN). "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    _sk = os.environ.get("SECRET_KEY", "").strip()
    if len(_sk) < 32:
        raise ValueError(f"SECRET_KEY must be >= 32 chars, got {len(_sk)}")

    # WALLET_ENC_KEY is REQUIRED: without it bot/wallets.py silently encrypts
    # every per-user wallet key/seed with a key derived from HOT_WALLET_KEY,
    # collapsing the isolation between the two secrets.
    _wek = os.environ.get("WALLET_ENC_KEY")
    if not _wek or len(_wek) < 32:
        raise ValueError(
            "WALLET_ENC_KEY must be set to a 32+ byte random value; never reuse "
            "HOT_WALLET_KEY. Generate with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    if _wek == os.environ.get("HOT_WALLET_KEY", "").strip():
        raise ValueError("WALLET_ENC_KEY must NOT equal HOT_WALLET_KEY")

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
