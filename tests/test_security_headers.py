"""CSP and static-page guards: the UI must keep working under script-src 'self'."""
import re
from pathlib import Path

from pai.web.security import CSP, EXEMPT_PREFIXES, security_headers

STATIC = Path(__file__).resolve().parent.parent / "pai" / "static"
INLINE_SCRIPT = re.compile(r"<script>(?!\s*</script>)", re.S)
SCRIPT_SRC = re.compile(r'<script src="/static/([^"?]+)')


def _directive(name: str) -> str:
    for part in CSP.split(";"):
        part = part.strip()
        if part.startswith(name + " "):
            return part
    raise AssertionError(f"no {name} directive")


def test_script_src_is_self_only():
    assert _directive("script-src") == "script-src 'self'"


def test_csp_blocks_foreign_objects_and_framing():
    assert "object-src 'none'" in CSP
    assert "frame-ancestors 'none'" in CSP


def test_pages_are_served_with_csp():
    headers = security_headers("/")
    assert headers["Content-Security-Policy"] == CSP
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_api_docs_are_exempt_from_csp():
    for prefix in EXEMPT_PREFIXES:
        assert "Content-Security-Policy" not in security_headers(prefix)


def test_static_pages_contain_no_inline_script():
    for html in STATIC.glob("*.html"):
        if html.name.endswith(".bak"):
            continue
        found = INLINE_SCRIPT.findall(html.read_text(encoding="utf-8"))
        assert not found, f"{html.name} has inline <script> blocks"


def test_every_referenced_script_exists():
    for html in STATIC.glob("*.html"):
        for name in SCRIPT_SRC.findall(html.read_text(encoding="utf-8")):
            assert (STATIC / name).is_file(), f"{html.name} references missing {name}"


def test_index_build_placeholders_are_stamped():
    from pai.web import ui

    html = ui._with_build_versions((STATIC / "index.html").read_text(encoding="utf-8"))
    assert "__BUILD__" not in html
    assert re.search(r'/static/app/\d+-[\w-]+\.js\?v=\d+', html)
