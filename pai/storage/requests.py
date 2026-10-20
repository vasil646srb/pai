"""Owner's own request backlog: things to do or remember later, written from the chat page (table requests)."""

from __future__ import annotations

import time
import uuid


class RequestsMixin:
    """Expects `_conn` and `_lock` from StorageCore."""

    def add_request(self, text: str) -> dict:
        rid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO requests (id, created_at, text, done) VALUES (?,?,?,0)", (rid, now, text))
            self._conn.commit()
        return {"id": rid, "created_at": now, "text": text, "done": False}

    def list_requests(self, include_done: bool = True, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM requests"
        if not include_done:
            sql += " WHERE done=0"
        sql += " ORDER BY done ASC, created_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (max(1, min(limit, 500)),)).fetchall()
        return [{"id": r["id"], "created_at": r["created_at"], "text": r["text"], "done": bool(r["done"])}
                for r in rows]

    def set_request_done(self, rid: str, done: bool = True) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE requests SET done=? WHERE id=?", (1 if done else 0, rid))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_request(self, rid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM requests WHERE id=?", (rid,))
            self._conn.commit()
            return cur.rowcount > 0
