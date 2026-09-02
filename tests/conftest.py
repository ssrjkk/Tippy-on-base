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
os.environ.setdefault("BOT_TOKEN", "0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
os.environ.setdefault("HOT_WALLET_KEY", "0x" + "11" * 32)
os.environ.setdefault("WALLET_ENC_KEY", "a" * 32)  # Test-only: 32-char key for wallet encryption
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci-only-32ch!")
os.environ.setdefault("X402_ENABLED", "1")
os.environ.setdefault("X402_RECEIVE_ADDRESS", "0x0000000000000000000000000000000000000001")
os.environ.setdefault("ADMIN_TG_ID", "111")
# Tests fake Base MAINNET (chain 8453); the repo .env points to Sepolia.
os.environ.setdefault("EXPECTED_CHAIN_ID", "8453")
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
    "suspicious_activity", "community_treasuries", "treasury_transactions",
    "treasury_proposals", "treasury_votes", "onchain_markets", "onchain_trades", "gas_drips",
    "notification_outbox", "create2_proxies",
]


def _reset_db(ledger) -> None:
    ledger._conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
    ledger._conn.commit()


@pytest.fixture(scope="session", autouse=True)
def _pg_test_db():
    import time

    import psycopg

    # DROP ... WITH (FORCE) kills backends left over from a previous run;
    # right after a heavy suite those backends can outlive the client for a
    # moment (and a different-role owner cannot be terminated), so retry.
    last = None
    for attempt in range(5):
        try:
            with psycopg.connect(TEST_ADMIN_URL, connect_timeout=3, autocommit=True) as admin:
                admin.execute("DROP DATABASE IF EXISTS tipbot_test WITH (FORCE)")
                admin.execute("CREATE DATABASE tipbot_test")
            last = None
            break
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    if last is not None:
        pytest.exit(f"PostgreSQL not available: {last}", returncode=1)
    yield


@pytest.fixture(autouse=True)
def _no_create2(monkeypatch):
    """Disable the CREATE2 deposit flow for tests by default.

    The real .env wires a live factory + forwarder; handler tests (e.g.
    cmd_deposit, e2e user journey) must not deploy proxies to Mainnet/Base.
    test_create2.py re-enables it explicitly via its own monkeypatches.
    """
    import bot.create2

    monkeypatch.setattr(bot.create2, "FACTORY_ADDRESS", None)
    monkeypatch.setattr(bot.create2, "CREATE2_SAFE_DEPOSITS", False)


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
    import web.frame
    import web.mini
    import web.server
    import web.x402

    monkeypatch.setattr(web.server, "ledger", async_fresh)
    monkeypatch.setattr(web.auth, "ledger", async_fresh)
    for _mod in (web.mini, web.frame, web.x402):
        if hasattr(_mod, "ledger"):
            monkeypatch.setattr(_mod, "ledger", async_fresh)
    # tip_targets holds its own module reference to the ledger proxy —
    # rebind it too, or basename resolution would hit the DEV database.
    import bot.tip_targets
    monkeypatch.setattr(bot.tip_targets, "ledger", async_fresh)
    handlers._common._money_cmd_last.clear()
    web.server._rl_state.clear()
    yield fresh
    fresh.close()  # release the open transaction, or TRUNCATE in the next test hangs
