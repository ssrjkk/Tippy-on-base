#!/bin/sh
set -e

echo "Running alembic migrations (serialized via Postgres advisory lock)..."
# Multiple replicas could race `alembic upgrade head`; take a Postgres
# advisory lock so exactly one instance runs migrations while the others wait.
python - <<'PY'
import asyncio
import os
import subprocess
import sys

import psycopg


async def main() -> int:
    conn = await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"], autocommit=True
    )
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtext('tippy_alembic'))")
        try:
            return subprocess.call(
                [sys.executable, "-m", "alembic", "upgrade", "head"]
            )
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock(hashtext('tippy_alembic'))"
            )
    finally:
        await conn.close()


sys.exit(asyncio.run(main()))
PY

echo "Starting Tippy..."
exec python -m deploy.run