"""Retrieval, action and event logs."""

from __future__ import annotations

import json

from .core import ACTION_LOG_KEEP


class LogsMixin:
    """Retrieval, action and event logs."""

    def log_retrieval(self, tool: str, args: dict, result_node_ids: list[str]) -> int:
        with self._lock:
            import time as _time

            cur = self._conn.execute(
                "INSERT INTO retrieval_log (ts, tool, args, result_node_ids, used_in_final_answer) VALUES (?,?,?,?,0)",
                (_time.time(), tool, json.dumps(args, ensure_ascii=False), json.dumps(result_node_ids)),
            )
            self._conn.commit()
            return cur.lastrowid

    def mark_used_in_final_answer(self, log_seq: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE retrieval_log SET used_in_final_answer=1 WHERE seq=?", (log_seq,))
            self._conn.commit()

    def get_retrieval_log(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM retrieval_log ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def log_action(
        self,
        kind: str,
        name: str,
        request: object,
        *,
        space_id: str | None = None,
        response: object = None,
        latency_seconds: float | None = None,
        error: str | None = None,
    ) -> int:
        with self._lock:
            import time as _time

            cur = self._conn.execute(
                "INSERT INTO action_log (ts, space_id, kind, name, request, response, "
                "latency_seconds, error) VALUES (?,?,?,?,?,?,?,?)",
                (
                    _time.time(),
                    space_id,
                    kind,
                    name,
                    json.dumps(request, ensure_ascii=False, default=str),
                    json.dumps(response, ensure_ascii=False, default=str) if response is not None else None,
                    latency_seconds,
                    error,
                ),
            )
            self._action_inserts += 1
            if self._action_inserts % 200 == 0:
                # keep the audit log bounded: it stores full prompts
                self._conn.execute(
                    "DELETE FROM action_log WHERE seq <= (SELECT MAX(seq) FROM action_log) - ?",
                    (ACTION_LOG_KEEP,),
                )
            self._conn.commit()
            return cur.lastrowid

    def get_action_log(
        self,
        limit: int = 200,
        kind: str | None = None,
        space_id: str | None = None,
    ) -> list[dict]:
        with self._lock:
            query = "SELECT * FROM action_log"
            clauses, params = [], []
            if kind:
                clauses.append("kind = ?")
                params.append(kind)
            if space_id:
                clauses.append("space_id = ?")
                params.append(space_id)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY seq DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def log_context_event(self, name: str, payload: object, *, space_id=None):
        return self.log_action("context_update", name, payload, space_id=space_id)

    def log_compression_event(self, name: str, payload: object, *, response=None, space_id=None):
        return self.log_action("compression", name, payload, response=response, space_id=space_id)

    def log_retrieval_event(self, name: str, payload: object, *, response=None, space_id=None):
        return self.log_action("retrieval", name, payload, response=response, space_id=space_id)
