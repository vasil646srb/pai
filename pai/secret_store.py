"""Secrets for the brain persona (site logins, API tokens).

The values live in <data_dir>/secrets.json (mode 600) and are never returned by the API: only names are listed.
The model writes {{secret:NAME}} in an http_request; the server substitutes the value and masks it in the reply.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class SecretStore:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "secrets.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def names(self) -> list[str]:
        return sorted(self._load())

    def set(self, name: str, value: str) -> None:
        if not NAME_RE.match(name):
            raise ValueError("имя: латиница, цифры, _ . - (до 64 символов)")
        if not value:
            raise ValueError("пустое значение")
        with self._lock:
            data = self._load()
            data[name] = value
            self._save(data)

    def delete(self, name: str) -> bool:
        with self._lock:
            data = self._load()
            if name not in data:
                return False
            del data[name]
            self._save(data)
            return True

    def values(self) -> dict[str, str]:
        """Internal use only (the dispatcher). Never pass this to an API response or to the model."""
        return self._load()
