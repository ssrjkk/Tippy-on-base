"""Treasury + AML tables — reconcile with bot/ledger.py _ensure_schema().

The initial schema (001) predated the community-treasury and AML-monitor
features. ledger._ensure_schema() creates these tables at runtime, but any
deploy path that provisions the database via `alembic upgrade head` would be
missing them and crash the treasury flows / AML monitor. This migration makes
the two schema sources agree.

Revision ID: 002
Revises: 001
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS suspicious_activity (
            id          BIGSERIAL PRIMARY KEY,
            tg_id       BIGINT NOT NULL,
            kind        TEXT NOT NULL,
            details     TEXT NOT NULL,
            severity    TEXT NOT NULL DEFAULT 'info',
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE INDEX IF NOT EXISTS idx_suspicious_tg ON suspicious_activity (tg_id);
        CREATE INDEX IF NOT EXISTS idx_suspicious_created ON suspicious_activity (created_at);

        CREATE TABLE IF NOT EXISTS community_treasuries (
            id          BIGSERIAL PRIMARY KEY,
            chat_id     BIGINT NOT NULL UNIQUE,
            owner_tg    BIGINT NOT NULL,
            balance     BIGINT NOT NULL DEFAULT 0,
            quorum_pct  INTEGER NOT NULL DEFAULT 50,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS treasury_transactions (
            id          BIGSERIAL PRIMARY KEY,
            treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
            kind        TEXT NOT NULL,
            tg_id       BIGINT,
            amount      BIGINT NOT NULL,
            note        TEXT,
            tx_hash     TEXT,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS treasury_proposals (
            id          BIGSERIAL PRIMARY KEY,
            treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
            proposer_tg BIGINT NOT NULL,
            amount      BIGINT NOT NULL,
            to_address  TEXT NOT NULL,
            description TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'voting',
            votes_yes   INTEGER NOT NULL DEFAULT 0,
            votes_no    INTEGER NOT NULL DEFAULT 0,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            closes_at   BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS treasury_votes (
            id          BIGSERIAL PRIMARY KEY,
            treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
            proposal_id BIGINT NOT NULL,
            tg_id       BIGINT NOT NULL,
            vote        INTEGER NOT NULL,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            UNIQUE (proposal_id, tg_id)
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS treasury_votes;
        DROP TABLE IF EXISTS treasury_proposals;
        DROP TABLE IF EXISTS treasury_transactions;
        DROP TABLE IF EXISTS community_treasuries;
        DROP TABLE IF EXISTS suspicious_activity;
    """)
