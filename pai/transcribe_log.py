"""History of speech recognition: which files were processed, from where, and how it went.

Kept in <data_dir>/transcribe_log.json, newest first, last MAX_ENTRIES attempts. Only a preview of the text is
stored: the full text of an uploaded file stays in the file store, Telegram speech is kept as a preview only.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

MAX_ENTRIES = 500
PREVIEW_CHARS = 1000
SOURCES = {"upload": "веб-чат", "telegram-group": "группа Telegram", "telegram-owner": "личка владельца"}
STATUSES = ("ok", "empty", "error")


class TranscribeLog:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._rows: list[dict] = []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self._rows = [r for r in raw if isinstance(r, dict)][:MAX_ENTRIES]
        except (OSError, ValueError):
            pass

    def add(self, *, name: str, kind: str, source: str, status: str, size: int = 0, duration: int = 0,
            text: str = "", error: str = "") -> dict:
        text = (text or "").strip()
        row = {
            "id": uuid.uuid4().hex[:12], "ts": time.time(), "file": (name or "file")[:200], "kind": kind or "",
            "source": source, "status": status, "size": int(size or 0), "duration": int(duration or 0),
            "chars": len(text), "preview": text[:PREVIEW_CHARS], "error": (error or "")[:500],
        }
        with self._lock:
            self._rows.insert(0, row)
            del self._rows[MAX_ENTRIES:]
            self._save()
        return row

    def list(self, limit: int = 50, source: str = "", status: str = "") -> list[dict]:
        with self._lock:
            rows = list(self._rows)
        if source in SOURCES:
            rows = [r for r in rows if r.get("source") == source]
        if status in STATUSES:
            rows = [r for r in rows if r.get("status") == status]
        return rows[:max(1, min(int(limit), MAX_ENTRIES))]

    def summary(self) -> dict:
        with self._lock:
            rows = list(self._rows)
        day_ago = time.time() - 86400
        out = {"total": len(rows), "today": sum(1 for r in rows if r["ts"] >= day_ago)}
        for s in STATUSES:
            out[s] = sum(1 for r in rows if r.get("status") == s)
        return out

    def clear(self) -> None:
        with self._lock:
            self._rows = []
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._rows, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
