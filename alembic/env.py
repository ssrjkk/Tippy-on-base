"""Alembic environment for Tippy ledger migrations.

Reads DATABASE_URL from the environment (same as bot.config).  The
``env.py`` is intentionally minimal — Alembic is used only for schema
versioning and migration tracking.  The actual DDL still lives in
``bot/ledger.py`` (SCHEMA_DLL) for backward compatibility with the
``ensure_schema()`` fallback.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment if available.
db_url = os.environ.get("DATABASE_URL")
if db_url:
    # The project's driver is psycopg v3 (psycopg[binary]); SQLAlchemy defaults
    # postgresql:// to psycopg2, which may not be installed. Pin the driver so
    # `alembic upgrade head` works everywhere (Docker and bare-metal alike).
    if db_url.startswith("postgresql://"):
        db_url = "postgresql+psycopg://" + db_url.split("://", 1)[1]
    config.set_main_option("sqlalchemy.url", db_url)

# Alembic serializes concurrent `upgrade head` runs with a session-level
# advisory lock. When a second process/thread starts migrations at the same
# time (e.g. the combined bot+web runner creating two Ledger() instances, or a
# web process racing the entrypoint), the second one would otherwise block on
# that lock forever and hang the process. lock_timeout makes the loser raise
# `lock_not_available` instead; the caller (Ledger._run_alembic) treats that
# as "schema already being migrated" and falls back to ensure_schema().
# statement_timeout guards against pathological long-held locks.
LOCK_TIMEOUT_MS = os.environ.get("ALEMBIC_LOCK_TIMEOUT_MS", "15000")
STATEMENT_TIMEOUT_MS = os.environ.get("ALEMBIC_STATEMENT_TIMEOUT_MS", "60000")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql(f"SET lock_timeout = {LOCK_TIMEOUT_MS}")
        connection.exec_driver_sql(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
