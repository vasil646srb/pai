"""Chat message history queries."""

from __future__ import annotations


class ChatMixin:
    """Chat message history queries."""

    def get_chat_messages(self, space_id: str, limit: int = 10, before_position=None):
        """Visible dialogue only: tool/skill execution results are internal
        level-0 nodes and are excluded."""
        sql = """
        SELECT *
        FROM nodes
        WHERE level = 0
          AND space_id = ?
          AND status IN ('active','pinned')
          AND tags NOT LIKE '%"tool_result"%'
        """
        args: list = [space_id]
        if before_position is not None:
            sql += " AND position_in_history < ?"
            args.append(before_position)
        sql += " ORDER BY position_in_history DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [self._row_to_node(r) for r in cur.fetchall()]

    def get_recent_dialog_context(self, space_id: str, limit: int = 30):
        rows = self.get_chat_messages(space_id, limit=limit)
        rows.reverse()
        out = []
        for n in rows:
            role = "assistant" if (n.tags and "assistant" in n.tags) else "user"
            out.append({"role": role, "content": n.raw_content})
        return out
