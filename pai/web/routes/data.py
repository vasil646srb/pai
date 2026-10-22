"""Раздел «Данные»: обзор всей базы и просмотр записей. Только чтение.

Функции обзора, списка и карточки работают с Storage напрямую и тестируются без HTTP.
Список возвращает обрезанный текст; полный текст — в карточке элемента.
"""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException

from ...semantic.index import _model_key

KINDS = ("node", "file", "inbox", "request")
PREVIEW = 300
TITLE = 120
DETAIL_FILE_CHARS = 20000
INDEXED_MODES = ("all", "yes", "no")

# Откуда читать каждый вид записей. space/where — SQL-выражения.
_SPEC = {
    "node": {"table": "nodes", "id": "id", "space": "space_id", "text": "raw_content", "at": "occurred_at",
             "where": """level = 0 AND status IN ('active','pinned') AND tags NOT LIKE '%"tool_result"%'"""},
    "file": {"table": "uploaded_files", "id": "id", "space": "space_id", "text": "name", "at": "created_at",
             "where": "1 = 1"},
    "inbox": {"table": "inbox_messages", "id": "id", "space": "'inbox'", "text": "text", "at": "created_at",
              "where": "1 = 1"},
    "request": {"table": "requests", "id": "id", "space": "'requests'", "text": "text", "at": "created_at",
                "where": "1 = 1"},
}


def _spec(kind: str) -> dict:
    if kind not in _SPEC:
        raise ValueError(f"неизвестный тип: {kind}")
    return _SPEC[kind]


def _like(q: str) -> str:
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _title(text: str) -> str:
    t = " ".join((text or "").split())
    return t[:TITLE] + ("…" if len(t) > TITLE else "")


def overview(storage, index_key: str | None = None) -> dict:
    with storage._lock:
        conn = storage._conn
        node_rows = conn.execute(
            """SELECT space_id, COUNT(*) AS n FROM nodes
               WHERE level = 0 AND status IN ('active','pinned') AND tags NOT LIKE '%"tool_result"%'
               GROUP BY space_id""").fetchall()
        file_rows = conn.execute("SELECT space_id, COUNT(*) AS n FROM uploaded_files GROUP BY space_id").fetchall()
        inbox_n = conn.execute("SELECT COUNT(*) FROM inbox_messages").fetchone()[0]
        req_n = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        indexed: dict = {}
        if index_key:
            indexed = {r["kind"]: r["n"] for r in conn.execute(
                "SELECT kind, COUNT(DISTINCT source_id) AS n FROM semantic_chunks WHERE model = ? GROUP BY kind",
                (index_key,)).fetchall()}
    spaces: dict[str, dict] = {}
    for r in node_rows:
        spaces.setdefault(r["space_id"], {"space_id": r["space_id"], "nodes": 0, "files": 0})["nodes"] = r["n"]
    for r in file_rows:
        spaces.setdefault(r["space_id"], {"space_id": r["space_id"], "nodes": 0, "files": 0})["files"] = r["n"]
    return {
        "kinds": {
            "node": sum(r["n"] for r in node_rows),
            "file": sum(r["n"] for r in file_rows),
            "inbox": inbox_n,
            "request": req_n,
        },
        "indexed": indexed,
        "spaces": sorted(spaces.values(), key=lambda s: s["space_id"]),
        "index_model": index_key,
    }


def list_items(storage, kind: str, *, space_id: str | None = None, q: str = "", offset: int = 0,
               limit: int = 50, indexed: str = "all", index_key: str | None = None) -> dict:
    spec = _spec(kind)
    if indexed not in INDEXED_MODES:
        raise ValueError("indexed: all, yes или no")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where, args = [spec["where"]], []
    if space_id:
        where.append(f"{spec['space']} = ?")
        args.append(space_id)
    if q:
        where.append(f"{spec['text']} LIKE ? ESCAPE '\\'")
        args.append(_like(q))
    if indexed != "all" and index_key:
        op = "IN" if indexed == "yes" else "NOT IN"
        where.append(f"{spec['id']} {op} (SELECT source_id FROM semantic_chunks WHERE kind = ? AND model = ?)")
        args += [kind, index_key]
    w = " AND ".join(where)
    with storage._lock:
        conn = storage._conn
        total = conn.execute(f"SELECT COUNT(*) FROM {spec['table']} WHERE {w}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT {spec['id']} AS id, {spec['space']} AS space_id, {spec['text']} AS text, "
            f"{spec['at']} AS at FROM {spec['table']} WHERE {w} ORDER BY {spec['at']} DESC LIMIT ? OFFSET ?",
            [*args, limit, offset]).fetchall()
        flagged: set = set()
        if index_key and rows:
            ids = [r["id"] for r in rows]
            marks = ",".join("?" for _ in ids)
            flagged = {r["source_id"] for r in conn.execute(
                f"SELECT DISTINCT source_id FROM semantic_chunks WHERE kind = ? AND model = ? "
                f"AND source_id IN ({marks})", [kind, index_key, *ids]).fetchall()}
    items = [{
        "id": r["id"], "kind": kind, "space_id": r["space_id"], "title": _title(r["text"]),
        "preview": (r["text"] or "")[:PREVIEW], "at": r["at"],
        "indexed": (r["id"] in flagged) if index_key else None,
    } for r in rows]
    return {"total": total, "offset": offset, "limit": limit, "items": items}


def item_detail(storage, kind: str, item_id: str, index_key: str | None = None) -> dict | None:
    _spec(kind)
    with storage._lock:
        conn = storage._conn
        if kind == "node":
            r = conn.execute("SELECT id, space_id, tags, occurred_at, raw_content, summary_text, status, level "
                             "FROM nodes WHERE id = ?", (item_id,)).fetchone()
        elif kind == "file":
            r = conn.execute("SELECT id, space_id, name, mime, size_orig, created_at "
                             "FROM uploaded_files WHERE id = ?", (item_id,)).fetchone()
        elif kind == "inbox":
            r = conn.execute("SELECT id, created_at, name, text FROM inbox_messages WHERE id = ?",
                             (item_id,)).fetchone()
        else:
            r = conn.execute("SELECT id, created_at, text, done FROM requests WHERE id = ?",
                             (item_id,)).fetchone()
        if r is None:
            return None
        indexed = None
        if index_key:
            indexed = conn.execute(
                "SELECT COUNT(*) FROM semantic_chunks WHERE kind = ? AND source_id = ? AND model = ?",
                (kind, item_id, index_key)).fetchone()[0] > 0
    base = {"id": item_id, "kind": kind, "indexed": indexed}
    if kind == "node":
        return {**base, "space_id": r["space_id"], "at": r["occurred_at"],
                "tags": json.loads(r["tags"] or "[]"), "status": r["status"], "level": r["level"],
                "summary": r["summary_text"], "text": r["raw_content"]}
    if kind == "file":
        body = storage.read_file_text(item_id) or ""
        return {**base, "space_id": r["space_id"], "at": r["created_at"], "name": r["name"],
                "mime": r["mime"], "size": r["size_orig"], "text": body[:DETAIL_FILE_CHARS],
                "truncated": len(body) > DETAIL_FILE_CHARS}
    if kind == "inbox":
        return {**base, "space_id": "inbox", "at": r["created_at"], "name": r["name"], "text": r["text"]}
    return {**base, "space_id": "requests", "at": r["created_at"], "text": r["text"], "done": bool(r["done"])}


def register(app: FastAPI) -> None:
    def _key():
        return _model_key(app.state.semantic_store.get())

    @app.get("/data/overview")
    def data_overview():
        return overview(app.state.storage, _key())

    @app.get("/data/items")
    def data_items(kind: str = "node", space_id: str | None = None, q: str = "", offset: int = 0,
                   limit: int = 50, indexed: str = "all"):
        try:
            return list_items(app.state.storage, kind, space_id=space_id or None, q=q.strip(),
                              offset=offset, limit=limit, indexed=indexed, index_key=_key())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/data/item/{kind}/{item_id}")
    def data_item(kind: str, item_id: str):
        try:
            found = item_detail(app.state.storage, kind, item_id, index_key=_key())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if found is None:
            raise HTTPException(404, "запись не найдена")
        return found
