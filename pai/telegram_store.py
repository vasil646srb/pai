"""Telegram messages, posts, bot actions and the poll offset, kept in SQLite.

Tables live in the same database as the memory index (index.sqlite3), so one backup covers everything.
Columns that hold structured data are JSON text; `files` is the last column of a message row: links
to the files of the message (Telegram file_id plus our upload id once the file is stored).
Records are returned as plain dicts with the same keys the service always used ("from" for the sender).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

TG_SCHEMA = """
CREATE TABLE IF NOT EXISTS tg_messages (
    id TEXT PRIMARY KEY,                -- '<chat_id>:<message_id>'
    channel TEXT NOT NULL,              -- owner | group | public | private
    chat_id TEXT NOT NULL DEFAULT '',
    chat_title TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    bot INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT '',
    date INTEGER,
    created_at REAL NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    forward TEXT,                       -- JSON: who wrote the forwarded message and where from
    media_group TEXT NOT NULL DEFAULT '',
    kinds TEXT,                         -- JSON: content fields of the message
    analyzed INTEGER NOT NULL DEFAULT 0,
    verdict TEXT,
    needs_reply INTEGER,
    tries INTEGER NOT NULL DEFAULT 0,
    reply TEXT,                         -- JSON
    held TEXT,
    draft TEXT,
    reply_status TEXT,
    error TEXT NOT NULL DEFAULT '',
    files TEXT                          -- JSON: links to the files of the message (last column)
);
CREATE INDEX IF NOT EXISTS idx_tg_messages_todo ON tg_messages(channel, analyzed);
CREATE INDEX IF NOT EXISTS idx_tg_messages_read ON tg_messages(read);

CREATE TABLE IF NOT EXISTS tg_posts (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    created REAL,
    sent REAL,
    message_id INTEGER,
    error TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0,
    reply_to INTEGER,
    draft_msg_id INTEGER,
    revise INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tg_posts_status ON tg_posts(status);

CREATE TABLE IF NOT EXISTS tg_actions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    question TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL,
    arguments TEXT NOT NULL DEFAULT '{}',   -- JSON
    created REAL,
    message_id INTEGER
);

CREATE TABLE IF NOT EXISTS tg_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

MSG_COLS = ("id", "channel", "chat_id", "chat_title", "sender", "bot", "text", "date", "created_at", "read",
            "forward", "media_group", "kinds", "analyzed", "verdict", "needs_reply", "tries", "reply", "held",
            "draft", "reply_status", "error", "files")
MSG_JSON = frozenset({"forward", "kinds", "reply", "files"})
MSG_BOOL = frozenset({"bot", "read", "analyzed", "needs_reply"})
POST_COLS = ("id", "channel", "text", "status", "created", "sent", "message_id", "error", "verified",
             "reply_to", "draft_msg_id", "revise")
POST_BOOL = frozenset({"verified", "revise"})
ACT_COLS = ("id", "status", "question", "tool", "arguments", "created", "message_id")
ACT_JSON = frozenset({"arguments"})
FILE_KEYS = ("kind", "name", "mime", "size", "file_id", "upload_id", "note")


def msg_row(it: dict) -> dict:
    """Service item (the dict shape used everywhere) -> table row."""
    files = it["files"] if "files" in it else (it.get("media") or [])
    return {
        "id": str(it["id"]), "channel": it.get("channel") or "", "chat_id": str(it.get("chat_id") or ""),
        "chat_title": it.get("chat_title") or "", "sender": it.get("from") or "", "bot": bool(it.get("bot")),
        "text": it.get("text") or "", "date": it.get("date"),
        "created_at": float(it.get("created_at") or time.time()), "read": bool(it.get("read")),
        "forward": it.get("forward"), "media_group": str(it.get("media_group") or ""),
        "kinds": it.get("kinds"), "analyzed": bool(it.get("analyzed")), "verdict": it.get("verdict"),
        "needs_reply": it.get("needs_reply"), "tries": int(it.get("tries") or 0), "reply": it.get("reply"),
        "held": it.get("held"), "draft": it.get("draft"), "reply_status": it.get("reply_status"),
        "error": it.get("error") or "", "files": files,
    }


def file_links(atts: list[dict]) -> list[dict]:
    """The part of an attachment worth keeping in the message row (no bytes)."""
    return [{k: a[k] for k in FILE_KEYS if k in a} for a in atts]


class TelegramStore:
    def __init__(self, conn: sqlite3.Connection, lock):
        self._conn = conn
        self._lock = lock
        with self._lock:
            conn.executescript(TG_SCHEMA)
            conn.commit()

    # ---------- encoding ----------
    @staticmethod
    def _enc(row: dict, json_cols, bool_cols) -> dict:
        out = {}
        for k, v in row.items():
            if k in json_cols:
                out[k] = None if v is None else json.dumps(v, ensure_ascii=False)
            elif k in bool_cols:
                out[k] = None if v is None else int(bool(v))
            else:
                out[k] = v
        return out

    @staticmethod
    def _dec(row, json_cols, bool_cols) -> dict:
        d = dict(row)
        for k in json_cols:
            d[k] = json.loads(d[k]) if d.get(k) else None
        for k in bool_cols:
            if d.get(k) is not None:
                d[k] = bool(d[k])
        return d

    def _insert(self, table: str, cols, row: dict, json_cols, bool_cols, replace: bool = False) -> bool:
        data = self._enc({k: row[k] for k in cols if k in row}, json_cols, bool_cols)
        keys = list(data)
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        sql = f"{verb} INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
        with self._lock:
            cur = self._conn.execute(sql, [data[k] for k in keys])
            self._conn.commit()
            return cur.rowcount == 1

    def _update(self, table: str, pk: str, key: str, fields: dict, cols, json_cols, bool_cols) -> None:
        data = self._enc({k: v for k, v in fields.items() if k in cols and k != pk}, json_cols, bool_cols)
        if not data:
            return
        sets = ", ".join(f"{k}=?" for k in data)
        with self._lock:
            self._conn.execute(f"UPDATE {table} SET {sets} WHERE {pk}=?", [*data.values(), key])
            self._conn.commit()

    # ---------- state ----------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM tg_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO tg_state (key, value) VALUES (?, ?)", (key, str(value)))
            self._conn.commit()

    # ---------- messages ----------
    def _msg(self, row) -> dict | None:
        if row is None:
            return None
        d = self._dec(row, MSG_JSON, MSG_BOOL)
        d["from"] = d.pop("sender")
        d["files"] = d.get("files") or []
        d["kinds"] = d.get("kinds") or []
        return d

    def _msgs(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._msg(r) for r in rows]

    def msg_insert(self, it: dict) -> bool:
        """True if the message was new. A message seen before is never overwritten (INSERT OR IGNORE)."""
        return self._insert("tg_messages", MSG_COLS, msg_row(it), MSG_JSON, MSG_BOOL)

    def msg_update(self, mid: str, **fields) -> None:
        self._update("tg_messages", "id", mid, fields, MSG_COLS, MSG_JSON, MSG_BOOL)

    def msg_get(self, mid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tg_messages WHERE id=?", (mid,)).fetchone()
        return self._msg(row)

    def msg_owner_todo(self) -> list[dict]:
        return self._msgs("SELECT * FROM tg_messages WHERE channel='owner' AND analyzed=0 ORDER BY rowid")

    def msg_group_todo(self, limit: int) -> list[dict]:
        return self._msgs("SELECT * FROM tg_messages WHERE channel='group' AND analyzed=0 AND bot=0 "
                          "ORDER BY rowid LIMIT ?", (limit,))

    def msg_unread(self, channel: str, limit: int) -> list[dict]:
        """Unread messages of the group and reading channels (owner's own messages are never 'news')."""
        if channel == "all":
            return self._msgs("SELECT * FROM tg_messages WHERE read=0 AND channel!='owner' ORDER BY rowid LIMIT ?",
                              (limit,))
        return self._msgs("SELECT * FROM tg_messages WHERE read=0 AND channel=? ORDER BY rowid LIMIT ?",
                          (channel, limit))

    def msg_mark_read(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany("UPDATE tg_messages SET read=1 WHERE id=?", [(i,) for i in ids])
            self._conn.commit()

    def msg_unread_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM tg_messages WHERE read=0").fetchone()[0]

    def msg_recent(self, n: int) -> list[dict]:
        return self._msgs("SELECT * FROM tg_messages ORDER BY rowid DESC LIMIT ?", (n,))

    # ---------- posts (queue) ----------
    def _post(self, row) -> dict | None:
        return None if row is None else self._dec(row, (), POST_BOOL)

    def post_save(self, item: dict, new: bool = False) -> None:
        if new:
            self._insert("tg_posts", POST_COLS, item, (), POST_BOOL)
        else:
            self._insert("tg_posts", POST_COLS, item, (), POST_BOOL, replace=True)

    def post_update(self, pid: str, **fields) -> None:
        self._update("tg_posts", "id", pid, fields, POST_COLS, (), POST_BOOL)

    def post_get(self, pid: str) -> dict | None:
        with self._lock:
            return self._post(self._conn.execute("SELECT * FROM tg_posts WHERE id=?", (pid,)).fetchone())

    def post_pending(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tg_posts WHERE status='pending' ORDER BY rowid").fetchall()
        return [self._post(r) for r in rows]

    def post_history(self, n: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tg_posts WHERE status!='pending' ORDER BY rowid DESC LIMIT ?",
                                      (n,)).fetchall()
        return [self._post(r) for r in rows]

    def post_pending_revision(self) -> dict | None:
        with self._lock:
            return self._post(self._conn.execute(
                "SELECT * FROM tg_posts WHERE status='pending' AND revise=1 ORDER BY rowid LIMIT 1").fetchone())

    def post_supersede_revisions(self, except_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE tg_posts SET status='superseded', revise=0 "
                               "WHERE status='pending' AND revise=1 AND id!=?", (except_id,))
            self._conn.commit()

    # ---------- bot actions (confirmations) ----------
    def _act(self, row) -> dict | None:
        return None if row is None else self._dec(row, ACT_JSON, ())

    def act_insert(self, item: dict) -> None:
        self._insert("tg_actions", ACT_COLS, item, ACT_JSON, (), replace=True)

    def act_update(self, aid: str, **fields) -> None:
        self._update("tg_actions", "id", aid, fields, ACT_COLS, ACT_JSON, ())

    def act_get(self, aid: str) -> dict | None:
        with self._lock:
            return self._act(self._conn.execute("SELECT * FROM tg_actions WHERE id=?", (aid,)).fetchone())

    # ---------- one-time import of the JSON files of the previous version ----------
    def import_legacy(self, data_dir: Path) -> dict:
        """Copies telegram_inbox/queue/actions.json and telegram_state.json into the tables, then renames each
        file to *.migrated.json. Safe to run again: rows that exist are not overwritten."""
        done = {}

        def load(name):
            p = data_dir / name
            if not p.exists():
                return None, None
            try:
                return json.loads(p.read_text(encoding="utf-8")), p
            except (OSError, ValueError):
                return None, p

        def retire(p: Path) -> None:
            os.replace(p, p.with_name(p.stem + ".migrated.json"))

        data, p = load("telegram_inbox.json")
        if isinstance(data, list):
            done["messages"] = sum(1 for it in data if isinstance(it, dict) and it.get("id")
                                   and self.msg_insert(it))
            retire(p)
        data, p = load("telegram_queue.json")
        if isinstance(data, list):
            done["posts"] = 0
            for it in data:
                if isinstance(it, dict) and it.get("id") and self.post_get(it["id"]) is None:
                    self.post_save({**it, "revise": bool(it.get("revise")), "verified": bool(it.get("verified"))},
                                   new=True)
                    done["posts"] += 1
            retire(p)
        data, p = load("telegram_actions.json")
        if isinstance(data, list):
            done["actions"] = 0
            for it in data:
                if isinstance(it, dict) and it.get("id") and self.act_get(it["id"]) is None:
                    self.act_insert(it)
                    done["actions"] += 1
            retire(p)
        data, p = load("telegram_state.json")
        if isinstance(data, dict) and "offset" in data and self.get_state("offset") is None:
            self.set_state("offset", str(int(data["offset"])))
            retire(p)
        return done
