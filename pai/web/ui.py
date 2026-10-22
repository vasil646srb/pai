"""Static UI mount: the SPA shell at / and the no-cache asset handler."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class NoCacheStaticFiles(StaticFiles):
    """Serve static assets directly on every request.

    Browser/proxy caching is intentionally disabled for this development UI.
    We also bypass Starlette's conditional ETag/Last-Modified handling so a
    request cannot be answered with HTTP 304 Not Modified. This guarantees
    that the browser receives the current file bytes from the server.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):
        from starlette.responses import FileResponse

        response = FileResponse(
            full_path,
            status_code=status_code,
            stat_result=stat_result,
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


_BUILD_REF = re.compile(r"(/static/[\w./-]+)\?v=__BUILD__")


def _with_build_versions(html: str) -> str:
    """Stamp every `/static/<file>?v=__BUILD__` reference with that file's mtime."""
    def stamp(m: re.Match) -> str:
        try:
            v = int((STATIC_DIR / m.group(1)[len("/static/"):]).stat().st_mtime)
        except OSError:
            v = 0
        return f"{m.group(1)}?v={v}"
    return _BUILD_REF.sub(stamp, html)


def mount_ui(app: FastAPI) -> None:
    """Serve the single-page UI from /static and render index.html at /."""
    if STATIC_DIR.exists():
        app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            html = _with_build_versions(html)
            return Response(
                content=html,
                media_type="text/html",
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )
