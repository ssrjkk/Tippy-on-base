"""Container entrypoint: run DB migrations (serialized via Postgres advisory
lock), then exec the combined runner.

Python replacement for deploy/entrypoint.sh: the Verdent runtime resolves the
image ENTRYPOINT as a direct process path and cannot load the shell script.
"""
import os
import subprocess
import sys

try:
    os.chdir("/app")
except OSError:
    pass  # not in the container (local run) — stay in the repo root


def run_migrations() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    import psycopg

    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        conn.execute("SELECT pg_advisory_lock(hashtext('tippy_alembic'))")
        try:
            return subprocess.call(
                [sys.executable, "-m", "alembic", "upgrade", "head"]
            )
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('tippy_alembic'))")
    finally:
        conn.close()


if __name__ == "__main__":
    code = run_migrations()
    if code != 0:
        sys.exit(code)
    os.execv(sys.executable, [sys.executable, "-m", "deploy.run"])
