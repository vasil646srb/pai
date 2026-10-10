"""Storage layer — Layer 0, source of truth.

Design per spec:
- Append-only WAL on disk (JSONL) is the durable log of every write.
- SQLite is a secondary index (rebuildable from the WAL) used for queries and FTS5 search.
- No vector DB, no tensors. Text + metadata only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from .models import MemoryNode, NodeStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    level INTEGER NOT NULL,
    parent_id TEXT,
    child_ids TEXT NOT NULL,       -- JSON array
    type TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    media_path TEXT,
    timestamp REAL NOT NULL,
    occurred_at REAL,
    ingested_at REAL NOT NULL,
    position_in_history INTEGER NOT NULL,
    timeframe_start REAL,
    timeframe_end REAL,
    tags TEXT NOT NULL,            -- JSON array
    status TEXT NOT NULL,
    annotations TEXT NOT NULL,     -- JSON array
    retrieval_count INTEGER NOT NULL DEFAULT 0,
    reasoning TEXT                 -- assistant chain-of-thought (display only, never fed back to the model)
);

CREATE INDEX IF NOT EXISTS idx_nodes_space ON nodes(space_id);
CREATE INDEX IF NOT EXISTS idx_nodes_level ON nodes(level);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_nodes_occurred ON nodes(occurred_at);
CREATE INDEX IF NOT EXISTS idx_nodes_position ON nodes(position_in_history);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id UNINDEXED,
    summary_text,
    raw_content
);

CREATE TABLE IF NOT EXISTS retrieval_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    result_node_ids TEXT NOT NULL,
    used_in_final_answer INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS action_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    space_id TEXT,
    kind TEXT NOT NULL,            -- 'llm_request' | 'tool_call'
    name TEXT NOT NULL,            -- model name, or tool name
    request TEXT NOT NULL,         -- JSON: messages sent / tool arguments
    response TEXT,                 -- JSON: tool result / assistant content+tool_calls
    latency_seconds REAL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_log_ts ON action_log(ts);
CREATE INDEX IF NOT EXISTS idx_action_log_kind ON action_log(kind);
"""


ACTION_LOG_KEEP = 3000  # most recent action_log rows retained


class Storage:
    """Thread-safe storage combining an append-only JSONL WAL with a SQLite index.

    The SQLite DB is treated as a derived, rebuildable cache: `rebuild_index_from_wal`
    replays the WAL to reconstruct it if it is lost or corrupted.
    """

    def __init__(self, data_dir: str | os.PathLike):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.wal_path = self.data_dir / "wal.jsonl"
        self.db_path = self.data_dir / "index.sqlite3"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate_schema()
        self._conn.commit()
        self._action_inserts = 0
        self._next_position = self._compute_next_position()

    def _migrate_schema(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(nodes)")}
        if "reasoning" not in cols:
            self._conn.execute("ALTER TABLE nodes ADD COLUMN reasoning TEXT")

    # ---------- WAL ----------

    def _append_wal(self, op: str, node: MemoryNode) -> None:
        record = {"op": op, "node": node.to_dict()}
        with open(self.wal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _compute_next_position(self) -> int:
        row = self._conn.execute(
            "SELECT MAX(position_in_history) AS m FROM nodes"
        ).fetchone()
        m = row["m"] if row and row["m"] is not None else -1
        return m + 1

    # ---------- Write path ----------

    def put_node(self, node: MemoryNode, assign_position: bool = True) -> MemoryNode:
        """Insert or update a node. Writes WAL first, then updates the SQLite index."""
        with self._lock:
            if assign_position and node.position_in_history == 0:
                exists = self._conn.execute(
                    "SELECT 1 FROM nodes WHERE id=?", (node.id,)
                ).fetchone()
                if not exists:
                    node.position_in_history = self._next_position
                    self._next_position += 1
            self._append_wal("put", node)
            self._upsert_sqlite(node)
            self._conn.commit()
            return node

    def _upsert_sqlite(self, node: MemoryNode) -> None:
        d = node.to_dict()
        self._conn.execute(
            """
            INSERT INTO nodes (
                id, space_id, level, parent_id, child_ids, type, summary_text,
                raw_content, media_path, timestamp, occurred_at, ingested_at,
                position_in_history, timeframe_start, timeframe_end, tags,
                status, annotations, retrieval_count, reasoning
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                space_id=excluded.space_id,
                level=excluded.level,
                parent_id=excluded.parent_id,
                child_ids=excluded.child_ids,
                type=excluded.type,
                summary_text=excluded.summary_text,
                raw_content=excluded.raw_content,
                media_path=excluded.media_path,
                timestamp=excluded.timestamp,
                occurred_at=excluded.occurred_at,
                ingested_at=excluded.ingested_at,
                position_in_history=excluded.position_in_history,
                timeframe_start=excluded.timeframe_start,
                timeframe_end=excluded.timeframe_end,
                tags=excluded.tags,
                status=excluded.status,
                annotations=excluded.annotations,
                retrieval_count=excluded.retrieval_count,
                reasoning=excluded.reasoning
            """,
            (
                d["id"], d["space_id"], d["level"], d["parent_id"],
                json.dumps(d["child_ids"]), d["type"], d["summary_text"],
                d["raw_content"], d["media_path"], d["timestamp"], d["occurred_at"],
                d["ingested_at"], d["position_in_history"], d["timeframe_start"],
                d["timeframe_end"], json.dumps(d["tags"], ensure_ascii=False), d["status"],
                json.dumps(d["annotations"], ensure_ascii=False), d["retrieval_count"],
                d.get("reasoning"),
            ),
        )
        self._conn.execute("DELETE FROM nodes_fts WHERE id=?", (d["id"],))
        self._conn.execute(
            "INSERT INTO nodes_fts (id, summary_text, raw_content) VALUES (?,?,?)",
            (d["id"], d["summary_text"], d["raw_content"]),
        )

    # ---------- Read path ----------

    def _row_to_node(self, row: sqlite3.Row) -> MemoryNode:
        d = {
            "id": row["id"],
            "space_id": row["space_id"],
            "level": row["level"],
            "parent_id": row["parent_id"],
            "child_ids": json.loads(row["child_ids"]),
            "type": row["type"],
            "summary_text": row["summary_text"],
            "raw_content": row["raw_content"],
            "media_path": row["media_path"],
            "timestamp": row["timestamp"],
            "occurred_at": row["occurred_at"],
            "ingested_at": row["ingested_at"],
            "position_in_history": row["position_in_history"],
            "timeframe_start": row["timeframe_start"],
            "timeframe_end": row["timeframe_end"],
            "tags": json.loads(row["tags"]),
            "status": row["status"],
            "annotations": json.loads(row["annotations"]),
            "retrieval_count": row["retrieval_count"],
            "reasoning": row["reasoning"] if "reasoning" in row.keys() else None,
        }
        return MemoryNode.from_dict(d)

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
        self, space_id: str, limit: int, statuses: tuple = ("active", "pinned"),
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
        terms = [t for t in query.replace('"', ' ').split() if t.strip()]
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
            q = (
                "SELECT * FROM nodes WHERE occurred_at IS NOT NULL "
                "AND occurred_at >= ? AND occurred_at <= ?"
            )
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

    # ---------- Retrieval audit log ----------

    def log_retrieval(self, tool: str, args: dict, result_node_ids: list[str]) -> int:
        with self._lock:
            import time as _time
            cur = self._conn.execute(
                "INSERT INTO retrieval_log (ts, tool, args, result_node_ids, used_in_final_answer) "
                "VALUES (?,?,?,?,0)",
                (_time.time(), tool, json.dumps(args, ensure_ascii=False),
                 json.dumps(result_node_ids)),
            )
            self._conn.commit()
            return cur.lastrowid

    def mark_used_in_final_answer(self, log_seq: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE retrieval_log SET used_in_final_answer=1 WHERE seq=?", (log_seq,)
            )
            self._conn.commit()

    def get_retrieval_log(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM retrieval_log ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- Assistant action log (LLM requests + tool calls) ----------

    def log_action(
        self, kind: str, name: str, request: object, *,
        space_id: str | None = None, response: object = None,
        latency_seconds: float | None = None, error: str | None = None,
    ) -> int:
        with self._lock:
            import time as _time
            cur = self._conn.execute(
                "INSERT INTO action_log (ts, space_id, kind, name, request, response, "
                "latency_seconds, error) VALUES (?,?,?,?,?,?,?,?)",
                (
                    _time.time(), space_id, kind, name,
                    json.dumps(request, ensure_ascii=False, default=str),
                    json.dumps(response, ensure_ascii=False, default=str) if response is not None else None,
                    latency_seconds, error,
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
        self, limit: int = 200, kind: str | None = None, space_id: str | None = None,
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

    # ---------- Backup / restore ----------

    def export_wal(self) -> bytes:
        """Returns the raw WAL (JSONL) as bytes — a complete, replayable
        backup of every write ever made to this store."""
        with self._lock:
            if not self.wal_path.exists():
                return b""
            return self.wal_path.read_bytes()

    def import_wal(self, data: bytes, mode: str = "merge") -> int:
        """Restores nodes from a previously exported WAL.

        mode="merge" (default) appends the incoming records after the
        existing WAL — later `put` records for the same id win on replay,
        so this is safe to run against a store that already has data.
        mode="replace" discards the current WAL entirely and starts fresh
        from the imported one.

        The whole file is validated (parseable JSON, each line has an
        `op` + a `node` that survives `MemoryNode.from_dict`) before
        anything is written, so a malformed backup never leaves the store
        half-updated. Returns the resulting node count in the index.
        """
        if mode not in ("merge", "replace"):
            raise ValueError(f"Invalid import mode: {mode!r} (expected 'merge' or 'replace')")

        text = data.decode("utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        parsed: list[str] = []
        for i, line in enumerate(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Line {i + 1}: invalid JSON ({e})") from e
            if not isinstance(record, dict) or "op" not in record or "node" not in record:
                raise ValueError(f"Line {i + 1}: missing required 'op'/'node' fields")
            try:
                MemoryNode.from_dict(record["node"])
            except (KeyError, ValueError, TypeError) as e:
                raise ValueError(f"Line {i + 1}: malformed node ({e})") from e
            parsed.append(line)

        with self._lock:
            if mode == "replace":
                with open(self.wal_path, "w", encoding="utf-8") as f:
                    for line in parsed:
                        f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            else:
                with open(self.wal_path, "a", encoding="utf-8") as f:
                    for line in parsed:
                        f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            return self.rebuild_index_from_wal()

    # ---------- Maintenance ----------

    def rebuild_index_from_wal(self) -> int:
        """Replay the WAL to reconstruct the SQLite index from scratch.
        Returns the resulting number of nodes in the index."""
        with self._lock:
            self._conn.execute("DELETE FROM nodes")
            self._conn.execute("DROP TABLE IF EXISTS nodes_fts")
            self._conn.execute(
                "CREATE VIRTUAL TABLE nodes_fts USING fts5(id UNINDEXED, summary_text, raw_content)"
            )
            self._conn.commit()
            if not self.wal_path.exists():
                return 0
            with open(self.wal_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        node = MemoryNode.from_dict(record["node"])
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue  # torn/corrupt line (e.g. crash mid-write): skip, keep the rest
                    self._upsert_sqlite(node)
            self._conn.commit()
            self._next_position = self._compute_next_position()
            return self.count_nodes()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def stats(self) -> dict:
        with self._lock:
            total = self.count_nodes()
            by_level = {}
            for row in self._conn.execute(
                "SELECT level, COUNT(*) AS c FROM nodes GROUP BY level"
            ):
                by_level[row["level"]] = row["c"]
            by_space = {}
            for row in self._conn.execute(
                "SELECT space_id, COUNT(*) AS c FROM nodes GROUP BY space_id"
            ):
                by_space[row["space_id"]] = row["c"]
            wal_size = self.wal_path.stat().st_size if self.wal_path.exists() else 0
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
            return {
                "total_nodes": total,
                "by_level": by_level,
                "by_space": by_space,
                "wal_size_bytes": wal_size,
                "db_size_bytes": db_size,
            }


    def get_chat_messages(self, space_id: str, limit: int = 10, before_position=None):
        """Visible dialogue only: tool/skill execution results are internal
        level-0 nodes and are excluded."""
        sql = '''
        SELECT *
        FROM nodes
        WHERE level = 0
          AND space_id = ?
          AND status IN ('active','pinned')
          AND tags NOT LIKE '%"tool_result"%'
        '''
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
