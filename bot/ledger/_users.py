"""Ledger domain mixin: LedgerUsersMixin (split from bot/ledger.py)."""
import secrets
import time
from decimal import Decimal

from .. import config
from ._base import MICRO


class LedgerUsersMixin:
    def ensure_user(self, tg_id: int, username: str | None, commit: bool = True) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (tg_id, username) VALUES (%s, %s) ON CONFLICT (tg_id) DO NOTHING",
                (tg_id, username),
            )
            self._conn.execute(
                "UPDATE users SET username = %s WHERE tg_id = %s AND %s::text IS NOT NULL",
                (username, tg_id, username),
            )
            if commit:
                self._conn.commit()



    def user_exists(self, tg_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return row is not None



    def consume_login_nonce(self, nonce: str) -> bool:
        """Atomically mark a wallet-login nonce as used; True only on first use.

        Replay of a signed (message, signature) pair reuses the same Nonce line,
        so the second attempt hits the PK conflict and is rejected.
        """
        import hashlib as _hashlib

        nonce_hash = _hashlib.sha256(nonce.encode()).hexdigest()
        with self._lock:
            try:
                # Opportunistic prune: login nonces are consumed once and are
                # useless after their TTL; sweep them so the table never grows
                # without bound. Login attempts are rare, so the extra DELETE
                # is negligible.
                self._conn.execute(
                    "DELETE FROM login_nonces WHERE created_at < %s",
                    (int(time.time()) - config.LOGIN_NONCE_TTL_SECONDS,),
                )
                cur = self._conn.execute(
                    "INSERT INTO login_nonces (nonce_hash) VALUES (%s) ON CONFLICT (nonce_hash) DO NOTHING RETURNING nonce_hash",
                    (nonce_hash,),
                )
                claimed = cur.fetchone() is not None
                self._conn.commit()
                return claimed
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                return False



    def all_users(self) -> list[dict]:
        with self._lock:
            return self._conn.execute("SELECT tg_id FROM users").fetchall()



    def find_by_username(self, username: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tg_id FROM users WHERE LOWER(username) = LOWER(%s)",
                (username,),
            ).fetchone()
        return row["tg_id"] if row else None



    def username_of(self, tg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT username FROM users WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return row["username"] if row else None



    def balance(self, tg_id: int) -> Decimal:
        with self._lock:
            row = self._conn.execute(
                "SELECT balance FROM users WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return Decimal(row["balance"] if row else 0) / Decimal(MICRO)



    def set_username(self, tg_id: int, username: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET username = %s WHERE tg_id = %s", (username, tg_id)
            )
            self._conn.commit()



    def new_link_nonce(self, tg_id: int, address: str) -> str:
        with self._lock:
            self.ensure_user(tg_id, None)
            nonce = secrets.token_hex(8)
            self._conn.execute(
                "INSERT INTO link_nonces (tg_id, address, nonce, created_at) "
                "VALUES (%s, %s, %s, (EXTRACT(EPOCH FROM now())::bigint)) "
                "ON CONFLICT (tg_id) DO UPDATE SET "
                "address = EXCLUDED.address, nonce = EXCLUDED.nonce, "
                "created_at = EXCLUDED.created_at",
                (tg_id, address, nonce),
            )
            self._conn.commit()
        return nonce



    def get_link_nonce(self, tg_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT address, nonce, created_at FROM link_nonces WHERE tg_id = %s",
                (tg_id,),
            ).fetchone()



    def confirm_link(self, tg_id: int, address: str, nonce: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT address, created_at FROM link_nonces WHERE tg_id = %s AND nonce = %s",
                (tg_id, nonce),
            ).fetchone()
            if not row or row["address"].lower() != address.lower():
                return False
            if int(time.time()) - int(row["created_at"]) > config.LINK_NONCE_TTL_SECONDS:
                self._conn.execute("DELETE FROM link_nonces WHERE tg_id = %s", (tg_id,))
                self._conn.commit()
                return False
            self._conn.execute(
                "INSERT INTO wallet_links (tg_id, address) VALUES (%s, %s) "
                "ON CONFLICT (tg_id) DO UPDATE SET address = EXCLUDED.address",
                (tg_id, address),
            )
            self._conn.execute(
                "DELETE FROM link_nonces WHERE tg_id = %s", (tg_id,)
            )
            self._conn.commit()
            return True



    def linked_address(self, tg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT address FROM wallet_links WHERE tg_id = %s", (tg_id,)
            ).fetchone()
        return row["address"] if row else None



    def tg_id_of_address(self, address: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tg_id FROM wallet_links WHERE LOWER(address) = LOWER(%s)",
                (address,),
            ).fetchone()
        return row["tg_id"] if row else None



    def set_create2_proxy(self, tg_id: int, proxy_address: str) -> None:
        """Record a CREATE2 proxy address for a tg_id (upsert).

        Only touches create2_proxies — the deposit scanner resolves the owner
        via tg_id_of_proxy. Never writes wallet_links so a user's own /link stays
        untouched (wallet_links.address is UNIQUE and owned by the /link flow).
        """
        with self._lock:
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "INSERT INTO create2_proxies (tg_id, proxy_address) "
                "VALUES (%s, %s) "
                "ON CONFLICT (tg_id) DO UPDATE SET proxy_address = EXCLUDED.proxy_address",
                (tg_id, proxy_address.lower()),
            )
            self._conn.commit()



    def tg_id_of_proxy(self, proxy_address: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tg_id FROM create2_proxies WHERE LOWER(proxy_address) = LOWER(%s)",
                (proxy_address,),
            ).fetchone()
        return row["tg_id"] if row else None



    def create2_proxy_of(self, tg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT proxy_address FROM create2_proxies WHERE tg_id = %s",
                (tg_id,),
            ).fetchone()
        return row["proxy_address"] if row else None



    def list_create2_proxies(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tg_id, proxy_address, deployed FROM create2_proxies ORDER BY tg_id"
            ).fetchall()
        return [dict(r) for r in rows]
