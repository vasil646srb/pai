"""Семантический индекс по базе.

Запись: storage.put_node вызывает хук -> id уходит в очередь -> воркер эмбеддит.
Старт и ручная переиндексация: обход всех источников; чанки с тем же хешем и ключом модели пропускаются.
Поиск: точный cosine по матрице текущей модели, кеш сбрасывается при каждой записи.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading

import numpy as np

from .config import SemanticSettingsStore  # noqa: F401  (тип для аннотаций)
from .embedder import DOC_PREFIX, QUERY_PREFIX, EmbeddingClient, EmbeddingError
from .progress import IndexProgress

_log = logging.getLogger("pai.semantic")

SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_chunks (
    chunk_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    space_id TEXT NOT NULL,
    tags TEXT NOT NULL,
    occurred_at REAL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sem_source ON semantic_chunks(kind, source_id);
CREATE INDEX IF NOT EXISTS idx_sem_model ON semantic_chunks(model);
"""

EMBED_BATCH = 32
_LIVE_STATUS = ("active", "pinned")


def _model_key(cfg: dict) -> str:
    """Векторы разной модели или размерности несопоставимы: ключ включает обе."""
    return f"{cfg['model']}|{cfg.get('dim') or 'full'}"


def _client(cfg: dict) -> EmbeddingClient:
    return EmbeddingClient(cfg["base_url"], cfg["model"], dim=cfg.get("dim") or None,
                           api=cfg.get("api_format", "openai"))


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)  # режем по переносу строки, если он есть
            if cut > start:
                end = cut
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


class SemanticIndex:
    def __init__(self, storage, settings: SemanticSettingsStore):
        self.storage = storage
        self.settings = settings
        with storage._lock:
            storage._conn.executescript(SCHEMA)
            storage._conn.commit()
        self._dirty: set[str] = set()
        self._dirty_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._backfill_pending = True
        self._gen = 0
        self._cache = None  # (gen, model_key, matrix, meta)
        self._cache_lock = threading.Lock()
        self.last_error = ""
        self.progress = IndexProgress()
        storage.semantic = self
        storage.write_hooks.append(self._on_write)

    # ---------- жизненный цикл ----------

    def start(self) -> None:
        threading.Thread(target=self._worker, name="pai-semantic", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def reindex(self) -> None:
        self._backfill_pending = True
        self._wake.set()

    def _on_write(self, node) -> None:
        if getattr(node, "level", 0) != 0:
            return
        with self._dirty_lock:
            self._dirty.add(node.id)
        self._wake.set()

    def _bump(self) -> None:
        with self._cache_lock:
            self._gen += 1

    def _worker(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=5.0)
            self._wake.clear()
            if not self.settings.get()["enabled"]:
                continue
            try:
                if self._backfill_pending:
                    self._run_backfill()
                self._drain_dirty()
                self.last_error = ""
            except EmbeddingError as e:
                self.last_error = str(e)
                _log.warning("semantic index paused: %s", e)
            except Exception as e:  # воркер не должен умереть
                self.last_error = f"{type(e).__name__}: {e}"
                _log.exception("semantic worker failed")

    # ---------- индексирование ----------

    def _node_row(self, nid: str):
        with self.storage._lock:
            r = self.storage._conn.execute(
                "SELECT id, space_id, tags, occurred_at, raw_content, status FROM nodes WHERE id=? AND level=0",
                (nid,),
            ).fetchone()
        if r is None or r["status"] not in _LIVE_STATUS:
            return None
        tags = json.loads(r["tags"] or "[]")
        if "tool_result" in tags:
            return None
        return ("node", r["id"], r["space_id"], tags, r["occurred_at"], r["raw_content"])

    def _drain_dirty(self) -> None:
        with self._dirty_lock:
            ids = list(self._dirty)
            self._dirty.clear()
        for n, nid in enumerate(ids):
            try:
                row = self._node_row(nid)
                if row is None:
                    self._delete_source("node", nid)
                else:
                    self._index_source(*row)
            except EmbeddingError:
                with self._dirty_lock:  # сервер недоступен: вернуть хвост в очередь
                    self._dirty.update(ids[n:])
                raise

    def _index_source(self, kind, source_id, space_id, tags, occurred_at, text, backfill: bool = False) -> None:
        cfg = self.settings.get()
        key = _model_key(cfg)
        pieces = chunk_text(text, cfg["chunk_size"], cfg["chunk_overlap"])
        with self.storage._lock:
            existing = {
                r["chunk_id"]: (r["text_hash"], r["model"])
                for r in self.storage._conn.execute(
                    "SELECT chunk_id, text_hash, model FROM semantic_chunks WHERE kind=? AND source_id=?",
                    (kind, source_id),
                )
            }
        wanted, todo = {}, []
        for i, piece in enumerate(pieces):
            cid, h = f"{kind}:{source_id}#{i}", _hash(piece)
            wanted[cid] = h
            if existing.get(cid) != (h, key):
                todo.append((cid, piece, h))

        if todo and not backfill:  # в переиндексации объём уже посчитан заранее
            self.progress.add_total(len(todo))
        client = _client(cfg)
        for k in range(0, len(todo), EMBED_BATCH):
            part = todo[k:k + EMBED_BATCH]
            vecs = client.embed([DOC_PREFIX + p for _, p, _ in part])
            with self.storage._lock:
                conn = self.storage._conn
                for (cid, piece, h), vec in zip(part, vecs):
                    conn.execute(
                        "INSERT OR REPLACE INTO semantic_chunks "
                        "(chunk_id, kind, source_id, space_id, tags, occurred_at, text, text_hash, model, dim, vec) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, kind, source_id, space_id, json.dumps(tags, ensure_ascii=False),
                         occurred_at, piece, h, key, int(vec.shape[0]), vec.astype(np.float32).tobytes()),
                    )
                conn.commit()
            self._bump()
            self.progress.add_done(len(part))

        stale = [cid for cid in existing if cid not in wanted]
        if stale:
            with self.storage._lock:
                self.storage._conn.executemany("DELETE FROM semantic_chunks WHERE chunk_id=?", [(c,) for c in stale])
                self.storage._conn.commit()
            self._bump()

    def _delete_source(self, kind: str, source_id: str) -> None:
        with self.storage._lock:
            self.storage._conn.execute(
                "DELETE FROM semantic_chunks WHERE kind=? AND source_id=?", (kind, source_id))
            self.storage._conn.commit()
        self._bump()

    def _all_sources(self):
        conn = self.storage._conn
        with self.storage._lock:
            nodes = conn.execute(
                "SELECT id, space_id, tags, occurred_at, raw_content FROM nodes "
                "WHERE level=0 AND status IN ('active','pinned') AND tags NOT LIKE '%\"tool_result\"%'"
            ).fetchall()
            files = conn.execute("SELECT id, space_id, name, created_at FROM uploaded_files").fetchall()
            inbox = conn.execute("SELECT id, created_at, name, text FROM inbox_messages").fetchall()
            reqs = conn.execute("SELECT id, created_at, text FROM requests").fetchall()
        for r in nodes:
            yield ("node", r["id"], r["space_id"], json.loads(r["tags"] or "[]"), r["occurred_at"], r["raw_content"])
        for f in files:
            body = self.storage.read_file_text(f["id"])
            if body:
                yield ("file", f["id"], f["space_id"], ["file"], f["created_at"], f"{f['name']}\n{body}")
        for r in inbox:
            yield ("inbox", r["id"], "inbox", ["inbox"], r["created_at"], f"{r['name']}: {r['text']}")
        for r in reqs:
            yield ("request", r["id"], "requests", ["request"], r["created_at"], r["text"])

    def _run_backfill(self) -> None:
        cfg = self.settings.get()
        key = _model_key(cfg)
        sources = list(self._all_sources())
        live = {(kind, sid) for kind, sid, *_ in sources}
        with self.storage._lock:
            have = {r["chunk_id"]: (r["text_hash"], r["model"]) for r in self.storage._conn.execute(
                "SELECT chunk_id, text_hash, model FROM semantic_chunks")}
        # Точный объём работы до старта: фрагменты, которых нет в индексе этой модели.
        total = 0
        for kind, sid, _space, _tags, _occ, text in sources:
            for i, piece in enumerate(chunk_text(text, cfg["chunk_size"], cfg["chunk_overlap"])):
                if have.get(f"{kind}:{sid}#{i}") != (_hash(piece), key):
                    total += 1
        self.progress.start(total)
        try:
            for kind, sid, space, tags, occ, text in sources:
                self._index_source(kind, sid, space, tags, occ, text, backfill=True)
            with self.storage._lock:
                conn = self.storage._conn
                for r in conn.execute("SELECT DISTINCT kind, source_id FROM semantic_chunks").fetchall():
                    if (r["kind"], r["source_id"]) not in live:
                        conn.execute("DELETE FROM semantic_chunks WHERE kind=? AND source_id=?",
                                     (r["kind"], r["source_id"]))
                conn.execute("DELETE FROM semantic_chunks WHERE model != ?", (key,))
                conn.commit()
            self._bump()
            self._backfill_pending = False
            _log.info("semantic backfill done (model=%s, chunks=%d)", key, total)
        finally:
            self.progress.finish()

    # ---------- поиск ----------

    def _load(self, key: str):
        with self._cache_lock:
            gen = self._gen
            if self._cache and self._cache[0] == gen and self._cache[1] == key:
                return self._cache[2], self._cache[3]
        with self.storage._lock:
            rows = self.storage._conn.execute(
                "SELECT chunk_id, kind, source_id, space_id, tags, occurred_at, text, vec "
                "FROM semantic_chunks WHERE model=?", (key,)).fetchall()
        if rows:
            matrix = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
        else:
            matrix = np.zeros((0, 1), dtype=np.float32)
        meta = [{
            "chunk_id": r["chunk_id"], "kind": r["kind"], "source_id": r["source_id"],
            "space_id": r["space_id"], "tags": json.loads(r["tags"]),
            "occurred_at": r["occurred_at"], "text": r["text"],
        } for r in rows]
        with self._cache_lock:
            self._cache = (gen, key, matrix, meta)
        return matrix, meta

    def search(self, query: str, *, persona=None, space_id: str | None = None,
               top_k: int = 8, min_score: float = 0.68, kinds: list[str] | None = None) -> list[dict]:
        cfg = self.settings.get()
        qv = _client(cfg).embed([QUERY_PREFIX + query])[0]
        matrix, meta = self._load(_model_key(cfg))
        if not meta:
            return []
        scores = matrix @ qv
        out = []
        for i in np.argsort(-scores):
            s = float(scores[i])
            if s < min_score:
                break  # отсортировано по убыванию: дальше только хуже
            m = meta[int(i)]
            if space_id and m["space_id"] != space_id:
                continue
            if kinds and m["kind"] not in kinds:
                continue
            if persona is not None and not persona.memory_filter.permits(m["space_id"], m["tags"]):
                continue
            out.append({"score": round(s, 4), **{k: m[k] for k in (
                "kind", "source_id", "chunk_id", "space_id", "tags", "occurred_at", "text")}})
            if len(out) >= top_k:
                break
        return out

    def status(self) -> dict:
        cfg = self.settings.get()
        key = _model_key(cfg)
        with self.storage._lock:
            rows = self.storage._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM semantic_chunks WHERE model=? GROUP BY kind",
                (key,)).fetchall()
            other = self.storage._conn.execute(
                "SELECT COUNT(*) FROM semantic_chunks WHERE model != ?", (key,)).fetchone()[0]
        with self._dirty_lock:
            pending = len(self._dirty)
        return {
            "enabled": cfg["enabled"], "model": key,
            "chunks": {r["kind"]: r["n"] for r in rows},
            "other_model_chunks": other, "pending": pending,
            "backfill_pending": self._backfill_pending, "last_error": self.last_error,
            "progress": self.progress.snapshot(),
        }
