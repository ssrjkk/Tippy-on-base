"""Reporting indexes on tx_log.

volume_history / user_stats / top_tippers filter the append-only tx_log table
on (kind, created_at) and (kind, counterparty); without composite indexes those
requests scan the whole table. Mirrors bot/ledger.py SCHEMA_DDL.

Revision ID: 005
Revises: 004
Create Date: 2026-09-12
"""
from typing import Sequence

from alembic import op

revision: str = "005"
down_revision: str | Sequence[str] | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tx_log_kind_ct "
        "ON tx_log (kind, created_at);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tx_log_kind_cp "
        "ON tx_log (kind, counterparty);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tx_log_kind_cp;")
    op.execute("DROP INDEX IF EXISTS idx_tx_log_kind_ct;")