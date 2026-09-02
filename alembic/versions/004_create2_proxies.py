"""CREATE2 per-user deposit proxies.

Each user gets a deterministic deposit address via the CREATE2 factory.
The proxy address (for a tg_id) is stored so the deposit scanner can map a
proxy that forwarded USDC back to its owner and auto-credit them.

Revision ID: 004
Revises: 003
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS create2_proxies (
            tg_id         BIGINT PRIMARY KEY,
            proxy_address TEXT NOT NULL,
            deployed      BOOLEAN NOT NULL DEFAULT false,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_create2_proxies_address
            ON create2_proxies (LOWER(proxy_address));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS create2_proxies;")