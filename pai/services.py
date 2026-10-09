"""Autonomous services: a saved prompt that a persona runs on its own schedule.

A service is stored in ``services.json`` under the data dir. A background
thread (``ServiceRunner``) wakes up every few seconds, starts every enabled
service whose ``next_run_at`` has passed, and records the result of each run
(reply or error) in the service's own run history. Runs use the normal
orchestrator turn, so the persona's tools, permissions and memory rules apply.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

_log = logging.getLogger(__name__)

MAX_RUNS_KEPT = 30
MAX_REPLY_CHARS = 4000
_SPACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def services_enabled() -> bool:
    return os.environ.get("PAI_SERVICES", "1") != "0"


class ServiceStore:
    """JSON-backed list of services. Thread-safe; writes atomically."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("services", []) if isinstance(data, dict) else []
            return [s for s in items if isinstance(s, dict) and s.get("id")]
        except (json.JSONDecodeError, OSError) as e:
            _log.warning("services file unreadable (%s): starting with no services", e)
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"services": self._items}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _validate(fields: dict, *, partial: bool) -> dict:
        out: dict = {}
        if "name" in fields or not partial:
            name = str(fields.get("name", "")).strip()
            if not 1 <= len(name) <= 80:
                raise ValueError("name: от 1 до 80 символов")
            out["name"] = name
        if "persona_id" in fields or not partial:
            pid = str(fields.get("persona_id", "")).strip()
            if not pid:
                raise ValueError("persona_id: укажите персону")
            out["persona_id"] = pid
        if "prompt" in fields or not partial:
            prompt = str(fields.get("prompt", "")).strip()
            if not 1 <= len(prompt) <= 4000:
                raise ValueError("prompt: от 1 до 4000 символов")
            out["prompt"] = prompt
        if "interval_minutes" in fields or not partial:
            try:
                interval = int(fields.get("interval_minutes", 60))
            except (TypeError, ValueError):
                raise ValueError("interval_minutes: целое число минут")
            if not 1 <= interval <= 10080:
                raise ValueError("interval_minutes: от 1 до 10080 (неделя)")
            out["interval_minutes"] = interval
        if "space_id" in fields or not partial:
            space = str(fields.get("space_id") or "personal").strip()
            if not _SPACE_RE.match(space):
                raise ValueError("space_id: латиница, цифры, _ и -, до 40 символов")
            out["space_id"] = space
        if "enabled" in fields or not partial:
            out["enabled"] = bool(fields.get("enabled", True))
        return out

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(s, runs=list(s.get("runs", []))) for s in self._items]

    def get(self, sid: str) -> Optional[dict]:
        with self._lock:
            for s in self._items:
                if s["id"] == sid:
                    return dict(s, runs=list(s.get("runs", [])))
            return None

    def create(self, fields: dict, now: float) -> dict:
        data = self._validate(fields, partial=False)
        item = {
            "id": uuid.uuid4().hex[:12], "created_at": now, "last_run_at": None,
            "next_run_at": now, "runs": [], **data,
        }
        with self._lock:
            self._items.append(item)
            self._save()
        return dict(item, runs=[])

    def update(self, sid: str, fields: dict, now: float) -> Optional[dict]:
        data = self._validate(fields, partial=True)
        with self._lock:
            for s in self._items:
                if s["id"] == sid:
                    interval_changed = "interval_minutes" in data and data["interval_minutes"] != s.get("interval_minutes")
                    s.update(data)
                    if interval_changed and s.get("last_run_at"):
                        s["next_run_at"] = s["last_run_at"] + s["interval_minutes"] * 60
                    if data.get("enabled") is True and s.get("next_run_at", 0) < now:
                        s["next_run_at"] = now
                    self._save()
                    return dict(s, runs=list(s.get("runs", [])))
        return None

    def delete(self, sid: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [s for s in self._items if s["id"] != sid]
            if len(self._items) != before:
                self._save()
                return True
            return False

    def add_run(self, sid: str, run: dict) -> None:
        with self._lock:
            for s in self._items:
                if s["id"] == sid:
                    runs = list(s.get("runs", [])) + [run]
                    s["runs"] = runs[-MAX_RUNS_KEPT:]
                    s["last_run_at"] = run["finished_at"]
                    s["next_run_at"] = run["finished_at"] + s["interval_minutes"] * 60
                    self._save()
                    return


class ServiceRunner:
    """Runs due services in background threads, one run per service at a time."""

    def __init__(self, store: ServiceStore, execute: Callable[[dict], dict], tick_seconds: float = 5.0):
        self.store = store
        self.execute = execute
        self.tick_seconds = tick_seconds
        self._running: set[str] = set()
        self._guard = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="pai-services", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_running(self, sid: str) -> bool:
        with self._guard:
            return sid in self._running

    def _loop(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            try:
                self.tick_once(time.time())
            except Exception:  # never let the scheduler die
                _log.exception("service scheduler tick failed")

    def tick_once(self, now: float) -> list[threading.Thread]:
        started = []
        for svc in self.store.list():
            if svc.get("enabled") and svc.get("next_run_at", 0) <= now:
                t = self.start_run(svc["id"])
                if t is not None:
                    started.append(t)
        return started

    def start_run(self, sid: str) -> Optional[threading.Thread]:
        with self._guard:
            if sid in self._running:
                return None
            self._running.add(sid)
        t = threading.Thread(target=self._run, args=(sid,), name=f"pai-service-{sid}", daemon=True)
        t.start()
        return t

    def _run(self, sid: str) -> None:
        try:
            svc = self.store.get(sid)
            if svc is None:
                return
            started_at = time.time()
            try:
                result = self.execute(svc)
                run = {"ok": True, "reply": str(result.get("reply", ""))[:MAX_REPLY_CHARS]}
            except Exception as e:
                _log.warning("service %s failed: %s", sid, e)
                run = {"ok": False, "error": f"{type(e).__name__}: {e}"[:1000]}
            finished_at = time.time()
            run.update(started_at=started_at, finished_at=finished_at,
                       duration_seconds=round(finished_at - started_at, 2))
            self.store.add_run(sid, run)
        finally:
            with self._guard:
                self._running.discard(sid)
