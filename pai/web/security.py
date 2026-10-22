"""Response security headers for the web UI and API.

The Content-Security-Policy stops injected markup from loading scripts from
other origins or running inline. The UI needs no inline script or inline event
handler, so `script-src` is 'self' only. `style-src` keeps 'unsafe-inline'
because the UI sets inline style attributes.

FastAPI's Swagger/ReDoc pages load their assets from a CDN, so they are exempt.
"""
from __future__ import annotations

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "media-src 'self' blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

EXEMPT_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def security_headers(path: str) -> dict[str, str]:
    headers = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"}
    if not path.startswith(EXEMPT_PREFIXES):
        headers["Content-Security-Policy"] = CSP
    return headers


def install_security_headers(app) -> None:
    @app.middleware("http")
    async def security_headers_middleware(request, call_next):
        response = await call_next(request)
        for name, value in security_headers(request.url.path).items():
            response.headers.setdefault(name, value)
        return response
