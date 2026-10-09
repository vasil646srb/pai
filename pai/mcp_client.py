"""MCP (Model Context Protocol) client: owner-configured servers as persona tools.

Design
  - Servers are added only through the owner REST API. A stdio server is an
    arbitrary local command, so the model never creates or edits one.
  - Each enabled server runs in one long-lived task on a private asyncio loop
    (daemon thread). Sync ToolDispatcher calls are bridged with
    run_coroutine_threadsafe, so the rest of the app stays synchronous.
  - Remote tools are exposed as ``mcp__<server>__<tool>``. They are granted to a
    persona only through its tool_permissions (default deny), like built-ins.
  - Tool descriptions and tool results are DATA, never instructions.

Requires the ``mcp`` package (2.x API). Imported lazily, so the rest of the app
runs without it; starting a server then reports a clear error.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .models import new_id, now_ts

PREFIX = "mcp__"
TRANSPORTS = ("stdio", "http")
MAX_TOOL_NAME = 64
MAX_DESCRIPTION_CHARS = 1000
MAX_RESULT_CHARS = 20_000
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 300.0

_SERVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]")
_HTTP_URL_RE = re.compile(r"^https?://\S+$")


class McpError(Exception):
    pass


@dataclass
class McpServerConfig:
    id: str = field(default_factory=new_id)
    name: str = ""                  # slug; becomes part of tool names
    transport: str = "stdio"        # stdio | http
    command: str = ""               # stdio: executable (no shell)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)       # stdio: extra env
    url: str = ""                   # http: streamable-HTTP endpoint
    headers: dict[str, str] = field(default_factory=dict)   # http: e.g. auth header
    enabled: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @staticmethod
    def from_dict(d: dict) -> "McpServerConfig":
        known = McpServerConfig.__dataclass_fields__
        return McpServerConfig(**{k: v for k, v in d.items() if k in known})


def validate_server_config(cfg: McpServerConfig) -> McpServerConfig:
    if not _SERVER_NAME_RE.match(cfg.name or ""):
        raise ValueError("name must match [a-z][a-z0-9_]{1,23}")
    if cfg.transport not in TRANSPORTS:
        raise ValueError(f"transport must be one of: {', '.join(TRANSPORTS)}")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in {**cfg.env, **cfg.headers}.items()):
        raise ValueError("env and headers must map strings to strings")
    if cfg.transport == "stdio":
        if not cfg.command.strip():
            raise ValueError("stdio server requires command")
        if cfg.url or cfg.headers:
            raise ValueError("url and headers apply to the http transport only")
        cfg.args = [str(a) for a in cfg.args]
    else:
        if not _HTTP_URL_RE.match(cfg.url or ""):
            raise ValueError("http server requires an http(s) url")
        if cfg.command or cfg.env or cfg.args:
            raise ValueError("command, args and env apply to the stdio transport only")
    cfg.timeout_seconds = max(1.0, min(float(cfg.timeout_seconds), MAX_TIMEOUT))
    return cfg


def public_view(cfg: McpServerConfig) -> dict:
    """Config as exposed by the API: secret values are masked, keys stay visible."""
    d = cfg.to_dict()
    d["env"] = {k: "***" for k in cfg.env}
    d["headers"] = {k: "***" for k in cfg.headers}
    return d


def exposed_tool_name(server: str, tool: str) -> str:
    raw = f"{PREFIX}{server}__{_UNSAFE_CHARS_RE.sub('_', tool)}"
    if len(raw) <= MAX_TOOL_NAME:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return raw[: MAX_TOOL_NAME - 9] + "_" + digest


def is_mcp_tool_name(name: str) -> bool:
    return name.startswith(PREFIX)


class McpServerRegistry:
    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else None
        self._servers: dict[str, McpServerConfig] = {}
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            self.load()

    def all(self) -> list[McpServerConfig]:
        return list(self._servers.values())

    def get(self, server_id: str) -> Optional[McpServerConfig]:
        return self._servers.get(server_id)

    def add(self, cfg: McpServerConfig) -> McpServerConfig:
        validate_server_config(cfg)
        with self._lock:
            if any(s.name == cfg.name for s in self._servers.values()):
                raise ValueError(f"MCP server name already exists: {cfg.name}")
            self._servers[cfg.id] = cfg
            self.save()
        return cfg

    def update(self, server_id: str, changes: dict) -> McpServerConfig:
        cur = self._require(server_id)
        changes = dict(changes)
        # Masked secrets ("***", as returned by public_view) keep the stored value.
        for key in ("env", "headers"):
            if key in changes and isinstance(changes[key], dict):
                old = getattr(cur, key)
                changes[key] = {k: (old.get(k, "") if v == "***" else v) for k, v in changes[key].items()}
        candidate = McpServerConfig.from_dict({**cur.to_dict(), **changes, "id": cur.id,
                                               "created_at": cur.created_at})
        validate_server_config(candidate)
        with self._lock:
            if any(s.name == candidate.name and s.id != cur.id for s in self._servers.values()):
                raise ValueError(f"MCP server name already exists: {candidate.name}")
            candidate.updated_at = now_ts()
            self._servers[cur.id] = candidate
            self.save()
        return candidate

    def remove(self, server_id: str) -> None:
        self._require(server_id)
        with self._lock:
            self._servers.pop(server_id, None)
            self.save()

    def _require(self, server_id: str) -> McpServerConfig:
        cfg = self._servers.get(server_id)
        if cfg is None:
            raise KeyError(server_id)
        return cfg

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [s.to_dict() for s in self._servers.values()]
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._servers = {}
        for d in data:
            cfg = McpServerConfig.from_dict(d)
            self._servers[cfg.id] = cfg


@dataclass
class _Connection:
    config: McpServerConfig
    status: str = "connecting"       # connecting | ready | error | stopped
    error: str = ""
    session: Any = None
    stop_event: Any = None
    task: Any = None
    tools: dict[str, tuple[str, dict]] = field(default_factory=dict)  # exposed -> (remote, schema)


class McpManager:
    def __init__(self, registry: McpServerRegistry):
        self.registry = registry
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="pai-mcp-loop", daemon=True)
        self._conns: dict[str, _Connection] = {}
        self._started = False
        self._lock = threading.Lock()

    # ----- lifecycle (sync, callable from any thread) -----

    def start(self) -> None:
        """Start the loop thread and connect every enabled server."""
        if not self._started:
            self._thread.start()
            self._started = True
        for cfg in self.registry.all():
            if cfg.enabled:
                self.reload(cfg.id)

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._run(self._disconnect_all(), timeout=15)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def reload(self, server_id: str) -> dict:
        """(Re)connect one server according to its current config; returns its status."""
        cfg = self.registry.get(server_id)
        if cfg is None:
            raise KeyError(server_id)
        if not self._started:
            self._thread.start()
            self._started = True
        if not cfg.enabled:
            self._run(self._disconnect(server_id), timeout=15)
        else:
            self._run(self._connect(cfg), timeout=cfg.timeout_seconds + 10)
        return self.server_status(server_id)

    def remove(self, server_id: str) -> None:
        if self._started:
            self._run(self._disconnect(server_id), timeout=15)
        self.registry.remove(server_id)

    # ----- queries -----

    def server_status(self, server_id: str) -> dict:
        cfg = self.registry.get(server_id)
        conn = self._conns.get(server_id)
        if conn is None:
            status = "disabled" if cfg and not cfg.enabled else "stopped"
            tools, error = [], ""
        else:
            status, error = conn.status, conn.error
            tools = sorted(conn.tools)
        return {"id": server_id, "name": cfg.name if cfg else "", "status": status,
                "error": error, "tools": tools}

    def statuses(self) -> list[dict]:
        return [self.server_status(s.id) for s in self.registry.all()]

    def schemas(self) -> list[dict]:
        """OpenAI-style schemas of all currently connected tools."""
        out = []
        for conn in list(self._conns.values()):
            if conn.status == "ready":
                out.extend(schema for _, schema in conn.tools.values())
        return out

    def tool_names(self) -> set[str]:
        return {s["function"]["name"] for s in self.schemas()}

    def has_tool(self, name: str) -> bool:
        return self._route(name) is not None

    # ----- invocation (sync) -----

    def call(self, exposed_name: str, arguments: dict) -> dict:
        route = self._route(exposed_name)
        if route is None:
            raise McpError(f"MCP tool not connected: {exposed_name}")
        conn, remote = route
        timeout = conn.config.timeout_seconds
        fut = asyncio.run_coroutine_threadsafe(self._call_remote(conn, remote, arguments or {}), self._loop)
        try:
            return fut.result(timeout=timeout + 5)
        except concurrent.futures.TimeoutError as e:
            fut.cancel()
            raise McpError(f"MCP call timed out after {timeout:g}s: {exposed_name}") from e

    # ----- internals -----

    def _run(self, coro, timeout: float):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise

    def _route(self, exposed_name: str):
        for conn in list(self._conns.values()):
            if conn.status == "ready" and exposed_name in conn.tools:
                return conn, conn.tools[exposed_name][0]
        return None

    async def _connect(self, cfg: McpServerConfig) -> None:
        await self._disconnect(cfg.id)
        conn = _Connection(config=cfg)
        conn.stop_event = asyncio.Event()
        self._conns[cfg.id] = conn
        ready: asyncio.Future = self._loop.create_future()
        conn.task = asyncio.create_task(self._serve(conn, ready))
        try:
            await asyncio.wait_for(asyncio.shield(ready), timeout=cfg.timeout_seconds)
        except BaseException as e:  # timeout, cancellation, or server-side failure
            if not conn.task.done():
                conn.task.cancel()
            if conn.status != "error":
                conn.status = "error"
                conn.error = str(e) or type(e).__name__

    async def _serve(self, conn: _Connection, ready: asyncio.Future) -> None:
        cfg = conn.config
        try:
            from mcp import ClientSession  # lazy: the app runs without the mcp package
            async with self._transport(cfg) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write, read_timeout_seconds=cfg.timeout_seconds) as session:
                    await session.initialize()
                    conn.tools = await self._list_tools(session, cfg)
                    conn.session = session
                    conn.status = "ready"
                    if not ready.done():
                        ready.set_result(None)
                    await conn.stop_event.wait()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            conn.status = "error"
            conn.error = f"{type(e).__name__}: {e}"[:500]
            if not ready.done():
                ready.set_exception(e)
        finally:
            conn.session = None
            conn.tools = {}
            if conn.status == "ready":
                conn.status = "stopped"

    def _transport(self, cfg: McpServerConfig):
        if cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import get_default_environment, stdio_client
            params = StdioServerParameters(
                command=cfg.command, args=list(cfg.args),
                env={**get_default_environment(), **cfg.env},
            )
            return stdio_client(params)
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
        http_client = create_mcp_http_client(headers=dict(cfg.headers),
                                             timeout=_httpx_timeout(cfg.timeout_seconds))
        return streamable_http_client(cfg.url, http_client=http_client)

    async def _list_tools(self, session, cfg: McpServerConfig) -> dict[str, tuple[str, dict]]:
        from mcp.types import PaginatedRequestParams
        tools, cursor = [], None
        while True:
            params = PaginatedRequestParams(cursor=cursor) if cursor else None
            page = await session.list_tools(params=params)
            tools.extend(page.tools)
            cursor = getattr(page, "next_cursor", None)
            if not cursor or len(tools) > 500:
                break
        index: dict[str, tuple[str, dict]] = {}
        for t in tools:
            exposed = exposed_tool_name(cfg.name, t.name)
            if exposed in index:
                continue  # sanitized-name collision: keep the first
            schema_in = t.input_schema if isinstance(t.input_schema, dict) else {}
            params_schema = {"type": "object", "properties": {}, **schema_in}
            desc = f"[MCP server {cfg.name}] {t.description or t.name}"
            index[exposed] = (t.name, {"type": "function", "function": {
                "name": exposed,
                "description": desc[:MAX_DESCRIPTION_CHARS],
                "parameters": params_schema,
            }})
        return index

    async def _call_remote(self, conn: _Connection, remote: str, arguments: dict) -> dict:
        if conn.session is None:
            raise McpError(f"MCP server '{conn.config.name}' is not connected")
        result = await conn.session.call_tool(
            remote, arguments, read_timeout_seconds=conn.config.timeout_seconds)
        parts = []
        for block in result.content or []:
            if getattr(block, "type", "") == "text":
                parts.append(block.text)
            else:
                parts.append(f"[{getattr(block, 'type', 'unknown')} content omitted]")
        text = "\n".join(parts)
        truncated = len(text) > MAX_RESULT_CHARS
        text = text[:MAX_RESULT_CHARS]
        out: dict[str, Any] = {"server": conn.config.name, "tool": remote,
                               "ok": not result.is_error, "output": text, "truncated": truncated}
        if result.structured_content is not None:
            raw = json.dumps(result.structured_content, ensure_ascii=False, default=str)
            if len(raw) <= MAX_RESULT_CHARS:
                out["structured"] = result.structured_content
            else:
                out["structured_truncated"] = True
        if result.is_error:
            out["tool_failed"] = True
            out["error"] = text[:2000] or "MCP tool returned an error"
        return out

    async def _disconnect(self, server_id: str) -> None:
        conn = self._conns.pop(server_id, None)
        if conn is None:
            return
        conn.tools = {}
        conn.status = "stopped"
        if conn.stop_event is not None:
            conn.stop_event.set()
        if conn.task is not None and not conn.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(conn.task), timeout=5)
            except BaseException:
                conn.task.cancel()

    async def _disconnect_all(self) -> None:
        for server_id in list(self._conns):
            await self._disconnect(server_id)


def _httpx_timeout(seconds: float):
    import httpx2
    return httpx2.Timeout(seconds, read=seconds)
