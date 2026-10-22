"""Web search, page fetch and HTTP request tools."""

from __future__ import annotations

import base64
import html
import ipaddress
import json
import re
import socket
import time
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
    """Transport for http_request / web_fetch: curl_cffi with a Chrome TLS fingerprint.

    impersonate="chrome" already sets a consistent User-Agent + sec-ch-ua pair, so the UA is
    not overridden by hand (a hand-written UA would contradict the Client Hints headers).
    requests is used only when curl_cffi is not installed."""
    if cffi_requests is None:
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
    session = cffi_requests.Session(impersonate="chrome")
    session.headers.update({"Accept-Language": "en-US,en;q=0.8"})
    return session


def _bs4_text(node):
    """Convert HTML to readable text using BeautifulSoup4."""
    for bad in node.select("script, style, noscript, template"):
        bad.decompose()
    return "\n".join(line.strip() for line in node.get_text("\n", strip=True).splitlines() if line.strip())


# ── транспорт поиска: curl_cffi ───────────────────────────────────────────────
# Termux: готовое колесо curl_cffi-0.16.3-...-android_24_arm64_v8a.whl, сборка не нужна.
try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # мягкий фолбэк: модуль импортируется, ошибка — только при вызове web_search
    cffi_requests = None

_NET_ERRORS: tuple = (OSError,)
try:  # у curl_cffi свой базовый класс ошибок
    from curl_cffi.requests.errors import RequestsError as _CffiError

    _NET_ERRORS = (_CffiError, OSError)
except Exception:  # pragma: no cover - зависит от версии curl_cffi
    pass
# requests остаётся только как аварийный фолбэк транспорта, но его ошибки тоже ловим
_NET_ERRORS = _NET_ERRORS + (requests.RequestException,)

_DDG_HTML = "https://html.duckduckgo.com/html/"
_DDG_LITE = "https://lite.duckduckgo.com/lite/"
_BING = "https://www.bing.com/search"
_MWMBL = "https://api.mwmbl.org/api/v1/search/"
# «captcha» убран из маркеров: ложно срабатывал на запросах вида "captcha bypass"
_BLOCK_MARKERS = (
    "anomaly-modal",
    "unfortunately, bots use duckduckgo",
    "g-recaptcha",
    "are you not a robot",
    "verifying your browser",
)


def _is_blocked(status: int, text: str) -> bool:
    if status in (202, 403, 429):
        return True
    low = (text or "")[:30000].lower()
    return any(m in low for m in _BLOCK_MARKERS)


def _session(impersonate=None):
    """curl_cffi-сессия. impersonate включается точечно: DDG с 'chrome' отдаёт 202,
    без него — 200 и 10 результатов (проверено с этого телефона)."""
    if cffi_requests is None:
        raise ToolError("web_search: curl_cffi не установлен (pip install curl_cffi)")
    session = cffi_requests.Session(impersonate=impersonate) if impersonate else cffi_requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.8",
        }
    )
    return session


def _region_for(query: str):
    """Кириллица в запросе → ru, иначе en. Раньше kl был захардкожен 'wt-wt'."""
    if re.search(r"[а-яёА-ЯЁ]", query):
        return "ru-ru", "ru-RU,ru;q=0.9,en;q=0.8"
    return "wt-wt", "en-US,en;q=0.8"


def _bing_unwrap(url: str) -> str:
    """bing.com/ck/a?...&u=a1<base64url> → реальный URL (иначе все ссылки — редиректы)."""
    if "bing.com/ck/a" not in url:
        return url
    u = (urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("u") or [""])[0]
    if u.startswith("a1"):
        u = u[2:]
    try:
        return urllib.parse.unquote(base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode("utf-8", "replace"))
    except Exception:
        return url


def _parse_ddg_html(text: str) -> list[dict]:
    """Markup of html.duckduckgo.com: one .result block per hit, title link a.result__a."""
    soup = BeautifulSoup(text, "html.parser")
    out = []
    for block in soup.select(".result, .web-result"):
        link = block.select_one("a.result__a")
        if link is None:
            continue
        snip = block.select_one(".result__snippet")
        out.append(
            {
                "title": link.get_text(" ", strip=True),
                "href": link.get("href", ""),
                "snippet": snip.get_text(" ", strip=True) if snip else "",
            }
        )
    return out


def _parse_bing_html(text: str) -> list[dict]:
    """Markup of www.bing.com/search: li.b_algo → h2 a + .b_caption p."""
    soup = BeautifulSoup(text, "html.parser")
    out = []
    for li in soup.select("li.b_algo"):
        link = li.select_one("h2 a")
        if link is None:
            continue
        p = li.select_one(".b_caption p") or li.select_one("p")
        out.append(
            {
                "title": link.get_text(" ", strip=True),
                "href": link.get("href", ""),
                "snippet": p.get_text(" ", strip=True) if p else "",
            }
        )
    return out


def _parse_ddg_lite(text: str) -> list[dict]:
    """Markup of lite.duckduckgo.com: a.result-link in a row, snippet in the same or next row.
    Терпимый парсер: фолбэк-селектор td a[href^=http] на случай смены вёрстки."""
    soup = BeautifulSoup(text, "html.parser")
    out = []
    links = soup.select("a.result-link") or soup.select("td a[href^=http]")
    for link in links:
        snippet = ""
        row = link.find_parent("tr")
        if row is not None:
            sn = row.select_one(".result-snippet")
            if sn is None:
                nxt = row.find_next_sibling("tr")
                if nxt is not None:
                    sn = nxt.select_one(".result-snippet")
            if sn is not None:
                snippet = sn.get_text(" ", strip=True)
        out.append({"title": link.get_text(" ", strip=True), "href": link.get("href", ""), "snippet": snippet})
    return out


def _mwmbl_text(value) -> str:
    """Mwmbl отдаёт title/extract как список фрагментов [{"value": ..., "is_bold": bool}],
    а не строку. Собираем фрагменты в одну строку, иначе .strip() в _normalize_hits падает."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for chunk in value:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                parts.append(str(chunk.get("value") or ""))
        return "".join(parts).strip()
    if isinstance(value, dict):
        return str(value.get("value") or "").strip()
    return "" if value is None else str(value).strip()


def _parse_mwmbl(payload) -> list[dict]:
    """api.mwmbl.org → JSON, без ключа. Индекс маленький: на узких запросах часто пусто."""
    items = payload.get("results") if isinstance(payload, dict) else payload
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "title": _mwmbl_text(it.get("title")),
                "href": _mwmbl_text(it.get("url")),
                "snippet": _mwmbl_text(it.get("extract")) or _mwmbl_text(it.get("description")),
            }
        )
    return out


def _normalize_hits(raw: list[dict], limit: int) -> list[dict]:
    results, seen = [], set()
    for r in raw:
        href = _ddg_unwrap(r.get("href", "") or "")
        if "bing.com" in href:
            href = _bing_unwrap(href)
        title = (r.get("title") or "").strip()
        if not title or not href.startswith(("http://", "https://")) or href in seen:
            continue
        seen.add(href)
        results.append({"title": title, "url": href, "snippet": (r.get("snippet") or "")[:300]})
        if len(results) >= limit:
            break
    return results


_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_BRAVE_KEY = ""  # set by configure_web_search(); empty means Brave is not used


def configure_web_search(brave_api_key: str = "") -> None:
    """Called once at app start. A Brave Search API key makes Brave the primary provider;
    DuckDuckGo scraping stays as the fallback (it is blocked by captcha from many servers)."""
    global _BRAVE_KEY
    _BRAVE_KEY = (brave_api_key or "").strip()


def _brave_search(query, limit, timeout, session):
    """Official Brave Search API. Returns hits, raises ToolError on any failure."""
    try:
        resp = session.get(
            _BRAVE_URL,
            params={"q": query, "count": limit, "safesearch": "moderate"},
            headers={"Accept": "application/json", "X-Subscription-Token": _BRAVE_KEY},
            timeout=timeout,
        )
    except _NET_ERRORS as e:
        raise ToolError(f"brave: сеть ({type(e).__name__})") from e
    if resp.status_code != 200:
        raise ToolError(f"brave: HTTP {resp.status_code}")
    try:
        items = (resp.json().get("web") or {}).get("results") or []
    except ValueError as e:
        raise ToolError("brave: некорректный JSON") from e
    raw = [
        {"title": it.get("title", ""), "href": it.get("url", ""), "snippet": it.get("description", "")}
        for it in items
    ]
    return _normalize_hits(raw, limit)


def web_search(query, limit=5, timeout=8.0, total_timeout=20.0):
    """Search the web: Brave API (when a key is set) → DDG html → Bing html → DDG Lite → Mwmbl.

    Every request gets min(timeout, remaining of total_timeout), so the worst case is bounded
    by total_timeout instead of the sum of per-provider timeouts.
    Returns a list (possibly empty when a provider answered and had no hits).
    Raises ToolError only when no provider gave a usable answer, with the reason of each."""
    query = (query or "").strip()
    if not query:
        raise ToolError("web_search: пустой запрос")
    try:  # None/строка больше не дают TypeError
        limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        limit = 5

    kl, accept_lang = _region_for(query)
    deadline = time.monotonic() + float(total_timeout)

    def left() -> float:
        return max(1.0, min(timeout, deadline - time.monotonic()))

    problems, answered = [], False
    if _BRAVE_KEY:  # 1. официальный API — только при ключе
        session = _session()
        try:
            results = _brave_search(query, limit, left(), session)
            if results:
                return results
            problems.append("brave: пустая выдача")
            answered = True
        except ToolError as e:
            problems.append(str(e))
        finally:
            session.close()

    chain = (  # (имя, метод, url, параметры, impersonate, парсер, это_json)
        ("ddg-html", "POST", _DDG_HTML, {"q": query, "kl": kl}, None, _parse_ddg_html, False),
        ("ddg-lite", "POST", _DDG_LITE, {"q": query, "kl": kl}, None, _parse_ddg_lite, False),
        ("mwmbl", "GET", _MWMBL, {"s": query}, None, _parse_mwmbl, True),
        # bing: последним. С мобильного IP отдаёт нерелевантную выдачу (гео-локаль),
        # mkt=en-US и cc=us возвращают 0 результатов, ensearch=1 — мусор. Оставляем фолбэком.
        (
            "bing",
            "GET",
            _BING,
            {"q": query, "count": limit, "mkt": "ru-RU" if kl == "ru-ru" else "en-US"},
            "chrome",
            _parse_bing_html,
            False,
        ),
    )
    for name, method, url, params, imp, parser, is_json in chain:
        session = _session(imp)
        try:
            if method == "POST":
                resp = session.post(
                    url,
                    data=params,
                    timeout=left(),
                    headers={"Referer": "https://duckduckgo.com/", "Accept-Language": accept_lang},
                )
            else:
                resp = session.get(url, params=params, timeout=left(), headers={"Accept-Language": accept_lang})
            status, text = resp.status_code, resp.text
        except _NET_ERRORS as e:  # сеть/таймаут — просто идём дальше
            problems.append(f"{name}: сеть ({type(e).__name__})")
            continue
        finally:
            session.close()
        if _is_blocked(status, text):
            problems.append(f"{name}: HTTP {status}, блокировка или капча")
            continue
        if status != 200:
            problems.append(f"{name}: HTTP {status}")
            continue
        try:
            raw = parser(json.loads(text) if is_json else text)
        except Exception as e:  # битая вёрстка или JSON не роняют каскад
            problems.append(f"{name}: разбор ({type(e).__name__})")
            continue
        answered = True  # провайдер ответил — «пусто» это валидный итог
        hits = _normalize_hits(raw, limit)
        if hits:
            return hits
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
    except _NET_ERRORS as e:
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

    resp = _get_checked(_http_session(), request_url, timeout=timeout)
    try:  # curl_cffi Response не поддерживает with, поэтому закрываем вручную
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
    except _NET_ERRORS as e:
        raise ToolError(f"web_fetch network error: {e}") from e
    finally:
        resp.close()

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
