"""Sign-in for the web UI and API.

The password comes from PAI_PASSWORD (server env, never stored). A correct password issues a random
token; only its SHA-256 is kept on the server (auth_sessions.json), so a leaked file cannot be replayed.
The browser keeps the token in localStorage and sends it as `Authorization: Bearer <token>`.

Fail closed: without a password (and without PAI_AUTH_DISABLED=1) every API call returns 503."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

TOKEN_TTL_SECONDS = 30 * 24 * 3600
MAX_FAILS = 5                 # wrong passwords per IP within FAIL_WINDOW
FAIL_WINDOW = 15 * 60
LOCK_SECONDS = 15 * 60
PUBLIC_PATHS = {"/", "/guest", "/auth/status", "/auth/login"}
PUBLIC_PREFIXES = ("/static/", "/guest/")
GUEST_MAX_BODY = 16 * 1024 * 1024  # base64 images (3 x 4.5 MB) plus text


def _digest(s: str) -> bytes:
    return hashlib.sha256(s.encode("utf-8")).digest()


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


class AuthStore:
    def __init__(self, data_dir: str | os.PathLike, password: str, disabled: bool = False, clock=time.time):
        self.disabled = disabled
        self._pw = _digest(password) if password else None
        self._clock = clock
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}
        self.path = Path(data_dir) / "auth_sessions.json"
        self._sessions: dict[str, float] = self._load()

    @property
    def enabled(self) -> bool:
        return self._pw is not None

    def _load(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        now = self._clock()
        return {k: float(v) for k, v in data.items() if float(v) > now} if isinstance(data, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._sessions), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def _seconds_locked(self, ip: str) -> float:
        now = self._clock()
        hits = [t for t in self._fails.get(ip, []) if now - t < FAIL_WINDOW]
        self._fails[ip] = hits
        if len(hits) >= MAX_FAILS:
            return max(0.0, LOCK_SECONDS - (now - hits[-1]))
        return 0.0

    def login(self, ip: str, password: str) -> str | None:
        """Return a token, or None for a wrong password. Raises PermissionError(seconds) when locked."""
        with self._lock:
            wait = self._seconds_locked(ip)
            if wait > 0:
                raise PermissionError(int(wait) + 1)
            if not self.enabled or not hmac.compare_digest(_digest(password or ""), self._pw):
                self._fails.setdefault(ip, []).append(self._clock())
                return None
            self._fails.pop(ip, None)
            token = secrets.token_urlsafe(32)
            self._sessions[_digest(token).hex()] = self._clock() + TOKEN_TTL_SECONDS
            now = self._clock()
            self._sessions = {k: v for k, v in self._sessions.items() if v > now}
            self._save()
            return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        key = _digest(token).hex()
        with self._lock:
            exp = self._sessions.get(key)
            if exp is None:
                return False
            if exp <= self._clock():
                self._sessions.pop(key, None)
                self._save()
                return False
            return True

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            if self._sessions.pop(_digest(token).hex(), None) is not None:
                self._save()


def install_auth(app: FastAPI, data_dir: str | os.PathLike, password: str, disabled: bool = False) -> AuthStore:
    store = AuthStore(data_dir, password, disabled)
    app.state.auth = store

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        path = request.url.path
        if path.startswith("/guest/"):
            try:
                too_big = int(request.headers.get("content-length") or 0) > GUEST_MAX_BODY
            except ValueError:
                too_big = True
            if too_big:
                return JSONResponse({"detail": "Слишком большой запрос"}, status_code=413)
        if (request.method == "OPTIONS" or store.disabled or path in PUBLIC_PATHS
                or path.startswith(PUBLIC_PREFIXES)):
            return await call_next(request)
        if not store.enabled:
            return JSONResponse({"detail": "Вход не настроен: задайте PAI_PASSWORD на сервере"}, status_code=503)
        if not store.valid(bearer_token(request)):
            return JSONResponse({"detail": "Нужен вход"}, status_code=401)
        return await call_next(request)

    return store
