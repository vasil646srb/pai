"""SQLite connection, schema, WAL append and the node write path."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from ..models import MemoryNode

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

-- uploaded files: original on disk (see storage/files.py), metadata and extracted text here
CREATE TABLE IF NOT EXISTS uploaded_files (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    space_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size_orig INTEGER NOT NULL,
    size_stored INTEGER NOT NULL,
    codec TEXT NOT NULL,            -- 'zstd' | 'gzip' | 'raw'
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uploaded_sha ON uploaded_files(sha256);
CREATE TABLE IF NOT EXISTS uploaded_file_text (
    file_id TEXT PRIMARY KEY REFERENCES uploaded_files(id),
    chars INTEGER NOT NULL,
    codec TEXT NOT NULL,
    body BLOB NOT NULL
);

-- messages from site visitors to the owner
CREATE TABLE IF NOT EXISTS inbox_messages (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    guest_id TEXT NOT NULL,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_inbox_created ON inbox_messages(created_at);

-- private guest rooms (see storage/guest_rooms.py)
CREATE TABLE IF NOT EXISTS guest_rooms (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    pw_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS guest_room_tokens (
    token_hash TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


ACTION_LOG_KEEP = 3000  # most recent action_log rows retained


class StorageCore:
    """SQLite connection, schema, WAL append and the node write path."""

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

    def _append_wal(self, op: str, node: MemoryNode) -> None:
        record = {"op": op, "node": node.to_dict()}
        with open(self.wal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _compute_next_position(self) -> int:
        row = self._conn.execute("SELECT MAX(position_in_history) AS m FROM nodes").fetchone()
        m = row["m"] if row and row["m"] is not None else -1
        return m + 1

    def put_node(self, node: MemoryNode, assign_position: bool = True) -> MemoryNode:
        """Insert or update a node. Writes WAL first, then updates the SQLite index."""
        with self._lock:
            if assign_position and node.position_in_history == 0:
                exists = self._conn.execute("SELECT 1 FROM nodes WHERE id=?", (node.id,)).fetchone()
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
                d["id"],
                d["space_id"],
                d["level"],
                d["parent_id"],
                json.dumps(d["child_ids"]),
                d["type"],
                d["summary_text"],
                d["raw_content"],
                d["media_path"],
                d["timestamp"],
                d["occurred_at"],
                d["ingested_at"],
                d["position_in_history"],
                d["timeframe_start"],
                d["timeframe_end"],
                json.dumps(d["tags"], ensure_ascii=False),
                d["status"],
                json.dumps(d["annotations"], ensure_ascii=False),
                d["retrieval_count"],
                d.get("reasoning"),
            ),
        )
        self._conn.execute("DELETE FROM nodes_fts WHERE id=?", (d["id"],))
        self._conn.execute(
            "INSERT INTO nodes_fts (id, summary_text, raw_content) VALUES (?,?,?)",
            (d["id"], d["summary_text"], d["raw_content"]),
        )

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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def stats(self) -> dict:
        with self._lock:
            total = self.count_nodes()
            by_level = {}
            for row in self._conn.execute("SELECT level, COUNT(*) AS c FROM nodes GROUP BY level"):
                by_level[row["level"]] = row["c"]
            by_space = {}
            for row in self._conn.execute("SELECT space_id, COUNT(*) AS c FROM nodes GROUP BY space_id"):
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
