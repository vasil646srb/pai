"""Messages from site visitors to the owner (table inbox_messages, see storage/core.py)."""

from __future__ import annotations

import time
import uuid


class InboxMixin:
    """Expects `_conn` and `_lock` from StorageCore."""

    def add_inbox(self, guest_id: str, name: str, text: str) -> dict:
        mid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO inbox_messages (id, created_at, guest_id, name, text, read) VALUES (?,?,?,?,?,0)",
                (mid, now, guest_id, name, text),
            )
            self._conn.commit()
        return {"id": mid, "created_at": now, "guest_id": guest_id, "name": name, "text": text, "read": False}

    def list_inbox(self, unread_only: bool = False, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM inbox_messages"
        if unread_only:
            sql += " WHERE read=0"
        sql += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (max(1, min(limit, 500)),)).fetchall()
        return [
            {"id": r["id"], "created_at": r["created_at"], "guest_id": r["guest_id"],
             "name": r["name"], "text": r["text"], "read": bool(r["read"])}
            for r in rows
        ]

    def set_inbox_read(self, mid: str, read: bool = True) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE inbox_messages SET read=? WHERE id=?", (1 if read else 0, mid))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_inbox(self, mid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM inbox_messages WHERE id=?", (mid,))
            self._conn.commit()
            return cur.rowcount > 0
