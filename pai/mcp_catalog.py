"""Catalog of MCP servers to browse on the MCP page.

Entries are descriptions only. Nothing here starts a server: the page pre-fills
the add-server form and the owner reviews and submits it.

Sources
  - bundled file pai/mcp_catalog.json (curated, shipped with the app)
  - remote feeds listed in PAI_MCP_CATALOG_URLS (comma-separated, https only).
    Remote entries are always labelled "community", whatever they claim.

Entry format (all fields except id, title, description, repo are optional):
  {"id": "git", "title": "Git", "description": "...", "repo": "https://...",
   "author": "...", "source": "official|community",
   "install": {"transport": "stdio", "command": "uvx", "args": ["mcp-server-git"], "note": "..."},
   "env_keys": ["API_TOKEN"]}
  install with transport "http" takes "url" instead of command/args.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

import requests

BUNDLED_PATH = Path(__file__).with_name("mcp_catalog.json")
MAX_FEED_BYTES = 1_000_000
FEED_TTL_SECONDS = 3600
FEED_TIMEOUT = 10

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
SOURCES = ("official", "community")
_TRANSPORTS = ("stdio", "http")


def _str(v: Any, limit: int) -> str:
    if not isinstance(v, str):
        raise ValueError("expected a string")
    return v.strip()[:limit]


def _validate_entry(raw: Any, *, remote: bool) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("entry must be an object")
    eid = _str(raw.get("id", ""), 24)
    if not _ID_RE.match(eid):
        raise ValueError(f"bad id {eid!r}")
    repo = _str(raw.get("repo", ""), 300)
    if not repo.startswith("https://"):
        raise ValueError("repo must be an https URL")
    source = _str(raw.get("source", "community"), 20) or "community"
    if source not in SOURCES:
        raise ValueError(f"bad source {source!r}")
    if remote:
        source = "community"  # remote feeds cannot claim to be official
    entry = {
        "id": eid,
        "title": _str(raw.get("title", ""), 80) or eid,
        "description": _str(raw.get("description", ""), 400),
        "repo": repo,
        "author": _str(raw.get("author", ""), 80),
        "source": source,
        "reviewed": False,  # nobody on this side has checked the code
        "install": None,
        "env_keys": [],
    }
    if not entry["description"]:
        raise ValueError("description is required")
    keys = raw.get("env_keys", [])
    if not isinstance(keys, list) or not all(isinstance(k, str) and len(k) <= 60 for k in keys):
        raise ValueError("env_keys must be a list of strings")
    entry["env_keys"] = [k for k in keys][:10]

    inst = raw.get("install")
    if inst is not None:
        if not isinstance(inst, dict):
            raise ValueError("install must be an object")
        transport = _str(inst.get("transport", "stdio"), 10)
        if transport not in _TRANSPORTS:
            raise ValueError(f"bad transport {transport!r}")
        note = _str(inst.get("note", ""), 300)
        if transport == "stdio":
            command = _str(inst.get("command", ""), 200)
            args = inst.get("args", [])
            if not command:
                raise ValueError("stdio install needs a command")
            if not isinstance(args, list) or not all(isinstance(a, str) and len(a) <= 300 for a in args) or len(args) > 20:
                raise ValueError("args must be a list of up to 20 strings")
            entry["install"] = {"transport": "stdio", "command": command, "args": args, "note": note}
        else:
            url = _str(inst.get("url", ""), 300)
            if not re.match(r"^https?://\S+$", url):
                raise ValueError("http install needs an http(s) url")
            entry["install"] = {"transport": "http", "url": url, "note": note}
    return entry


def parse_feed(data: Any, *, remote: bool) -> tuple[list[dict], list[str]]:
    """Return (valid entries, error messages). Invalid entries are skipped, not fatal."""
    items = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return [], ["feed must be a list or an object with 'entries'"]
    entries, errors, seen = [], [], set()
    for raw in items[:500]:
        try:
            e = _validate_entry(raw, remote=remote)
        except ValueError as exc:
            errors.append(f"{(raw or {}).get('id', '?') if isinstance(raw, dict) else '?'}: {exc}")
            continue
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        entries.append(e)
    return entries, errors


class McpCatalog:
    def __init__(self, bundled_path: Path = BUNDLED_PATH, feed_urls: Optional[list[str]] = None):
        self.bundled_path = Path(bundled_path)
        self.feed_urls = list(feed_urls or [])
        self._cache: dict[str, tuple[float, list[dict], list[str]]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "McpCatalog":
        raw = os.environ.get("PAI_MCP_CATALOG_URLS", "")
        urls = [u.strip() for u in raw.split(",") if u.strip().startswith("https://")]
        return cls(feed_urls=urls)

    def _bundled(self) -> tuple[list[dict], list[str]]:
        try:
            data = json.loads(self.bundled_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return [], [f"bundled catalog unreadable: {e}"]
        return parse_feed(data, remote=False)

    def _feed(self, url: str) -> tuple[list[dict], list[str]]:
        with self._lock:
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < FEED_TTL_SECONDS:
                return hit[1], hit[2]
        try:
            r = requests.get(url, timeout=FEED_TIMEOUT, stream=True)
            r.raise_for_status()
            raw = r.raw.read(MAX_FEED_BYTES + 1, decode_content=True)
            if len(raw) > MAX_FEED_BYTES:
                raise ValueError("feed is larger than 1 MB")
            entries, errors = parse_feed(json.loads(raw.decode("utf-8")), remote=True)
        except Exception as e:  # network, size, JSON: report, keep serving the rest
            entries, errors = [], [f"{url}: {type(e).__name__}: {e}"[:300]]
        with self._lock:
            self._cache[url] = (time.time(), entries, errors)
        return entries, errors

    def list(self) -> dict:
        entries, errors = self._bundled()
        seen = {e["id"] for e in entries}
        for url in self.feed_urls:
            feed_entries, feed_errors = self._feed(url)
            errors.extend(feed_errors)
            for e in feed_entries:
                if e["id"] not in seen:
                    seen.add(e["id"])
                    entries.append(e)
        return {"entries": entries, "errors": errors}
