"""WAL export, import and index rebuild."""

from __future__ import annotations

import json
import os

from ..models import MemoryNode


class WalMixin:
    """WAL export, import and index rebuild."""

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

    def rebuild_index_from_wal(self) -> int:
        """Replay the WAL to reconstruct the SQLite index from scratch.
        Returns the resulting number of nodes in the index."""
        with self._lock:
            self._conn.execute("DELETE FROM nodes")
            self._conn.execute("DROP TABLE IF EXISTS nodes_fts")
            self._conn.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(id UNINDEXED, summary_text, raw_content)")
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
                    if record.get("op") == "delete":
                        self._remove_sqlite(node.id)
                    else:
                        self._upsert_sqlite(node)
            self._conn.commit()
            self._next_position = self._compute_next_position()
            return self.count_nodes()
