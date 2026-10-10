"""Tool layer: memory, web search/fetch (requests + BeautifulSoup4),
HTTP requests, and local command execution."""
from __future__ import annotations

import html
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .models import BudgetProfile, MemoryFilter, Persona, now_ts
from .settings import termux_bash_path
from .time_utils import calculate_time_delta as _calculate_time_delta

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_fragment",
            "description": "Get the exact content of a memory fragment by ID.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_archive",
            "description": "Full-text search across the archive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "space_id": {"type": "string"},
                    "date_from": {"type": "string"},
                    "date_to": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_time_delta",
            "description": "Compute the difference between two dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date1": {"type": "string"},
                    "date2": {"type": "string"},
                },
                "required": ["date1", "date2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_fragments_by_timeframe",
            "description": "List fragments within a time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string"},
                    "date_to": {"type": "string"},
                    "space_id": {"type": "string"},
                },
                "required": ["date_from", "date_to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web (DuckDuckGo). Returns {title, url, snippet}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Follow a link (e.g. from web_search results) and get the "
                "page's content. The mode parameter controls what's returned: "
                "'text' — page text with tags stripped, 'html' — the raw "
                "HTML source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full page URL, including scheme (https://...)."},
                    "mode": {
                        "type": "string",
                        "enum": ["text", "html"],
                        "default": "text",
                        "description": "'text' — readable text only, 'html' — raw HTML.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "default": 8000,
                        "description": "Maximum length of the returned content, in characters.",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": (
                "Send a direct HTTP request with requests. Supports GET, POST, PUT, "
                "PATCH and DELETE. Use headers, query params, JSON body or raw body "
                "when needed. Returns status, headers and parsed JSON/text response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                    "url": {"type": "string"},
                    "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                    "params": {"type": "object"},
                    "json": {"type": "object", "description": "JSON request body for POST/PUT/PATCH."},
                    "body": {"type": "string", "description": "Raw request body; use instead of json."},
                    "timeout": {"type": "number", "default": 20},
                    "max_chars": {"type": "integer", "default": 200000}
                },
                "required": ["method", "url"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": (
                "Execute a local command. runtime='bash' runs bash -lc, runtime='termux' "
                "runs the Termux shell, and runtime='python3' runs python3 -c with the "
                "command as Python source. Use only when the user explicitly needs local "
                "execution. Output is capped and the process has a timeout."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "runtime": {"type": "string", "enum": ["bash", "termux", "python3"], "default": "bash"},
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                    "max_output_chars": {"type": "integer", "default": 50000}
                },
                "required": ["command"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_function",
            "description": (
                "Propose a new custom function. The function is created only as a "
                "draft and does not become active without explicit owner confirmation "
                "on the 'Functions' page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Unique function name in snake_case/Latin script."},
                    "description": {"type": "string"},
                    "parameters": {"type": "object", "description": "JSON Schema of the input parameters."},
                    "kind": {"type": "string", "enum": ["llm_prompt", "http_json", "python", "dynamic"], "description": "python allows running arbitrary inline_code only after validate and explicit activate by the owner."},
                    "implementation": {"type": "object", "description": "For python: {\"inline_code\": \"def function_name(...): ...\"}. The code runs only after validate + activate."},
                },
                "required": ["name", "description", "parameters", "kind", "implementation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_rule",
            "description": (
                "Propose a new self-generated behavior rule (a pattern noticed in the "
                "dialogue). The rule is created only as a draft and does not become "
                "active without explicit owner confirmation on the 'Rules' page. If "
                "scope='persona' and persona_id is not given, the rule is automatically "
                "attached to the currently active persona."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_text": {"type": "string", "description": "The rule text in plain language, e.g.: 'If the user writes \u201cplan for the day\u201d, show open commitments first.'"},
                    "scope": {
                        "type": "string",
                        "enum": ["global", "persona", "space", "project"],
                        "default": "global",
                        "description": "global — always applies; persona — only for the current/given persona; space/project — only for a specific space.",
                    },
                    "space_id": {"type": "string", "description": "Required when scope='space' or 'project'."},
                    "trigger_context": {"type": "string", "description": "Brief description of the situation/context in which this rule was noticed."},
                    "confidence": {"type": "number", "default": 0.5, "description": "The model's confidence in the rule's usefulness, from 0 to 1."},
                    "derived_from": {"type": "array", "items": {"type": "string"}, "description": "IDs of memory fragments the rule was derived from (if any)."},
                },
                "required": ["rule_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_skill",
            "description": (
                "Load the full instructions of one of the active skills listed in the "
                "system prompt. Call it BEFORE acting when a skill's trigger matches the "
                "user's request, then follow the returned instructions."
            ),
            "parameters": {
                "type": "object",
                "properties": {"skill_id": {"type": "string", "description": "Skill id from the active skills list."}},
                "required": ["skill_id"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Persona management: only the Second Brain persona has these schemas, and they
# are never granted through tool_permissions.
# ---------------------------------------------------------------------------
_PERSONA_FIELD_PROPS = {
    "name": {"type": "string", "description": "Display name (max 80 chars)."},
    "description": {"type": "string", "description": "What the persona is for (max 1000 chars)."},
    "tone": {"type": "string", "description": "Tone, e.g. 'neutral', 'friendly' (max 100 chars)."},
    "system_core_addon": {"type": "string", "description": "Persona instructions (max 6000 chars)."},
    "active_skill_ids": {"type": "array", "items": {"type": "string"}, "description": "Skill ids from the skills list."},
    "allow_spaces": {"type": "array", "items": {"type": "string"}, "description": "Memory spaces the persona may read. Empty = all."},
    "deny_spaces": {"type": "array", "items": {"type": "string"}, "description": "Memory spaces hidden from the persona."},
    "allow_tags": {"type": "array", "items": {"type": "string"}, "description": "Only memory with these tags. Empty = any."},
    "deny_tags": {"type": "array", "items": {"type": "string"}, "description": "Memory with these tags is hidden."},
    "tool_permissions": {"type": "array", "items": {"type": "string"},
                         "description": "Tools the persona may use. Cannot include shell_exec or persona-management tools."},
}

PERSONA_TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "list_personas",
        "description": "List all personas with their description, tone, tool permissions and memory filters.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_persona",
        "description": "Full settings of one persona, including its instructions (system_core_addon) and skills.",
        "parameters": {"type": "object", "properties": {"persona_id": {"type": "string"}},
                       "required": ["persona_id"]},
    }},
    {"type": "function", "function": {
        "name": "create_persona",
        "description": "Create a new persona. Use only when the owner explicitly asks. New personas get "
                       "read-only memory and web tools by default; shell_exec can only be granted by the owner in the UI.",
        "parameters": {"type": "object",
                       "properties": {"persona_id": {"type": "string", "description": "[a-z][a-z0-9_]{1,31}"},
                                      **_PERSONA_FIELD_PROPS},
                       "required": ["persona_id", "name"]},
    }},
    {"type": "function", "function": {
        "name": "edit_persona",
        "description": "Change settings of an existing persona (not Second Brain). Pass only the fields to change. "
                       "Use only when the owner explicitly asks.",
        "parameters": {"type": "object",
                       "properties": {"persona_id": {"type": "string"}, **_PERSONA_FIELD_PROPS},
                       "required": ["persona_id"]},
    }},
]
TOOL_SCHEMAS.extend(PERSONA_TOOL_SCHEMAS)

BRAIN_ONLY_TOOLS = frozenset({"list_personas", "get_persona", "create_persona", "edit_persona"})
BRAIN_PERSONA_ID = "brain"
# Tools a model may never grant to a persona it creates or edits (the owner can, in the UI).
MODEL_GRANT_DENYLIST = frozenset({"shell_exec"})
_PERSONA_ID_RE = re.compile(r"[a-z][a-z0-9_]{1,31}")
_MAX_ADDON_CHARS = 6000
_DEFAULT_NEW_PERSONA_TOOLS = ["get_fragment", "search_archive", "calculate_time_delta", "web_search", "web_fetch"]
_PERSONA_STR_LIMITS = {"name": 80, "description": 1000, "tone": 100}
_PERSONA_LIST_FIELDS = ("allow_spaces", "deny_spaces", "allow_tags", "deny_tags", "tool_permissions", "active_skill_ids")


TOOL_PLAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "plan_tool_pool",
        "description": (
            "Orchestration-only control: plan independent real-tool calls for the next execution round. "
            "This control is never executed as a real tool. Each call must already be executable "
            "without the result of another call in the same pool. Put dependent calls into a later "
            "round. Use an empty calls array only when the task needs tools but there is no safe/useful "
            "call to make yet. If no tools are needed at all, answer directly instead of planning."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "arguments": {"type": "object"},
                            "id": {"type": "string"},
                        },
                        "required": ["name", "arguments"],
                    },
                }
            },
            "required": ["calls"],
        },
    },
}

def _iso_to_ts(s, end_of_day=False):
    """ISO date/datetime -> epoch seconds. A bare date used as an UPPER bound
    (end_of_day=True) means "through the end of that day" — otherwise
    date_to="2026-10-03" silently excluded everything that happened on the 3rd."""
    if not s:
        return None
    s = str(s).strip()
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    if end_of_day and len(s) <= 10:
        ts += 86399.999
    return ts


class ToolError(Exception):
    pass


_TAG_RE = re.compile(r'<[^>]+>')


def _strip_tags(s):
    return html.unescape(_TAG_RE.sub("", s)).strip()


def _ddg_unwrap(url):
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.path == "/l/" or (parsed.netloc.endswith("duckduckgo.com") and "uddg" in parsed.query):
        qs = urllib.parse.parse_qs(parsed.query)
        target = qs.get("uddg")
        if target:
            return urllib.parse.unquote(target[0])
    return url


def _http_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.8",
    })
    return session


def _bs4_text(node):
    """Convert HTML to readable text using BeautifulSoup4."""
    for bad in node.select("script, style, noscript, template"):
        bad.decompose()
    return "\n".join(
        line.strip()
        for line in node.get_text("\n", strip=True).splitlines()
        if line.strip()
    )


def web_search(query, limit=5, timeout=8.0):
    """Search DuckDuckGo HTML using requests + BeautifulSoup4."""
    limit = max(1, min(int(limit), 10))
    try:
        resp = _http_session().get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ToolError(f"web_search network error: {e}") from e

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen = set()

    for block in soup.select(".result"):
        link = block.select_one("a.result__a")
        if not link:
            continue
        href = _ddg_unwrap(link.get("href", ""))
        title = link.get_text(" ", strip=True)
        snippet_el = block.select_one(".result__snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        if not href or not title or href in seen:
            continue
        if not href.startswith(("http://", "https://")):
            continue
        seen.add(href)
        results.append({
            "title": title,
            "url": href,
            "snippet": snippet[:300],
        })
        if len(results) >= limit:
            break

    return results


def _tool_http_request_impl(args):
    method = str(args.get("method", "GET")).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ToolError(f"Unsupported HTTP method: {method}")
    url = str(args.get("url", "")).strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ToolError(f"http_request invalid URL: {url!r}")

    timeout = max(1, min(float(args.get("timeout", 20)), 120))
    max_chars = max(1, min(int(args.get("max_chars", 200_000)), 1_000_000))
    headers = {str(k): str(v) for k, v in (args.get("headers") or {}).items()}
    params = args.get("params") or {}
    json_body = args.get("json", None)
    raw_body = args.get("body", None)
    if json_body is not None and raw_body is not None:
        raise ToolError("Use either 'json' or 'body', not both")

    try:
        response = _http_session().request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            data=raw_body,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        raise ToolError(f"http_request network error: {e}") from e

    text = response.text[:max_chars]
    try:
        payload = response.json()
        response_kind = "json"
    except ValueError:
        payload = text
        response_kind = "text"

    return {
        "method": method,
        "url": url,
        "final_url": response.url,
        "status": response.status_code,
        "ok": response.ok,
        "headers": dict(response.headers),
        "content_type": response.headers.get("Content-Type", ""),
        "response_kind": response_kind,
        "result": payload,
        "truncated": len(response.text) > max_chars,
    }


def _tool_shell_exec_impl(args):
    runtime = str(args.get("runtime", "bash")).lower()
    command = str(args.get("command", ""))
    if not command.strip():
        raise ToolError("shell_exec requires a non-empty command")
    timeout = max(1, min(int(args.get("timeout", 30)), 300))
    max_output = max(1024, min(int(args.get("max_output_chars", 50_000)), 500_000))
    cwd = str(args.get("cwd") or os.getcwd())

    if runtime == "bash":
        argv = ["bash", "-lc", command]
    elif runtime == "termux":
        # Prefer the Termux bash if available; otherwise use the normal shell
        # so the tool also works when the server itself is running in Termux.
        termux_bash = termux_bash_path()
        if termux_bash and os.path.exists(termux_bash):
            argv = [termux_bash, "-lc", command]
        else:
            argv = ["bash", "-lc", command]
    elif runtime == "python3":
        argv = ["python3", "-c", command]
    else:
        raise ToolError("runtime must be one of: bash, termux, python3")

    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except FileNotFoundError as e:
        raise ToolError(f"Runtime not found: {argv[0]}") from e
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        partial = out[-max_output:]
        raise ToolError(f"Command timed out after {timeout}s. Partial output:\n{partial}") from e
    except OSError as e:
        raise ToolError(f"Failed to execute command: {e}") from e

    stdout = (proc.stdout or "")
    stderr = (proc.stderr or "")
    combined = (stdout + ("\n[stderr]\n" + stderr if stderr else ""))
    truncated = len(combined) > max_output
    if truncated:
        combined = combined[-max_output:]
    return {
        "runtime": runtime,
        "command": command,
        "cwd": cwd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "output": combined,
        "truncated": truncated,
    }


_MAX_FETCH_BYTES = 2_000_000
_META_CHARSET_RE = re.compile(rb'<meta[^>]+charset=["\']?([\w-]+)', re.IGNORECASE)


def _html_to_text(body: str) -> str:
    """HTML -> readable text via BeautifulSoup (scripts/styles removed)."""
    return _bs4_text(BeautifulSoup(body, "html.parser"))


def _normalize_url(url: str) -> str:
    """Make Unicode URLs (IDN hosts, Cyrillic paths/queries) requestable."""
    try:
        parts = urllib.parse.urlsplit(url)
        netloc = parts.netloc.encode("idna").decode("ascii")
        path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
        query = urllib.parse.quote(parts.query, safe="=&?/%:@!$'()*+,;-._~")
        fragment = urllib.parse.quote(parts.fragment, safe="")
        return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, fragment))
    except Exception:
        return url


def _decode_body(raw: bytes, content_type: str) -> str:
    charset = None
    m = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    if m:
        charset = m.group(1)
    else:
        m2 = _META_CHARSET_RE.search(raw[:4096])
        if m2:
            charset = m2.group(1).decode("ascii", errors="ignore")
    for enc in (charset, "utf-8"):
        if not enc:
            continue
        try:
            return raw.decode(enc, errors="strict" if enc == "utf-8" else "replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_url(url, mode="text", timeout=10.0, max_chars=8000):
    """Follow a link and return its page content.

    mode="text" returns readable text (HTML parsed with BeautifulSoup4),
    mode="html" returns the raw source. Returns
    {"url", "final_url", "status", "mode", "content", "truncated"}
    or raises ToolError."""
    if mode not in ("text", "html"):
        mode = "text"
    max_chars = max(1, min(int(max_chars), 200_000))
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ToolError(f"web_fetch invalid URL: {url!r}")
    request_url = _normalize_url(url)

    try:
        with _http_session().get(request_url, timeout=timeout, allow_redirects=True, stream=True) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            ct = content_type.lower()
            if ct and not any(t in ct for t in ("text", "html", "xml", "json")):
                raise ToolError(f"web_fetch: unsupported content type '{content_type}' for {url}")
            chunks, total = [], 0
            for chunk in resp.iter_content(65536):
                chunks.append(chunk)
                total += len(chunk)
                if total >= _MAX_FETCH_BYTES:
                    break
            raw = b"".join(chunks)[:_MAX_FETCH_BYTES]
            status, final_url = resp.status_code, resp.url
    except requests.RequestException as e:
        raise ToolError(f"web_fetch network error: {e}") from e

    body = _decode_body(raw, content_type)
    is_html = "html" in ct or "xml" in ct or (not ct and body.lstrip()[:1] == "<")
    content = body if (mode == "html" or not is_html) else _html_to_text(body)
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]

    return {
        "url": url,
        "final_url": final_url,
        "status": status,
        "mode": mode,
        "content": content,
        "truncated": truncated,
    }


class ToolDispatcher:
    def __init__(self, storage, reference_now=None, custom_functions=None, llm=None, rules_manager=None, skills=None,
                 persona_registry=None):
        self.skills = skills
        self.persona_registry = persona_registry
        self.storage = storage
        self.reference_now = reference_now
        self.custom_functions = custom_functions
        self.llm = llm
        self.rules_manager = rules_manager

    def schemas_for_persona(self, persona):
        # Built-in tools are granted only according to the persona's permissions.
        is_brain = getattr(persona, "persona_id", None) == BRAIN_PERSONA_ID
        builtins = [
            s for s in TOOL_SCHEMAS
            if (s["function"]["name"] in BRAIN_ONLY_TOOLS and is_brain and self.persona_registry is not None)
            or (s["function"]["name"] not in BRAIN_ONLY_TOOLS and s["function"]["name"] in persona.tool_permissions)
            or (s["function"]["name"] == "propose_function" and self.custom_functions is not None)
            or (s["function"]["name"] == "propose_rule" and self.rules_manager is not None)
            # get_skill is granted automatically to personas that have skills
            or (s["function"]["name"] == "get_skill" and self.skills is not None
                and bool(getattr(persona, "active_skill_ids", None)))
        ]
        # Custom functions are also persona tools: an active function must be
        # explicitly selected in tool_permissions.
        custom = []
        if self.custom_functions:
            allowed = set(persona.tool_permissions)
            custom = [s for s in self.custom_functions.schemas() if s["function"]["name"] in allowed]
        return builtins + custom

    def dispatch(self, name, arguments, persona=None):
        if persona is not None and name not in persona.tool_permissions and name not in ("propose_function", "propose_rule", "get_skill", *BRAIN_ONLY_TOOLS):
            raise ToolError(f"Persona not permitted to use tool '{name}'")
        handler = getattr(self, f"_tool_{name}", None)
        if handler is not None:
            try:
                return handler(arguments, persona)
            except Exception as e:
                return {"tool_failed": True, "error": str(e), "tool": name}
        if self.custom_functions and self.custom_functions.get_by_name(name):
            try:
                return self.custom_functions.execute(name, arguments, self.llm)
            except Exception as e:
                return {"tool_failed": True, "error": str(e), "tool": name}
        return {"tool_failed": True, "error": f"Unknown tool: {name}", "tool": name}

    def _tool_http_request(self, args, persona):
        try:
            result = _tool_http_request_impl(args)
        except ToolError as e:
            return {"tool_failed": True, "error": str(e), "tool": "http_request"}
        return result

    def _tool_shell_exec(self, args, persona):
        try:
            result = _tool_shell_exec_impl(args)
        except ToolError as e:
            return {"tool_failed": True, "error": str(e), "tool": "shell_exec"}
        return result

    def _tool_propose_function(self, args, persona):
        if not self.custom_functions:
            raise ToolError("Custom function registry is not configured")
        try:
            from .custom_functions import CustomFunction
            # LLM proposals are always saved as a draft. Even if the
            # implementation doesn't yet pass strict validation (e.g. it
            # includes inline_code), the owner must see it on the
            # "Functions" page and decide what to do.
            fn = self.custom_functions.add(CustomFunction(
                name=args["name"],
                description=args.get("description", ""),
                parameters=args.get("parameters", {"type": "object", "properties": {}}),
                kind=args.get("kind", "llm_prompt"),
                implementation=args.get("implementation", {}),
            ), validate=False)
        except (KeyError, ValueError) as e:
            raise ToolError(f"Cannot create function proposal: {e}")
        validation_error = None
        try:
            self.custom_functions._validate(fn)
        except ValueError as e:
            validation_error = str(e)

        return {
            "created": True,
            "id": fn.id,
            "name": fn.name,
            "status": fn.status,
            "valid": validation_error is None,
            "validation_error": validation_error,
            "message": (
                "Function saved as a draft. It is not active and cannot be called until "
                "the owner confirms it on the \"Functions\" page."
                if validation_error is None else
                f"Function saved as a draft, but needs fixing before it can be confirmed: {validation_error}"
            ),
        }

    def _tool_propose_rule(self, args, persona):
        if not self.rules_manager:
            raise ToolError("Rules manager is not configured")
        from .models import RuleScope, RuleStatus, SelfGeneratedRule

        rule_text = str(args.get("rule_text", "")).strip()
        if not rule_text:
            raise ToolError("propose_rule requires a non-empty rule_text")

        scope_raw = str(args.get("scope", "global") or "global").strip().lower()
        try:
            scope = RuleScope(scope_raw)
        except ValueError:
            raise ToolError(f"Unsupported rule scope: {scope_raw}")

        persona_id = args.get("persona_id")
        # Auto-attach: a rule with scope='persona' proposed without an
        # explicit persona_id is automatically attached to the currently
        # active persona (the one the dialogue is being conducted as). This
        # lets the model propose rules "for itself" without knowing the
        # exact persona_id in advance.
        if scope == RuleScope.PERSONA and not persona_id:
            if persona is None:
                raise ToolError("scope='persona' requires an active persona in this context")
            persona_id = persona.persona_id

        space_id = args.get("space_id")
        if scope in (RuleScope.SPACE, RuleScope.PROJECT) and not space_id:
            raise ToolError(f"scope='{scope.value}' requires space_id")

        rule = self.rules_manager.add(SelfGeneratedRule(
            rule_text=rule_text,
            scope=scope,
            persona_id=persona_id,
            space_id=space_id,
            trigger_context=str(args.get("trigger_context", "") or ""),
            confidence=float(args.get("confidence", 0.5) or 0.5),
            derived_from=list(args.get("derived_from") or []),
            status=RuleStatus.DRAFT,
        ))

        return {
            "created": True,
            "id": rule.id,
            "scope": rule.scope.value,
            "persona_id": rule.persona_id,
            "space_id": rule.space_id,
            "status": rule.status.value,
            "message": (
                "Rule saved as a draft. It has no effect and does not influence "
                "behavior until the owner confirms it on the \"Rules\" page."
            ),
        }

    # ---- persona management (Second Brain only) ----

    def _require_brain(self, persona):
        if persona is None or getattr(persona, "persona_id", None) != BRAIN_PERSONA_ID:
            raise ToolError("This tool is available only to the Second Brain persona")
        if self.persona_registry is None:
            raise ToolError("Persona registry is not configured")

    def _persona_known_tools(self):
        known = {s["function"]["name"] for s in TOOL_SCHEMAS} - BRAIN_ONLY_TOOLS
        if self.custom_functions:
            known.update(f.name for f in self.custom_functions.active())
        return known

    def _clean_persona_fields(self, args, caller):
        """Validate the optional persona fields in args; return only those present."""
        out = {}
        for key, limit in _PERSONA_STR_LIMITS.items():
            if args.get(key) is not None:
                val = str(args[key]).strip()
                if key == "name" and not val:
                    raise ToolError("name must not be empty")
                if len(val) > limit:
                    raise ToolError(f"{key} is too long (max {limit} characters)")
                out[key] = val
        if args.get("system_core_addon") is not None:
            val = str(args["system_core_addon"]).strip()
            if len(val) > _MAX_ADDON_CHARS:
                raise ToolError(f"system_core_addon is too long (max {_MAX_ADDON_CHARS} characters)")
            out["system_core_addon"] = val
        for key in _PERSONA_LIST_FIELDS:
            if args.get(key) is None:
                continue
            raw = args[key]
            if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
                raise ToolError(f"{key} must be an array of non-empty strings")
            out[key] = list(dict.fromkeys(x.strip() for x in raw))
        if "tool_permissions" in out:
            tools = out["tool_permissions"]
            denied = [t for t in tools if t in MODEL_GRANT_DENYLIST or t in BRAIN_ONLY_TOOLS]
            if denied:
                raise ToolError(f"These tools cannot be granted through this tool: {denied}")
            unknown = [t for t in tools if t not in self._persona_known_tools()]
            if unknown:
                raise ToolError(f"Unknown tool(s): {unknown}. Known: {sorted(self._persona_known_tools())}")
            beyond = [t for t in tools if t not in (caller.tool_permissions or [])]
            if beyond:
                raise ToolError(f"Cannot grant tools you do not have yourself: {beyond}")
        if "active_skill_ids" in out and self.skills is not None:
            unknown = [x for x in out["active_skill_ids"] if self.skills.get(x) is None]
            if unknown:
                raise ToolError(f"Unknown skill id(s): {unknown}")
        return out

    @staticmethod
    def _persona_brief(p):
        mf = p.memory_filter
        return {"persona_id": p.persona_id, "name": p.name, "description": p.description,
                "tone": p.tone, "status": p.status, "tool_permissions": list(p.tool_permissions),
                "allow_spaces": list(mf.allow_spaces), "deny_spaces": list(mf.deny_spaces),
                "allow_tags": list(mf.allow_tags), "deny_tags": list(mf.deny_tags)}

    @staticmethod
    def _persona_full(p):
        return {**ToolDispatcher._persona_brief(p), "system_core_addon": p.system_core_addon,
                "active_skill_ids": list(p.active_skill_ids)}

    def _tool_list_personas(self, args, persona):
        self._require_brain(persona)
        return {"personas": [self._persona_brief(p) for p in self.persona_registry.all()]}

    def _tool_get_persona(self, args, persona):
        self._require_brain(persona)
        pid = str(args.get("persona_id", "")).strip()
        target = self.persona_registry.get(pid)
        if target is None:
            return {"found": False, "persona_id": pid}
        return {"found": True, "persona": self._persona_full(target)}

    def _tool_create_persona(self, args, persona):
        self._require_brain(persona)
        persona_id = str(args.get("persona_id", "")).strip()
        if not _PERSONA_ID_RE.fullmatch(persona_id):
            raise ToolError("persona_id must match [a-z][a-z0-9_]{1,31}")
        if self.persona_registry.get(persona_id) is not None:
            raise ToolError(f"Persona '{persona_id}' already exists; use edit_persona")
        f = self._clean_persona_fields(args, persona)
        if not f.get("name"):
            raise ToolError("name is required")
        new = Persona(
            persona_id=persona_id,
            name=f["name"],
            description=f.get("description", ""),
            system_core_addon=f.get("system_core_addon", ""),
            active_skill_ids=f.get("active_skill_ids", []),
            memory_filter=MemoryFilter(
                allow_spaces=f.get("allow_spaces", []), deny_spaces=f.get("deny_spaces", []),
                allow_tags=f.get("allow_tags", []), deny_tags=f.get("deny_tags", []),
            ),
            tool_permissions=f.get("tool_permissions", list(_DEFAULT_NEW_PERSONA_TOOLS)),
            budget_profile=BudgetProfile(),
            tone=f.get("tone", "neutral"),
        )
        self.persona_registry.register(new)
        return {"created": True, "persona": self._persona_full(new)}

    def _tool_edit_persona(self, args, persona):
        self._require_brain(persona)
        pid = str(args.get("persona_id", "")).strip()
        if pid == BRAIN_PERSONA_ID:
            raise ToolError("The Second Brain persona cannot be edited with this tool")
        target = self.persona_registry.get(pid)
        if target is None:
            raise ToolError(f"Persona '{pid}' not found")
        f = self._clean_persona_fields(args, persona)
        if not f:
            raise ToolError("Nothing to change: pass at least one field besides persona_id")
        for key in ("name", "description", "system_core_addon", "tone", "active_skill_ids", "tool_permissions"):
            if key in f:
                setattr(target, key, f[key])
        for key in ("allow_spaces", "deny_spaces", "allow_tags", "deny_tags"):
            if key in f:
                setattr(target.memory_filter, key, f[key])
        target.updated_at = now_ts()
        self.persona_registry.register(target)
        return {"updated": True, "changed": sorted(f), "persona": self._persona_full(target)}

    def _tool_get_skill(self, args, persona):
        skill_id = str(args.get("skill_id", "")).strip()
        allowed = list(getattr(persona, "active_skill_ids", None) or [])
        if self.skills is None or skill_id not in allowed:
            return {"found": False, "skill_id": skill_id, "available": allowed}
        text = self.skills.fetch_full_text(skill_id)
        if text is None:
            return {"found": False, "skill_id": skill_id, "available": allowed}
        return {"found": True, "skill_id": skill_id, "instructions": text}

    def _tool_get_fragment(self, args, persona):
        node_id = args["id"]
        node = self.storage.get_fragment(node_id)
        if node is None:
            self.storage.log_retrieval("get_fragment", args, [])
            return {"found": False, "id": node_id}
        if persona and not persona.memory_filter.permits(node.space_id, node.tags):
            self.storage.log_retrieval("get_fragment", args, [])
            return {"found": False, "id": node_id, "reason": "access_denied_by_persona_filter"}
        self.storage.log_retrieval("get_fragment", args, [node_id])
        return {"found": True, "id": node.id, "space_id": node.space_id, "level": node.level,
                "type": node.type.value if hasattr(node.type, "value") else node.type,
                "summary_text": node.summary_text, "raw_content": node.raw_content,
                "occurred_at": node.occurred_at, "tags": node.tags}

    def _tool_search_archive(self, args, persona):
        query = args["query"]
        space_id = args.get("space_id")
        date_from = _iso_to_ts(args.get("date_from"))
        date_to = _iso_to_ts(args.get("date_to"), end_of_day=True)
        limit = max(1, min(int(args.get("limit", 10) or 10), 50))
        results = self.storage.search_archive(query, space_id=space_id, date_from=date_from, date_to=date_to, limit=limit * 3)
        if persona:
            # filter by space AND tags (e.g. a persona that hides "diary")
            results = [r for r in results if persona.memory_filter.permits(r["space_id"], r.get("tags") or [])]
        results = results[:limit]
        for r in results:
            r.pop("tags", None)
        self.storage.log_retrieval("search_archive", args, [r["id"] for r in results])
        return {"results": results}

    def _tool_calculate_time_delta(self, args, persona):
        result = _calculate_time_delta(args["date1"], args["date2"], reference_now=self.reference_now)
        self.storage.log_retrieval("calculate_time_delta", args, [])
        return result

    def _tool_list_fragments_by_timeframe(self, args, persona):
        date_from = _iso_to_ts(args["date_from"])
        date_to = _iso_to_ts(args["date_to"], end_of_day=True)
        space_id = args.get("space_id")
        nodes = self.storage.list_fragments_by_timeframe(date_from, date_to, space_id=space_id)
        if persona:
            nodes = [n for n in nodes if persona.memory_filter.permits(n.space_id, n.tags)]
        self.storage.log_retrieval("list_fragments_by_timeframe", args, [n.id for n in nodes])
        return {"results": [{"id": n.id, "space_id": n.space_id, "occurred_at": n.occurred_at, "summary_text": n.summary_text} for n in nodes]}

    def _tool_web_search(self, args, persona):
        query = args["query"]
        limit = args.get("limit", 5)
        try:
            results = web_search(query, limit=limit)
        except ToolError as e:
            self.storage.log_retrieval("web_search", args, [])
            return {"results": [], "error": str(e)}
        self.storage.log_retrieval("web_search", args, [r["url"] for r in results])
        return {"results": results}

    def _tool_web_fetch(self, args, persona):
        url = args["url"]
        mode = args.get("mode", "text")
        max_chars = args.get("max_chars", 8000)
        try:
            result = fetch_url(url, mode=mode, max_chars=max_chars)
        except ToolError as e:
            self.storage.log_retrieval("web_fetch", args, [])
            return {"url": url, "error": str(e)}
        self.storage.log_retrieval("web_fetch", args, [url])
        return result
