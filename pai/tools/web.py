"""Web search, page fetch and HTTP request tools."""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request

import requests
from bs4 import BeautifulSoup

from .errors import ToolError

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
        results.append(
            {
                "title": title,
                "url": href,
                "snippet": snippet[:300],
            }
        )
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


class WebToolsMixin:
    """Web search, page fetch and HTTP request tools."""

    def _tool_http_request(self, args, persona):
        try:
            result = _tool_http_request_impl(args)
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
