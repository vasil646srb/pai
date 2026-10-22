"""describe_tools: request parsing, persona-safe payloads, compact catalog."""
from pai.orchestrator.tool_pool import (
    MAX_DESCRIBE_NAMES,
    _purpose,
    describe_payload,
    extract_describe_request,
)


def _schema(name, desc="Does a thing. More detail here."):
    return {"type": "function", "function": {"name": name, "description": desc,
                                              "parameters": {"type": "object", "properties": {}}}}


def test_extract_dedupes_strips_and_keeps_call_id():
    result = {"tool_calls": [{"name": "describe_tools", "id": "c1",
                              "arguments": {"names": ["web_search", "web_search", " telegram_read "]}}]}
    assert extract_describe_request(result) == (["web_search", "telegram_read"], "c1")


def test_extract_accepts_names_as_json_string():
    result = {"tool_calls": [{"name": "describe_tools", "id": "c2", "arguments": {"names": '["a", "b"]'}}]}
    assert extract_describe_request(result) == (["a", "b"], "c2")


def test_plan_in_same_reply_wins_over_describe():
    result = {"tool_calls": [
        {"name": "describe_tools", "id": "d", "arguments": {"names": ["x"]}},
        {"name": "plan_tool_pool", "id": "p", "arguments": {"calls": []}},
    ]}
    assert extract_describe_request(result) is None


def test_no_describe_call_returns_none():
    assert extract_describe_request({"tool_calls": [], "content": "hi"}) is None


def test_names_are_capped():
    names = [f"t{i}" for i in range(MAX_DESCRIBE_NAMES + 5)]
    got, _ = extract_describe_request({"tool_calls": [{"name": "describe_tools", "id": "c",
                                                        "arguments": {"names": names}}]})
    assert len(got) == MAX_DESCRIBE_NAMES


def test_payload_describes_only_persona_tools():
    schemas = [_schema("web_search")]
    payload = describe_payload(["web_search", "shell_exec"], schemas)
    assert [t["name"] for t in payload["tools"]] == ["web_search"]
    assert payload["not_available"] == ["shell_exec"]
    assert "parameters" in payload["tools"][0]


def test_purpose_is_first_sentence_and_capped():
    assert _purpose("Search the web. Returns {title, url}.") == "Search the web."
    assert len(_purpose("x" * 500)) == 120


# ---- end-to-end through the dialog loop (fake LLM) ----

import pytest  # noqa: E402

from pai.llm import MockLLM  # noqa: E402
from pai.orchestrator import Orchestrator  # noqa: E402
from pai.personas import default_personas  # noqa: E402
from pai.skills import default_skill_catalog  # noqa: E402
from pai.storage import Storage  # noqa: E402


@pytest.fixture
def orch(tmp_path):
    storage = Storage(tmp_path / "data")
    llm = MockLLM()
    o = Orchestrator(storage, llm, default_personas(), default_skill_catalog(),
                     timezone_name="UTC", verify_enabled=False)
    yield o, llm
    storage.close()


def test_describe_then_plan_does_not_use_a_round(orch):
    o, llm = orch
    llm.queue_response(tool_calls=[{"id": "d1", "name": "describe_tools",
                                    "arguments": {"names": ["calculate_time_delta"]}}])
    llm.queue_response(tool_calls=[{"id": "p1", "name": "plan_tool_pool", "arguments": {"calls": [
        {"name": "calculate_time_delta", "arguments": {"date1": "2026-01-01", "date2": "2026-01-02"}}]}}])
    llm.queue_response(content="Разница один день.")

    result = o.handle_turn("сколько дней между 1 и 2 января 2026", persona_id="brain")

    assert result.reply == "Разница один день."
    assert len(result.tool_trace) == 1
    chat_calls = [c for c in llm.calls if "messages" in c]
    assert len(chat_calls) == 3
    # both planner calls advertise both control tools
    assert [t["function"]["name"] for t in chat_calls[0]["tools"]] == ["plan_tool_pool", "describe_tools"]
    # the catalog in the system prompt is compact: no parameter schema dump
    system = chat_calls[0]["messages"][0]["content"]
    assert "TOOL CATALOG" in system
    assert '"parameters"' not in system.split("TOOL CATALOG", 1)[1]
    # the described schema reached the planner as a tool result before the plan
    tool_msgs = [m for m in chat_calls[1]["messages"] if m.get("role") == "tool"]
    assert any("calculate_time_delta" in m["content"] and "parameters" in m["content"] for m in tool_msgs)
