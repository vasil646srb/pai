"""Smoke tests: python -m pytest tests  (or: python tests/test_smoke.py)"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from pai.app import create_app
from pai.llm import MockLLM, OpenAICompatibleClient, ThinkSplitter, extract_reasoning, split_think


def _last(llm):
    return [c for c in llm.calls if "messages" in c][-1]


def _first(llm):
    return [c for c in llm.calls if "messages" in c][0]


def make(llm=None):
    return create_app(data_dir=tempfile.mkdtemp(), llm=llm or MockLLM())


def sse(client, **body):
    events = []
    with client.stream("POST", "/chat/stream", json=body) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    return events


def test_reasoning_streams_and_persists():
    llm = MockLLM()
    llm.queue_response(content="Hello there", reasoning="think hard")
    c = TestClient(make(llm))
    ev = sse(c, text="hi", persona_id="brain")
    types = [e["type"] for e in ev]
    assert "reasoning_delta" in types and "delta" in types and types[-1] == "done"
    done = ev[-1]
    assert done["reply"] == "Hello there" and done["reasoning"] == "think hard"
    hist = c.get("/chat/history?space_id=personal&limit=10").json()["messages"]
    assert hist[-1]["role"] == "assistant" and hist[-1]["reasoning"] == "think hard"
    # non-stream endpoint shares the path
    llm.queue_response(content="again", reasoning="r2")
    r = c.post("/chat", json={"text": "x", "persona_id": "brain"}).json()
    assert r["reply"] == "again" and r["reasoning"] == "r2"


def test_user_message_not_duplicated_in_prompt():
    llm = MockLLM()
    c = TestClient(make(llm))
    sse(c, text="UNIQUEMARKER one", persona_id="brain")
    sse(c, text="second", persona_id="brain")
    sys_prompt = _last(llm)["messages"][0]["content"]
    assert sys_prompt.count("User: UNIQUEMARKER") == 1   # previous turn, once, in the tail
    assert "User: second" not in sys_prompt              # current message only as the user turn


def test_tool_loop_and_reasoning_across_phases():
    llm = MockLLM()
    llm.queue_response(content="let me compute", reasoning="phase1", tool_calls=[
        {"id": "1", "name": "plan_tool_pool", "arguments": {"calls": [
            {"name": "calculate_time_delta", "arguments": {"date1": "2026-01-01", "date2": "2026-01-11"}}]}}])
    llm.queue_response(content="10 days", reasoning="phase2")
    c = TestClient(make(llm))
    ev = sse(c, text="how many days", persona_id="brain")
    types = [e["type"] for e in ev]
    assert "tool_call" in types and "tool_result" in types and "delta_reset" in types
    done = ev[-1]
    assert done["reply"] == "10 days"
    assert "phase1" in done["reasoning"] and "phase2" in done["reasoning"]
    assert done["tool_trace"][0]["result"]["days"] == 10


def test_json_string_pool_arguments():
    llm = MockLLM()
    llm.queue_response(tool_calls=[{"id": "1", "name": "plan_tool_pool", "arguments": {
        "calls": json.dumps([{"name": "calculate_time_delta",
                              "arguments": json.dumps({"date1": "2026-01-01", "date2": "2026-01-02"})}])}}])
    llm.queue_response(content="ok")
    c = TestClient(make(llm))
    ev = sse(c, text="x", persona_id="brain")
    assert ev[-1]["tool_trace"][0]["result"]["days"] == 1


def test_personas_api_and_spaces():
    llm = MockLLM()
    c = TestClient(make(llm))
    ps = {p["persona_id"]: p for p in c.get("/personas").json()}
    assert all(p["system_core_addon"] for p in ps.values())
    # work persona is not allowed in "personal" ...
    ev = sse(c, text="hi", persona_id="work", space_id="personal")
    assert ev[-1]["type"] == "error"
    # ... but works in its own space, and its addon reaches the prompt
    ev = sse(c, text="hi", persona_id="work", space_id="work")
    assert ev[-1]["type"] == "done"
    assert "PERSONA: Work Assistant" in _last(llm)["messages"][0]["content"]
    # tools endpoint used to 500 (missing budget_profile)
    r = c.patch("/personas/work/tools", json={"tool_permissions": ["get_fragment"]})
    assert r.status_code == 200 and r.json()["system_core_addon"]


def test_get_skill():
    llm = MockLLM()
    llm.queue_response(tool_calls=[{"id": "1", "name": "plan_tool_pool", "arguments": {
        "calls": [{"name": "get_skill", "arguments": {"skill_id": "daily_plan"}}]}}])
    llm.queue_response(content="done")
    c = TestClient(make(llm))
    ev = sse(c, text="plan my day", persona_id="work", space_id="work")
    res = ev[-1]["tool_trace"][0]["result"]
    assert res["found"] and "daily plan" in res["instructions"]
    assert "get_skill" in _first(llm)["messages"][0]["content"]


def test_text_plan_is_executed_not_shown():
    llm = MockLLM()
    llm.queue_response(content='Tool pool plan: [{"name": "calculate_time_delta", "arguments": '
                               '{"date1": "2026-01-01", "date2": "2026-01-05"}}]')
    llm.queue_response(content="4 days")
    c = TestClient(make(llm))
    ev = sse(c, text="x", persona_id="brain")
    assert ev[-1]["reply"] == "4 days" and ev[-1]["tool_trace"][0]["result"]["days"] == 4
    # history uses the real tool protocol
    second = [c_ for c_ in llm.calls if "messages" in c_][1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"] and second[-1]["role"] == "tool"


def test_think_splitter():
    assert split_think("<think>abc</think>answer") == ("answer", "abc")
    sp = ThinkSplitter()
    out = []
    for ch in "<th" + "ink>re" + "ason</thi" + "nk>fin" + "al":
        out += sp.feed(ch)
    out += sp.flush()
    assert "".join(t for k, t in out if k == "reasoning") == "reason"
    assert "".join(t for k, t in out if k == "content") == "final"
    assert extract_reasoning({"reasoning_content": "x"}) == "x"
    assert extract_reasoning({"reasoning_details": [{"type": "reasoning.text", "text": "y"}]}) == "y"


class _Fake(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        self.server.last_body = body
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"choices": [{"delta": {"reasoning_content": "step one. "}}]},
                {"choices": [{"delta": {"reasoning_content": "step two."}}]},
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                          "completion_tokens_details": {"reasoning_tokens": 3}}},
            ]
            if self.server.mode == "tool":
                chunks = [
                    {"choices": [{"delta": {"reasoning": "need tool"}}]},
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                        "name": "plan_tool_pool", "arguments": '{"calls": [{"name": "calculate_time_delta", '}}]}}]},
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
                        "arguments": '"arguments": {"date1": "2026-01-01", "date2": "2026-01-03"}}]}'}}]},
                        "finish_reason": "tool_calls"}]},
                ]
                self.server.mode = "tool_done"
            elif self.server.mode == "tool_done":
                chunks = [{"choices": [{"delta": {"content": "<think>inline</think>2 days"}}]}]
            for ch in chunks:
                self.wfile.write(f"data: {json.dumps(ch)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            out = json.dumps({"choices": [{"message": {"content": "plain", "reasoning_details": [
                {"type": "reasoning.text", "text": "detail"}]}, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)


def _server(mode="plain"):
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    srv.mode = mode
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_real_client_streaming_and_tools():
    srv = _server("plain")
    cl = OpenAICompatibleClient(f"http://127.0.0.1:{srv.server_port}/v1", model="m", context_window=8192,
                                top_k=40, frequency_penalty=0.2, reasoning_enabled=True)
    r = cl.chat([{"role": "user", "content": "hi"}])
    assert r["content"] == "plain" and r["reasoning"] == "detail"
    assert srv.last_body["top_k"] == 40 and srv.last_body["frequency_penalty"] == 0.2
    assert srv.last_body["reasoning_effort"] == "medium"
    evs = list(cl.chat_stream([{"role": "user", "content": "hi"}]))
    assert "".join(e["text"] for e in evs if e["type"] == "reasoning") == "step one. step two."
    assert "".join(e["text"] for e in evs if e["type"] == "content") == "Hello"
    assert any(e["type"] == "usage" for e in evs)

    srv.mode = "tool"
    app = make(OpenAICompatibleClient(f"http://127.0.0.1:{srv.server_port}/v1", model="m", context_window=8192))
    c = TestClient(app)
    ev = sse(c, text="days?", persona_id="brain")
    done = ev[-1]
    assert done["reply"] == "2 days", done
    assert "need tool" in done["reasoning"] and "inline" in done["reasoning"]
    assert done["tool_trace"][0]["result"]["days"] == 2


def test_settings_change_applies_to_runner():
    c = TestClient(make())
    app = c.app
    r = c.put("/settings/llm", json={"base_url": "http://127.0.0.1:9/v1", "model": "zzz"})
    assert r.status_code == 200
    assert app.state.orchestrator.llm_runner.llm.model == "zzz"   # no stale client


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
