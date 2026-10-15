"""Web search, page fetch and HTTP request tools."""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

import requests
from bs4 import BeautifulSoup

from .errors import ToolError
from .schemas import BRAIN_PERSONA_ID

_TAG_RE = re.compile(r"<[^>]+>")


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
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.8",
        }
    )
    return session


def _bs4_text(node):
    """Convert HTML to readable text using BeautifulSoup4."""
    for bad in node.select("script, style, noscript, template"):
        bad.decompose()
    return "\n".join(line.strip() for line in node.get_text("\n", strip=True).splitlines() if line.strip())


_DDG_HTML = "https://html.duckduckgo.com/html/"
_DDG_LITE = "https://lite.duckduckgo.com/lite/"
_BLOCK_MARKERS = ("anomaly-modal", "unfortunately, bots use duckduckgo", "g-recaptcha", "captcha")


def _is_blocked(status: int, text: str) -> bool:
    if status in (202, 403, 429):
        return True
    low = (text or "")[:30000].lower()
    return any(m in low for m in _BLOCK_MARKERS)


def _parse_ddg_html(text: str) -> list[dict]:
    """Markup of html.duckduckgo.com: one .result block per hit, title link a.result__a."""
    soup = BeautifulSoup(text, "html.parser")
    out = []
    for block in soup.select(".result, .web-result"):
        link = block.select_one("a.result__a")
        if link is None:
            continue
        snip = block.select_one(".result__snippet")
        out.append({"title": link.get_text(" ", strip=True), "href": link.get("href", ""),
                    "snippet": snip.get_text(" ", strip=True) if snip else ""})
    return out


def _parse_ddg_lite(text: str) -> list[dict]:
    """Markup of lite.duckduckgo.com: a.result-link in a row, snippet in the next row's .result-snippet."""
    soup = BeautifulSoup(text, "html.parser")
    out = []
    for link in soup.select("a.result-link"):
        snippet = ""
        row = link.find_parent("tr")
        nxt = row.find_next_sibling("tr") if row is not None else None
        if nxt is not None:
            sn = nxt.select_one(".result-snippet")
            snippet = sn.get_text(" ", strip=True) if sn else ""
        out.append({"title": link.get_text(" ", strip=True), "href": link.get("href", ""), "snippet": snippet})
    return out


def _normalize_hits(raw: list[dict], limit: int) -> list[dict]:
    results, seen = [], set()
    for r in raw:
        href = _ddg_unwrap(r.get("href", "") or "")
        title = (r.get("title") or "").strip()
        if not title or not href.startswith(("http://", "https://")) or href in seen:
            continue
        seen.add(href)
        results.append({"title": title, "url": href, "snippet": (r.get("snippet") or "")[:300]})
        if len(results) >= limit:
            break
    return results


def web_search(query, limit=5, timeout=8.0):
    """DuckDuckGo search: html endpoint first, lite endpoint as fallback.

    Returns a list (possibly empty when the engine answered and had no hits).
    Raises ToolError only when no backend gave a usable answer, with the reason of each."""
    query = (query or "").strip()
    if not query:
        raise ToolError("web_search: пустой запрос")
    limit = max(1, min(int(limit), 10))
    session = _http_session()
    backends = (("html", _DDG_HTML, _parse_ddg_html), ("lite", _DDG_LITE, _parse_ddg_lite))
    problems, answered = [], False
    for name, url, parser in backends:
        try:
            resp = session.post(url, data={"q": query, "kl": "wt-wt"}, timeout=timeout,
                                headers={"Referer": "https://duckduckgo.com/"})
        except requests.RequestException as e:
            problems.append(f"{name}: сеть ({type(e).__name__})")
            continue
        if _is_blocked(resp.status_code, resp.text):
            problems.append(f"{name}: HTTP {resp.status_code}, блокировка или капча")
            continue
        if resp.status_code != 200:
            problems.append(f"{name}: HTTP {resp.status_code}")
            continue
        answered = True
        results = _normalize_hits(parser(resp.text), limit)
        if results:
            return results
        problems.append(f"{name}: пустая выдача")
    if answered:
        return []
    raise ToolError("web_search недоступен: " + "; ".join(problems))


_SECRET_REF = re.compile(r"\{\{secret:([A-Za-z0-9_.-]{1,64})\}\}")
_SECRETS = None        # SecretStore, set by app.create_app via set_secret_store()
_BRAIN_SESSION = None  # one cookie-keeping session for the brain persona: log in once, stay logged in


def set_secret_store(store) -> None:
    global _SECRETS
    _SECRETS = store


def _brain_session():
    global _BRAIN_SESSION
    if _BRAIN_SESSION is None:
        _BRAIN_SESSION = _http_session()
    return _BRAIN_SESSION


def _tool_http_request_impl(args, brain: bool = False):
    method = str(args.get("method", "GET")).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ToolError(f"Unsupported HTTP method: {method}")
    stored = _SECRETS.values() if (brain and _SECRETS is not None) else {}
    used: set[str] = set()

    def fill(value):
        """Insert saved secrets into strings (recursively). Only the brain persona may use them."""
        if isinstance(value, str):
            def repl(m):
                name = m.group(1)
                if not brain:
                    raise ToolError("секреты доступны только основной персоне")
                if name not in stored:
                    raise ToolError(f"секрет {name!r} не задан в настройках")
                used.add(name)
                return stored[name]
            return _SECRET_REF.sub(repl, value)
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value

    def redact(text: str) -> str:
        """Mask every secret value that was inserted into this request."""
        for name in used:
            text = text.replace(stored[name], "***")
        return text

    url = fill(str(args.get("url", "")).strip())
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ToolError(f"http_request invalid URL: {redact(url)!r}")

    timeout = max(1, min(float(args.get("timeout", 20)), 120))
    max_chars = max(1, min(int(args.get("max_chars", 200_000)), 1_000_000))
    headers = {str(k): fill(str(v)) for k, v in (args.get("headers") or {}).items()}
    params = fill(args.get("params") or {})
    json_body = fill(args.get("json", None))
    raw_body = fill(args.get("body", None))
    if json_body is not None and raw_body is not None:
        raise ToolError("Use either 'json' or 'body', not both")

    try:
        response = _request_checked(
            method, url,
            headers=headers, params=params, json=json_body, data=raw_body, timeout=timeout,
            allow_private=brain, session=_brain_session() if brain else None,
        )
    except requests.RequestException as e:
        raise ToolError(redact(f"http_request network error: {e}")) from e

    body = redact(response.text)
    text = body[:max_chars]
    try:
        payload = json.loads(body)
        response_kind = "json"
    except ValueError:
        payload = text
        response_kind = "text"

    return {
        "method": method,
        "url": redact(url),
        "final_url": redact(response.url),
        "status": response.status_code,
        "ok": response.ok,
        "headers": {k: redact(v) for k, v in response.headers.items()},
        "content_type": response.headers.get("Content-Type", ""),
        "response_kind": response_kind,
        "result": payload,
        "truncated": len(body) > max_chars,
        "session": "kept between calls" if brain else "none",
        "used_secrets": sorted(used),
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
    except ValueError:
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


def _assert_public_url(url: str, allow_private: bool = False) -> None:
    """Refuse URLs whose host resolves to a loopback, private, link-local or otherwise non-global address.
    allow_private is used only for the brain persona's http_request (its own machines and LAN)."""
    host = urllib.parse.urlparse(url).hostname or ""
    if not host:
        raise ToolError("web_fetch: нет имени хоста")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ToolError(f"web_fetch: не удалось найти хост {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ToolError("web_fetch: адрес во внутренней сети запрещён")


def _get_checked(session, url: str, timeout: float, max_redirects: int = 5):
    """GET with manual redirects: every hop is checked against _assert_public_url."""
    for _ in range(max_redirects + 1):
        _assert_public_url(url)
        resp = session.get(url, timeout=timeout, allow_redirects=False, stream=True)
        if resp.is_redirect and resp.headers.get("Location"):
            url = urllib.parse.urljoin(url, resp.headers["Location"])
            resp.close()
            continue
        return resp
    raise ToolError("web_fetch: слишком много перенаправлений")


def _request_checked(method: str, url: str, *, timeout: float, max_redirects: int = 5,
                     allow_private: bool = False, session=None, **kw):
    """Any HTTP method, manual redirects, every hop checked against _assert_public_url."""
    session = session or _http_session()
    for _ in range(max_redirects + 1):
        _assert_public_url(url, allow_private)
        resp = session.request(method, url, timeout=timeout, allow_redirects=False, **kw)
        if resp.is_redirect and resp.headers.get("Location"):
            url = urllib.parse.urljoin(url, resp.headers["Location"])
            if resp.status_code in (301, 302, 303) and method != "GET":
                method = "GET"
                kw.pop("json", None)
                kw.pop("data", None)
            kw.pop("params", None)  # query parameters apply to the first request only
            resp.close()
            continue
        return resp
    raise ToolError("http_request: слишком много перенаправлений")


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
        with _get_checked(_http_session(), request_url, timeout=timeout) as resp:
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


class WebToolsMixin:
    """Web search, page fetch and HTTP request tools."""

    def _tool_http_request(self, args, persona):
        brain = getattr(persona, "persona_id", None) == BRAIN_PERSONA_ID
        try:
            result = _tool_http_request_impl(args, brain=brain)
        except ToolError as e:
            return {"tool_failed": True, "error": str(e), "tool": "http_request"}
        return result

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
