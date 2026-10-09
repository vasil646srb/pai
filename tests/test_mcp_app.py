"""MCP through the real app: owner API, persona grants, chat tool loop, /mcp page."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from pai.app import create_app
from pai.llm import MockLLM

STUB = str(Path(__file__).with_name("mcp_stub_server.py"))
SERVER = {"name": "stub", "transport": "stdio", "command": sys.executable, "args": [STUB], "timeout_seconds": 20}


def _app(llm=None):
    return create_app(data_dir=tempfile.mkdtemp(), llm=llm or MockLLM())


def test_owner_api_and_persona_grant():
    app = _app()
    with TestClient(app) as c:
        r = c.post("/mcp/servers", json={**SERVER, "env": {"TOKEN": "s3cret"}})
        assert r.status_code == 201, r.text
        srv = r.json()
        assert srv["runtime"]["status"] == "ready", srv
        assert srv["env"] == {"TOKEN": "***"} and "s3cret" not in r.text
        tools = c.get(f"/mcp/servers/{srv['id']}/tools").json()
        assert {"mcp__stub__echo", "mcp__stub__add"} <= {t["name"] for t in tools}

        # MCP tools appear in the persona tool list and are accepted as grants
        assert "mcp__stub__echo" in {t["name"] for t in c.get("/tools").json()}
        r = c.patch("/personas/work/tools", json={"tool_permissions": ["web_search", "mcp__stub__echo"]})
        assert r.status_code == 200, r.text
        work = next(p for p in c.get("/personas").json() if p["persona_id"] == "work")
        assert "mcp__stub__echo" in work["tool_permissions"]
        r = c.patch("/personas/work/tools", json={"tool_permissions": ["mcp__stub__missing"]})
        assert r.status_code == 422

        # editing keeps the stored secret when the page sends the masked value back
        r = c.patch(f"/mcp/servers/{srv['id']}", json={"env": {"TOKEN": "***"}, "timeout_seconds": 15})
        assert r.status_code == 200 and r.json()["runtime"]["status"] == "ready"
        assert app.state.mcp.registry.get(srv["id"]).env == {"TOKEN": "s3cret"}

        # disable -> tools disappear; delete -> gone
        assert c.patch(f"/mcp/servers/{srv['id']}", json={"enabled": False}).json()["runtime"]["status"] == "disabled"
        assert "mcp__stub__echo" not in {t["name"] for t in c.get("/tools").json()}
        assert c.delete(f"/mcp/servers/{srv['id']}").status_code == 204
        assert c.get("/mcp/servers").json() == []
        assert c.get("/mcp/servers/nope/tools").status_code == 404

        # bad config is rejected with 422, not a crash
        assert c.post("/mcp/servers", json={"name": "Bad Name", "command": "x"}).status_code == 422


def test_chat_turn_calls_granted_mcp_tool_only():
    llm = MockLLM()
    llm.queue_response(tool_calls=[{"id": "1", "name": "plan_tool_pool", "arguments": {
        "calls": [{"name": "mcp__stub__echo", "arguments": {"text": "pong"}}]}}])
    llm.queue_response(content="done")
    llm.queue_response(tool_calls=[{"id": "2", "name": "plan_tool_pool", "arguments": {
        "calls": [{"name": "mcp__stub__echo", "arguments": {"text": "again"}}]}}])
    llm.queue_response(content="done2")
    app = _app(llm)
    with TestClient(app) as c:
        c.post("/mcp/servers", json=SERVER)

        # not granted yet: the call is not in the persona's schemas, so the pool drops it
        ev = _sse(c, text="echo please", persona_id="work", space_id="work")
        assert ev[-1]["tool_trace"] == [], ev[-1]["tool_trace"]

        c.patch("/personas/work/tools", json={"tool_permissions": ["mcp__stub__echo"]})
        ev = _sse(c, text="echo again", persona_id="work", space_id="work")
        res = ev[-1]["tool_trace"][0]["result"]
        assert res["ok"] and res["output"] == "again", res


def test_mcp_page_is_served():
    with TestClient(_app()) as c:
        r = c.get("/mcp")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "MCP-серверы" in r.text


def _sse(client, **body):
    import json
    events = []
    with client.stream("POST", "/chat/stream", json=body) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    return events
