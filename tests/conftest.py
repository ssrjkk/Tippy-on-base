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
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5433/tipbot_test"
)
# Where to connect to CREATE/DROP the test database.
TEST_ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5433/postgres"
)

TABLES = [
    "users", "tx_log", "pending_deposits", "link_nonces", "wallet_links",
    "bets", "bet_positions", "last_block", "message_authors", "reaction_tips",
    "user_settings", "user_wallets", "x402_payments", "paywall_items", "paywall_purchases",
    "paywall_channels", "paywall_subscriptions",
]


def _reset_db(ledger) -> None:
    ledger._conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
    ledger._conn.commit()


@pytest.fixture(scope="session", autouse=True)
def _pg_test_db():
    import psycopg

    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS tipbot_test WITH (FORCE)")
        admin.execute("CREATE DATABASE tipbot_test")
    yield


@pytest.fixture()
def ledger(monkeypatch):
    from bot import handlers
    from bot import ledger as ledger_mod

    fresh = ledger_mod.Ledger(TEST_DB_URL)
    _reset_db(fresh)
    monkeypatch.setattr(ledger_mod, "ledger", fresh)
    monkeypatch.setattr(handlers, "ledger", fresh)
    handlers._money_cmd_last.clear()
    yield fresh
    fresh.close()  # release the open transaction, or TRUNCATE in the next test hangs
