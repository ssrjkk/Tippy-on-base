"""Agent configuration — spend caps, circuit breakers, API endpoints.

All limits are enforced in code, never in the LLM prompt.
"""

import os

# --- Spend caps (USDC, not micro) ---------------------------------------------------
DAILY_SPEND_CAP_USDC = float(os.environ.get("AGENT_DAILY_CAP", "50"))
PER_TX_CAP_USDC = float(os.environ.get("AGENT_TX_CAP", "10"))
MAX_ACTIONS_PER_HOUR = int(os.environ.get("AGENT_ACTIONS_PER_HOUR", "20"))
MAX_BET_OWN_MARKETS_PCT = 0.10  # 10% of pool max on own markets

# --- Circuit breaker ----------------------------------------------------------------
MAX_CONSECUTIVE_ERRORS = int(os.environ.get("AGENT_MAX_ERRORS", "3"))
COOLDOWN_SECONDS = int(os.environ.get("AGENT_COOLDOWN_SECS", "300"))

# --- API endpoints ------------------------------------------------------------------
TIPPY_BASE_URL = os.environ.get("TIPPY_BASE_URL", "http://localhost:8000")
AGENT_TG_ID = int(os.environ.get("AGENT_TG_ID", "0"))  # 0 = unauthenticated demo

# --- News sources -------------------------------------------------------------------
CRYPTOPANIC_RSS = "https://cryptopanic.com/api/free/v1/posts/?auth_token=&public=true"
NEWS_CHECK_INTERVAL = int(os.environ.get("AGENT_NEWS_INTERVAL", "300"))  # seconds

# --- LLM ---------------------------------------------------------------------------
LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "gpt-4o-mini")
LLM_BUDGET_DAILY = float(os.environ.get("AGENT_LLM_BUDGET", "5.00"))  # USD/day

# --- Chain --------------------------------------------------------------------------
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
