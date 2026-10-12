import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from pai.llm import LLMError, MockLLM, OpenAICompatibleClient


def test_mock_llm_deterministic_fallback():
    llm = MockLLM()
    result = llm.chat(messages=[{"role": "user", "content": "hello there"}])
    assert result["tool_calls"] == []
    assert "hello there" in result["content"]


def test_mock_llm_queued_response():
    llm = MockLLM()
    llm.queue_response(content="canned reply")
    result = llm.chat(messages=[{"role": "user", "content": "x"}])
    assert result["content"] == "canned reply"


def test_mock_llm_queued_tool_call():
    llm = MockLLM()
    llm.queue_response(tool_calls=[{"id": "1", "name": "get_fragment", "arguments": {"id": "abc"}}])
    result = llm.chat(messages=[{"role": "user", "content": "x"}])
    assert result["tool_calls"][0]["name"] == "get_fragment"


def test_mock_llm_summarize_fallback():
    llm = MockLLM()
    summary = llm.summarize("это длинный текст " * 20)
    assert summary.startswith("Summary:")


def test_mock_llm_records_calls():
    llm = MockLLM()
    llm.chat(messages=[{"role": "user", "content": "a"}])
    llm.chat(messages=[{"role": "user", "content": "b"}])
    assert len(llm.calls) == 2


# ---------- OpenAICompatibleClient against a local fake server ----------

class _FakeHandler(BaseHTTPRequestHandler):
    response_body = {
        "choices": [{"message": {"content": "hi from server", "tool_calls": []}}]
    }
    status_code = 200
    last_payload = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        type(self).last_payload = json.loads(body.decode("utf-8"))
        self.send_response(self._FakeHandler_class_status())
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.response_body).encode("utf-8"))

    def _FakeHandler_class_status(self):
        return type(self).status_code

    def log_message(self, format, *args):
        pass  # silence test server logs


@pytest.fixture
def fake_server():
    _FakeHandler.response_body = {
        "choices": [{"message": {"content": "hi from server", "tool_calls": []}}]
    }
    _FakeHandler.status_code = 200
    _FakeHandler.last_payload = None
    server = HTTPServer(("127.0.0.1", 0), _FakeHandler)
    port = server.server_address[1]
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()


def test_openai_compatible_client_basic_chat(fake_server):
    client = OpenAICompatibleClient(base_url=fake_server, model="test-model")
    result = client.chat(messages=[{"role": "user", "content": "hello"}])
    assert result["content"] == "hi from server"
    assert result["tool_calls"] == []


def test_openai_compatible_client_parses_tool_calls(fake_server):
    _FakeHandler.response_body = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "get_fragment", "arguments": json.dumps({"id": "n1"})},
                }],
            }
        }]
    }
    client = OpenAICompatibleClient(base_url=fake_server, model="test-model")
    result = client.chat(messages=[{"role": "user", "content": "hello"}])
    assert result["tool_calls"][0]["name"] == "get_fragment"
    assert result["tool_calls"][0]["arguments"] == {"id": "n1"}


def test_openai_compatible_client_http_error_raises_llmerror(fake_server):
    _FakeHandler.status_code = 500
    _FakeHandler.response_body = {"error": "boom"}
    client = OpenAICompatibleClient(base_url=fake_server, model="test-model")
    with pytest.raises(LLMError):
        client.chat(messages=[{"role": "user", "content": "hello"}])


def test_openai_compatible_client_unreachable_host_raises_llmerror():
    client = OpenAICompatibleClient(base_url="http://127.0.0.1:1", model="x", timeout=1.0)
    with pytest.raises(LLMError):
        client.chat(messages=[{"role": "user", "content": "hello"}])


def test_openai_compatible_client_parses_reasoning(fake_server):
    _FakeHandler.response_body = {
        "choices": [{"message": {"content": "ответ", "tool_calls": [], "reasoning": "думаю вслух"}}]
    }
    client = OpenAICompatibleClient(base_url=fake_server, model="test-model", reasoning_enabled=True)
    result = client.chat(messages=[{"role": "user", "content": "hello"}])
    assert result["content"] == "ответ"
    assert result["reasoning"] == "думаю вслух"


def test_openai_compatible_client_reasoning_none_when_absent(fake_server):
    client = OpenAICompatibleClient(base_url=fake_server, model="test-model")
    result = client.chat(messages=[{"role": "user", "content": "hello"}])
    assert result.get("reasoning") is None


def test_mock_llm_chat_stream_yields_typed_events():
    llm = MockLLM()
    llm.queue_response(content="два слова", reasoning="думаю")
    events = list(llm.chat_stream(messages=[{"role": "user", "content": "x"}]))
    kinds = {e["type"] for e in events}
    assert {"reasoning", "content"} <= kinds
    reasoning_text = "".join(e["text"] for e in events if e["type"] == "reasoning")
    content_text = "".join(e["text"] for e in events if e["type"] == "content")
    assert reasoning_text.strip() == "думаю"
    assert content_text == "два слова"


def test_context_window_clamps_max_tokens_to_remaining_context(fake_server):
    client = OpenAICompatibleClient(
        base_url=fake_server, model="test-model", context_window=1024, max_tokens=80
    )
    messages = [{"role": "user", "content": "x" * 4000}]
    # the output budget is what remains of the window after the client's own input estimate
    expected = 1024 - client._estimate_message_tokens(messages)
    client.chat(messages=messages)
    assert _FakeHandler.last_payload["max_tokens"] == expected


def test_context_window_rejects_input_that_already_exceeds_limit(fake_server):
    client = OpenAICompatibleClient(
        base_url=fake_server, model="test-model", context_window=1024, max_tokens=20
    )
    with pytest.raises(LLMError, match="Context window"):
        client.chat(messages=[{"role": "user", "content": "x" * 4100}])
