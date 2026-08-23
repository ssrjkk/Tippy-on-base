"""Initial schema — all tables from bot/ledger.py SCHEMA_DDL.

Revision ID: 001
Revises: None
Create Date: 2026-08-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id     BIGINT PRIMARY KEY,
            username  TEXT,
            balance   BIGINT NOT NULL DEFAULT 0,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS tx_log (
            id        BIGSERIAL PRIMARY KEY,
            kind      TEXT NOT NULL,
            tg_id     BIGINT NOT NULL,
            counterparty TEXT,
            amount    BIGINT NOT NULL,
            tx_hash   TEXT,
            note      TEXT,
            status    TEXT,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS pending_deposits (
            tx_hash      TEXT PRIMARY KEY,
            sender       TEXT NOT NULL,
            amount_micro BIGINT NOT NULL,
            claimed      BIGINT NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS link_nonces (
            tg_id       BIGINT PRIMARY KEY,
            address     TEXT NOT NULL,
            nonce       TEXT NOT NULL,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS wallet_links (
            tg_id     BIGINT PRIMARY KEY,
            address   TEXT NOT NULL UNIQUE,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS bets (
            id          BIGSERIAL PRIMARY KEY,
            creator     BIGINT NOT NULL,
            question    TEXT NOT NULL,
            options     TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'open',
            winner      BIGINT,
            close_at    BIGINT,
            deadline_notified BIGINT NOT NULL DEFAULT 0,
            grace_warned BIGINT NOT NULL DEFAULT 0,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS bet_positions (
            id           BIGSERIAL PRIMARY KEY,
            bet_id       BIGINT NOT NULL,
            tg_id        BIGINT NOT NULL,
            option_idx   BIGINT NOT NULL,
            amount_micro BIGINT NOT NULL,
            created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE INDEX IF NOT EXISTS idx_bet_positions_bet ON bet_positions (bet_id);
        CREATE TABLE IF NOT EXISTS last_block (
            id    BIGINT PRIMARY KEY CHECK (id = 1),
            block BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS message_authors (
            chat_id    BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            tg_id      BIGINT NOT NULL,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            PRIMARY KEY (chat_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS reaction_tips (
            chat_id    BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            tg_id      BIGINT NOT NULL,
            amount_micro BIGINT NOT NULL,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            PRIMARY KEY (chat_id, message_id, tg_id)
        );
        CREATE INDEX IF NOT EXISTS idx_reaction_tips_tg ON reaction_tips (tg_id);
        CREATE TABLE IF NOT EXISTS user_settings (
            tg_id         BIGINT PRIMARY KEY,
            reaction_tips BIGINT NOT NULL DEFAULT 1,
            notify_deposits BIGINT NOT NULL DEFAULT 1,
            lang          TEXT NOT NULL DEFAULT 'ru'
        );
        CREATE TABLE IF NOT EXISTS user_wallets (
            tg_id      BIGINT PRIMARY KEY,
            address    TEXT NOT NULL UNIQUE,
            key_enc    TEXT NOT NULL,
            seed_enc   TEXT NOT NULL,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS x402_payments (
            tx_hash      TEXT PRIMARY KEY,
            recipient_tg BIGINT NOT NULL,
            amount_micro BIGINT NOT NULL,
            sender       TEXT NOT NULL,
            created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS paywall_items (
            id          BIGSERIAL PRIMARY KEY,
            owner_tg    BIGINT NOT NULL,
            title       TEXT NOT NULL,
            price_micro BIGINT NOT NULL,
            content     TEXT NOT NULL,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS paywall_purchases (
            id          BIGSERIAL PRIMARY KEY,
            item_id     BIGINT NOT NULL,
            buyer_tg    BIGINT,
            tx_hash     TEXT,
            amount_micro BIGINT NOT NULL,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            UNIQUE (item_id, buyer_tg),
            UNIQUE (item_id, tx_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_paywall_purchases_item ON paywall_purchases (item_id);
        CREATE TABLE IF NOT EXISTS paywall_channels (
            chat_id     BIGINT PRIMARY KEY,
            owner_tg    BIGINT NOT NULL,
            price_micro BIGINT NOT NULL,
            period_days BIGINT NOT NULL DEFAULT 30,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS paywall_subscriptions (
            chat_id    BIGINT NOT NULL,
            tg_id      BIGINT NOT NULL,
            expires_at BIGINT NOT NULL,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            PRIMARY KEY (chat_id, tg_id)
        );
        CREATE INDEX IF NOT EXISTS idx_paywall_subscriptions_expires
            ON paywall_subscriptions (expires_at);
        CREATE TABLE IF NOT EXISTS markets (
            id            BIGSERIAL PRIMARY KEY,
            creator       BIGINT NOT NULL,
            question      TEXT NOT NULL,
            options       TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'open',
            winner        BIGINT,
            close_at      BIGINT,
            subsidy_micro BIGINT NOT NULL,
            b_micro       BIGINT NOT NULL,
            escrow_micro  BIGINT NOT NULL DEFAULT 0,
            deadline_notified BIGINT NOT NULL DEFAULT 0,
            grace_warned  BIGINT NOT NULL DEFAULT 0,
            created_at    BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS market_shares (
            market_id  BIGINT NOT NULL,
            tg_id      BIGINT NOT NULL,
            option_idx BIGINT NOT NULL,
            shares     BIGINT NOT NULL DEFAULT 0,
            cost_micro BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (market_id, tg_id, option_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_market_shares_user ON market_shares (tg_id);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS market_shares;
        DROP TABLE IF EXISTS markets;
        DROP TABLE IF EXISTS paywall_subscriptions;
        DROP TABLE IF EXISTS paywall_channels;
        DROP TABLE IF EXISTS paywall_purchases;
        DROP TABLE IF EXISTS paywall_items;
        DROP TABLE IF EXISTS x402_payments;
        DROP TABLE IF EXISTS user_wallets;
        DROP TABLE IF EXISTS user_settings;
        DROP TABLE IF EXISTS reaction_tips;
        DROP TABLE IF EXISTS message_authors;
        DROP TABLE IF EXISTS last_block;
        DROP TABLE IF EXISTS bet_positions;
        DROP TABLE IF EXISTS bets;
        DROP TABLE IF EXISTS wallet_links;
        DROP TABLE IF EXISTS link_nonces;
        DROP TABLE IF EXISTS pending_deposits;
        DROP TABLE IF EXISTS tx_log;
        DROP TABLE IF EXISTS users;
    """)
