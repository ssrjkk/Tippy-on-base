"""Test fixtures: a fresh Postgres test database, patched into bot modules.

Requires a running PostgreSQL server (docker compose up -d db — the default
credentials below match the compose service). A dedicated test database is
dropped and recreated once per session; every test gets a truncated ledger.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Config reads these at import time.
os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ.setdefault("HOT_WALLET_KEY", "0x" + "11" * 32)
os.environ.setdefault("WALLET_ENC_KEY", "a" * 32)  # Test-only: 32-char key for wallet encryption
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5432/tipbot_test"
)
# Where to connect to CREATE/DROP the test database.
TEST_ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5432/postgres"
)

TABLES = [
    "users", "tx_log", "pending_deposits", "link_nonces", "wallet_links",
    "bets", "bet_positions", "last_block", "message_authors", "reaction_tips",
    "user_settings", "user_wallets", "x402_payments", "paywall_items", "paywall_purchases",
    "paywall_channels", "paywall_subscriptions", "markets", "market_shares",
    "suspicious_activity",
]


def _reset_db(ledger) -> None:
    ledger._conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
    ledger._conn.commit()


@pytest.fixture(scope="session", autouse=True)
def _pg_test_db():
    import psycopg

    try:
        with psycopg.connect(TEST_ADMIN_URL, connect_timeout=3) as admin:
            admin.execute("DROP DATABASE IF EXISTS tipbot_test WITH (FORCE)")
            admin.execute("CREATE DATABASE tipbot_test")
    except Exception as e:
        pytest.skip(f"PostgreSQL not available: {e}")
    yield


@pytest.fixture()
def ledger(monkeypatch):
    from bot import handlers
    from bot import ledger as ledger_mod

    fresh = ledger_mod.Ledger(TEST_DB_URL)
    _reset_db(fresh)
    monkeypatch.setattr(ledger_mod, "ledger", fresh)
    # Handlers now call the async proxy; wrap the fresh instance so awaited
    # ledger calls hit the hermetic test database.
    async_fresh = ledger_mod.AsyncLedger(fresh)
    monkeypatch.setattr(ledger_mod, "async_ledger", async_fresh)
    monkeypatch.setattr(handlers._common, "ledger", async_fresh)
    # Web modules bind the singleton at import time; rebind them so web
    # tests are hermetic (test database, not whatever DATABASE_URL points to).
    # Wrap in AsyncLedger: the routes now `await ledger.x()`, so the injected
    # ledger must be awaitable too.
    import web.auth
    import web.server
    import web.mini
    import web.frame
    import web.x402

    monkeypatch.setattr(web.server, "ledger", async_fresh)
    monkeypatch.setattr(web.auth, "ledger", async_fresh)
    for _mod in (web.mini, web.frame, web.x402):
        if hasattr(_mod, "ledger"):
            monkeypatch.setattr(_mod, "ledger", async_fresh)
    handlers._common._money_cmd_last.clear()
    web.server._rl_state.clear()
    yield fresh
    fresh.close()  # release the open transaction, or TRUNCATE in the next test hangs
