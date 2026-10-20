"""Knowledge-source tools: Wikipedia, PubMed (NCBI E-utilities), Wikimedia Commons images.

All three are free public APIs without a key. Results use the same shape as web_search where possible.
Optional env: NCBI_API_KEY (raises PubMed limits), PAI_CONTACT_EMAIL (sent to NCBI as contact).
"""

from __future__ import annotations

import html
import inspect
import os
import re
from urllib.parse import quote

import requests

from .errors import ToolError

_TIMEOUT = 8.0
_LANG = re.compile(r"^[a-z]{2,3}(-[a-z]{2,8})?$")
_TAG = re.compile(r"<[^>]+>")
_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_UA = "pai/1.0 (personal assistant; contact via owner)"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _UA})


def _get_json(url: str, params: dict, what: str) -> dict:
    try:
        resp = _SESSION.get(url, params=params, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise ToolError(f"{what}: сеть ({type(e).__name__})")
    if resp.status_code != 200:
        raise ToolError(f"{what}: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise ToolError(f"{what}: ответ не JSON")
    if isinstance(data, dict) and "error" in data:
        info = data["error"].get("info") if isinstance(data["error"], dict) else data["error"]
        raise ToolError(f"{what}: {info}")
    return data


def _limit(limit) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, 10))


def _text(s) -> str:
    return html.unescape(_TAG.sub("", s or "")).strip()


def _query(q) -> str:
    q = (q or "").strip()
    if not q:
        raise ToolError("пустой запрос")
    return q


def wikipedia_search(query, limit=5, lang="ru") -> list[dict]:
    query = _query(query)
    lang = (lang or "ru").strip().lower()
    if not _LANG.match(lang):
        raise ToolError(f"wikipedia_search: неверный код языка {lang!r}")
    data = _get_json(
        f"https://{lang}.wikipedia.org/w/api.php",
        {"action": "query", "list": "search", "srsearch": query, "srlimit": _limit(limit),
         "srprop": "snippet", "format": "json", "formatversion": 2},
        "wikipedia_search",
    )
    out = []
    for hit in data.get("query", {}).get("search", []):
        title = hit.get("title", "")
        out.append({
            "title": title,
            "url": f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            "snippet": _text(hit.get("snippet")),
        })
    return out


def pubmed_search(query, limit=5) -> list[dict]:
    query = _query(query)
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    common = {"tool": "pai"}
    if os.environ.get("PAI_CONTACT_EMAIL"):
        common["email"] = os.environ["PAI_CONTACT_EMAIL"]
    if os.environ.get("NCBI_API_KEY"):
        common["api_key"] = os.environ["NCBI_API_KEY"]
    n = _limit(limit)
    found = _get_json(base + "esearch.fcgi",
                      {"db": "pubmed", "term": query, "retmax": n, "retmode": "json",
                       "sort": "relevance", **common}, "pubmed_search")
    ids = found.get("esearchresult", {}).get("idlist", [])[:n]
    if not ids:
        return []
    summary = _get_json(base + "esummary.fcgi",
                        {"db": "pubmed", "id": ",".join(ids), "retmode": "json", **common},
                        "pubmed_search").get("result", {})
    out = []
    for uid in ids:
        doc = summary.get(uid)
        if not doc:
            continue
        authors = [a.get("name", "") for a in doc.get("authors", [])]
        who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        meta = " · ".join(x for x in (who, doc.get("source", ""), doc.get("pubdate", "")) if x)
        out.append({
            "title": _text(doc.get("title")),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
            "snippet": meta,
        })
    return out


def image_search(query, limit=5) -> list[dict]:
    query = _query(query)
    n = _limit(limit)
    data = _get_json(
        "https://commons.wikimedia.org/w/api.php",
        {"action": "query", "generator": "search", "gsrsearch": query, "gsrnamespace": 6,
         "gsrlimit": min(n * 2, 20), "prop": "imageinfo",
         "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 800,
         "format": "json", "formatversion": 2},
        "image_search",
    )
    pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
    out = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in _IMAGE_MIMES:
            continue
        meta = info.get("extmetadata", {})
        out.append({
            "title": page.get("title", "").removeprefix("File:"),
            "page_url": info.get("descriptionurl", ""),
            "image_url": info.get("thumburl") or info.get("url", ""),
            "width": info.get("thumbwidth") or info.get("width"),
            "height": info.get("thumbheight") or info.get("height"),
            "license": meta.get("LicenseShortName", {}).get("value", ""),
            "author": _text(meta.get("Artist", {}).get("value", ""))[:200],
        })
        if len(out) >= n:
            break
    return out


class SourcesToolsMixin:
    """Dispatcher handlers: _tool_<name>(args, persona) -> dict, same contract as WebToolsMixin."""

    def _run_source(self, name, args, fn, url_key):
        accepted = inspect.signature(fn).parameters
        kwargs = {k: v for k, v in (args or {}).items() if k in accepted}  # ignore unknown model args
        try:
            results = fn(**kwargs)
        except (ToolError, TypeError, ValueError) as e:
            self.storage.log_retrieval(name, args, [])
            return {"results": [], "error": str(e)}
        self.storage.log_retrieval(name, args, [r.get(url_key, "") for r in results])
        return {"results": results}

    def _tool_wikipedia_search(self, args, persona):
        return self._run_source("wikipedia_search", args, wikipedia_search, "url")

    def _tool_pubmed_search(self, args, persona):
        return self._run_source("pubmed_search", args, pubmed_search, "url")

    def _tool_image_search(self, args, persona):
        return self._run_source("image_search", args, image_search, "page_url")
