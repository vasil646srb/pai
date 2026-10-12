"""Private guest rooms: name and password chosen by the guest, unlock tokens stored hashed."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid

PBKDF_ITERS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF_ITERS)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF_ITERS)
    except ValueError:
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class GuestRoomsMixin:
    """Expects `_conn` and `_lock` from StorageCore."""

    def create_guest_room(self, name: str, password: str) -> dict:
        rid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO guest_rooms (id, name, created_at, pw_hash) VALUES (?,?,?,?)",
                (rid, name, now, hash_password(password)),
            )
            self._conn.commit()
        return {"id": rid, "name": name, "created_at": now}

    def list_guest_rooms(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT id, name, created_at FROM guest_rooms ORDER BY created_at DESC").fetchall()
        return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"]} for r in rows]

    def guest_room_exists(self, rid: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM guest_rooms WHERE id=?", (rid,)).fetchone() is not None

    def unlock_guest_room(self, rid: str, password: str, ttl_seconds: int) -> str | None:
        """Return a session token when the password is right, else None."""
        with self._lock:
            row = self._conn.execute("SELECT pw_hash FROM guest_rooms WHERE id=?", (rid,)).fetchone()
        if row is None or not verify_password(password, row["pw_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._conn.execute("DELETE FROM guest_room_tokens WHERE expires_at<=?", (now,))
            self._conn.execute(
                "INSERT INTO guest_room_tokens (token_hash, room_id, expires_at) VALUES (?,?,?)",
                (_token_key(token), rid, now + ttl_seconds),
            )
            self._conn.commit()
        return token

    def guest_room_for_token(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT room_id, expires_at FROM guest_room_tokens WHERE token_hash=?", (_token_key(token),)
            ).fetchone()
        if row is None or row["expires_at"] <= time.time():
            return None
        return row["room_id"]
