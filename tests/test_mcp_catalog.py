"""MCP catalog: bundled file, validation, remote feeds, endpoint."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from pai.app import create_app
from pai.llm import MockLLM
from pai.mcp_catalog import BUNDLED_PATH, McpCatalog, parse_feed

GOOD = {"id": "git", "title": "Git", "description": "Repo tools", "repo": "https://github.com/x/y",
        "install": {"transport": "stdio", "command": "uvx", "args": ["mcp-server-git"]}}


def test_bundled_catalog_is_valid_and_labelled():
    data = json.loads(BUNDLED_PATH.read_text(encoding="utf-8"))
    entries, errors = parse_feed(data, remote=False)
    assert errors == []
    assert len(entries) >= 5
    assert all(e["reviewed"] is False for e in entries)
    assert any(e["source"] == "official" for e in entries)
    assert all(e["repo"].startswith("https://") for e in entries)


def test_invalid_entries_are_skipped_not_fatal():
    bad = [
        {**GOOD, "id": "Bad Id"},
        {**GOOD, "id": "ok_one", "repo": "http://insecure.example"},
        {**GOOD, "id": "ok_two", "description": ""},
        {**GOOD, "id": "ok_three", "install": {"transport": "ftp", "command": "x"}},
        {**GOOD, "id": "ok_four", "install": {"transport": "stdio", "command": ""}},
        {**GOOD, "id": "ok_five", "install": {"transport": "http", "url": "javascript:alert(1)"}},
        {**GOOD, "id": "ok_six", "source": "trusted"},
        "not an object",
    ]
    entries, errors = parse_feed(bad + [GOOD], remote=False)
    assert [e["id"] for e in entries] == ["git"]
    assert len(errors) == len(bad)


def test_remote_entries_cannot_claim_official():
    entries, _ = parse_feed([{**GOOD, "id": "fake", "source": "official"}], remote=True)
    assert entries[0]["source"] == "community"


def test_duplicate_ids_first_wins():
    entries, _ = parse_feed([GOOD, {**GOOD, "title": "Other"}], remote=False)
    assert len(entries) == 1 and entries[0]["title"] == "Git"


class _Feed(BaseHTTPRequestHandler):
    body = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


def test_remote_feed_is_merged_and_failures_are_reported():
    _Feed.body = json.dumps({"entries": [
        {**GOOD, "id": "remote_git", "source": "official"},
        {**GOOD, "id": "broken", "repo": "nope"},
    ]}).encode()
    srv = HTTPServer(("127.0.0.1", 0), _Feed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/feed.json"
        cat = McpCatalog(feed_urls=[url, "http://127.0.0.1:1/dead.json"])
        out = cat.list()
        ids = {e["id"]: e for e in out["entries"]}
        assert "remote_git" in ids and ids["remote_git"]["source"] == "community"
        assert "git" in ids                                  # bundled entries still there
        assert any("broken" in e for e in out["errors"])     # invalid remote entry reported
        assert any("dead.json" in e for e in out["errors"])  # unreachable feed reported, not raised
    finally:
        srv.shutdown()


def test_endpoint_serves_catalog_without_starting_anything():
    app = create_app(data_dir=tempfile.mkdtemp(), llm=MockLLM())
    with TestClient(app) as c:
        data = c.get("/mcp/catalog").json()
        assert data["entries"] and data["errors"] == []
        assert c.get("/mcp/servers").json() == []             # browsing does not connect anything
