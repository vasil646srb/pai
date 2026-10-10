"""Memory node reads and status changes."""

from __future__ import annotations

import json
import sqlite3

from ..models import MemoryNode, NodeStatus


class NodesMixin:
    """Memory node reads and status changes."""

    def get_fragment(self, node_id: str, record_retrieval: bool = True) -> MemoryNode | None:
        """Exact retrieval by ID — the primary memory-access mechanism."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            if row is None:
                return None
            if record_retrieval:
                self._conn.execute(
                    "UPDATE nodes SET retrieval_count = retrieval_count + 1 WHERE id=?",
                    (node_id,),
                )
                self._conn.commit()
                row = self._conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            return self._row_to_node(row)

    def get_children(self, node_id: str) -> list[MemoryNode]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE parent_id=? ORDER BY position_in_history ASC",
                (node_id,),
            ).fetchall()
            return [self._row_to_node(r) for r in rows]

    def get_by_level(
        self, level: int, space_id: str | None = None, statuses: list[str] | None = None
    ) -> list[MemoryNode]:
        with self._lock:
            q = "SELECT * FROM nodes WHERE level=?"
            params: list = [level]
            if space_id is not None:
                q += " AND space_id=?"
                params.append(space_id)
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                q += f" AND status IN ({placeholders})"
                params.extend(statuses)
            q += " ORDER BY position_in_history ASC"
            rows = self._conn.execute(q, params).fetchall()
            return [self._row_to_node(r) for r in rows]

    def get_recent_level0(
        self,
        space_id: str,
        limit: int,
        statuses: tuple = ("active", "pinned"),
        include_tool_results: bool = False,
    ) -> list[MemoryNode]:
        """The `limit` most recent raw nodes of a space, oldest first — a
        bounded query instead of loading the whole history on every turn."""
        with self._lock:
            ph = ",".join("?" for _ in statuses)
            q = f"SELECT * FROM nodes WHERE level=0 AND space_id=? AND status IN ({ph})"
            params: list = [space_id, *statuses]
            if not include_tool_results:
                q += " AND tags NOT LIKE '%\"tool_result\"%'"
            q += " ORDER BY position_in_history DESC LIMIT ?"
            params.append(max(1, int(limit)))
            rows = self._conn.execute(q, params).fetchall()
            nodes = [self._row_to_node(r) for r in rows]
            nodes.reverse()
            return nodes

    def get_roots(self, space_id: str | None = None, min_level: int = 0) -> list[MemoryNode]:
        """Nodes with no parent — tops of the tree."""
        with self._lock:
            q = "SELECT * FROM nodes WHERE parent_id IS NULL AND level >= ?"
            params: list = [min_level]
            if space_id is not None:
                q += " AND space_id=?"
                params.append(space_id)
            q += " ORDER BY level DESC, position_in_history ASC"
            rows = self._conn.execute(q, params).fetchall()
            return [self._row_to_node(r) for r in rows]

    def get_max_level(self, space_id: str | None = None) -> int:
        with self._lock:
            q = "SELECT MAX(level) AS m FROM nodes"
            params: list = []
            if space_id is not None:
                q += " WHERE space_id=?"
                params.append(space_id)
            row = self._conn.execute(q, params).fetchone()
            return row["m"] if row and row["m"] is not None else -1

    def count_nodes(self, space_id: str | None = None) -> int:
        with self._lock:
            q = "SELECT COUNT(*) AS c FROM nodes"
            params: list = []
            if space_id is not None:
                q += " WHERE space_id=?"
                params.append(space_id)
            row = self._conn.execute(q, params).fetchone()
            return row["c"]

    def search_archive(
        self,
        query: str,
        space_id: str | None = None,
        date_from: float | None = None,
        date_to: float | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Auxiliary full-text search. Returns candidate id + snippet only —
        never a substitute for get_fragment(). Space/date filters are applied
        in SQL so LIMIT never starves the result set."""
        with self._lock:
            fts_query = self._sanitize_fts_query(query)
            if not fts_query:
                return []
            sql = """
                SELECT n.id AS id, n.space_id AS space_id, n.occurred_at AS occurred_at,
                       n.level AS level, n.tags AS tags,
                       snippet(nodes_fts, 2, '[', ']', '...', 8) AS snip
                FROM nodes_fts
                JOIN nodes n ON n.id = nodes_fts.id
                WHERE nodes_fts MATCH ?
                  AND n.status NOT IN ('excluded','hidden')
            """
            params: list = [fts_query]
            if space_id is not None:
                sql += " AND n.space_id = ?"
                params.append(space_id)
            if date_from is not None:
                sql += " AND COALESCE(n.occurred_at, 0) >= ?"
                params.append(date_from)
            if date_to is not None:
                sql += " AND COALESCE(n.occurred_at, 0) <= ?"
                params.append(date_to)
            sql += " ORDER BY rank LIMIT ?"
            params.append(max(1, int(limit)))
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []  # malformed FTS expression: treat as "no matches"
            return [
                {
                    "id": r["id"],
                    "space_id": r["space_id"],
                    "level": r["level"],
                    "occurred_at": r["occurred_at"],
                    "snippet": r["snip"],
                    "tags": json.loads(r["tags"] or "[]"),
                }
                for r in rows
            ]

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        # Strip FTS5 special characters to avoid syntax errors; wrap terms as OR'd prefix matches.
        terms = [t for t in query.replace('"', " ").split() if t.strip()]
        safe_terms = []
        for t in terms:
            cleaned = "".join(ch for ch in t if ch.isalnum() or ch in "-_")
            if cleaned:
                safe_terms.append(f'"{cleaned}"*')
        return " OR ".join(safe_terms)

    def list_fragments_by_timeframe(
        self, date_from: float, date_to: float, space_id: str | None = None
    ) -> list[MemoryNode]:
        with self._lock:
            q = "SELECT * FROM nodes WHERE occurred_at IS NOT NULL AND occurred_at >= ? AND occurred_at <= ?"
            params: list = [date_from, date_to]
            if space_id is not None:
                q += " AND space_id=?"
                params.append(space_id)
            q += " ORDER BY occurred_at ASC"
            rows = self._conn.execute(q, params).fetchall()
            return [self._row_to_node(r) for r in rows]

    def set_status(self, node_id: str, status: NodeStatus) -> None:
        with self._lock:
            node = self.get_fragment(node_id, record_retrieval=False)
            if node is None:
                raise KeyError(f"No such node: {node_id}")
            node.status = status
            self.put_node(node, assign_position=False)
