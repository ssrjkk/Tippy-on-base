"""Multi-wallet support — up to MAX_WALLETS_PER_USER wallets per user.

Revision ID: 003
Revises: 002
Create Date: 2026-08-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add id column as new primary key
    op.execute("""
        ALTER TABLE user_wallets ADD COLUMN id BIGSERIAL;
    """)

    # Make tg_id non-unique (allow multiple wallets per user)
    # Drop old PK constraint (name varies by how it was created)
    op.execute("""
        ALTER TABLE user_wallets DROP CONSTRAINT IF EXISTS user_wallets_pkey;
    """)

    # Add new PK on id
    op.execute("""
        ALTER TABLE user_wallets ADD PRIMARY KEY (id);
    """)

    # Add slot and active columns
    op.execute("""
        ALTER TABLE user_wallets ADD COLUMN slot INT NOT NULL DEFAULT 1;
        ALTER TABLE user_wallets ADD COLUMN active BOOLEAN NOT NULL DEFAULT true;
    """)

    # Unique index: one wallet per slot per user
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_wallets_user_slot
            ON user_wallets (tg_id, slot);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS uq_wallets_user_slot;
        ALTER TABLE user_wallets DROP COLUMN IF EXISTS active;
        ALTER TABLE user_wallets DROP COLUMN IF EXISTS slot;
    """)
    # Revert to tg_id PK (drop id column)
    op.execute("""
        ALTER TABLE user_wallets DROP CONSTRAINT IF EXISTS user_wallets_pkey;
        ALTER TABLE user_wallets DROP COLUMN IF EXISTS id;
        ALTER TABLE user_wallets ADD PRIMARY KEY (tg_id);
    """)
