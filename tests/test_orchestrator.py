from datetime import datetime, timezone

import pytest

from pai.llm import LLMError, MockLLM
from pai.orchestrator import MAX_TOOL_ITERATIONS, Orchestrator, _ITERATION_LIMIT_FALLBACK
from pai.tools import ToolDispatcher
from pai.personas import PersonaRegistry, RulesManager, default_personas
from pai.skills import default_skill_catalog
from pai.storage import Storage
from pai.tools import ToolError


def _plan(*calls):
    """Model reply that plans one tool pool (the current protocol), listing the calls."""
    return [{"id": "p", "name": "plan_tool_pool",
             "arguments": {"calls": [{"name": c["name"], "arguments": c["arguments"]} for c in calls]}}]


@pytest.fixture
def orch(tmp_path):
    storage = Storage(tmp_path / "data")
    llm = MockLLM()
    personas = default_personas()
    skills = default_skill_catalog()
    o = Orchestrator(storage, llm, personas, skills, timezone_name="UTC", verify_enabled=False)
    yield o, llm
    storage.close()


def test_handle_turn_simple_reply(orch):
    o, llm = orch
    llm.queue_response(content="Привет! Чем могу помочь?")
    result = o.handle_turn("Привет", persona_id="brain")
    assert result.reply == "Привет! Чем могу помочь?"
    assert result.usage["total_calls"] == 1


def test_handle_turn_unknown_persona_raises(orch):
    o, llm = orch
    with pytest.raises(KeyError):
        o.handle_turn("hi", persona_id="does-not-exist")


def test_handle_turn_denied_space_raises(orch):
    o, llm = orch
    with pytest.raises(ToolError):
        o.handle_turn("hi", persona_id="work", space_id="personal")


def test_handle_turn_stores_user_and_assistant_nodes(orch):
    o, llm = orch
    llm.queue_response(content="ответ")
    o.handle_turn("вопрос пользователя", persona_id="brain", space_id="personal")

    nodes = o.storage.get_by_level(0, space_id="personal")
    contents = [n.raw_content for n in nodes]
    assert "вопрос пользователя" in contents
    assert "ответ" in contents


def test_handle_turn_executes_tool_call_then_replies(orch):
    o, llm = orch
    node = o.tree.ingest_raw("важный факт про проект", space_id="personal")

    llm.queue_response(tool_calls=_plan({"name": "get_fragment", "arguments": {"id": node.id}}))
    llm.queue_response(content="Нашёл: важный факт про проект")

    result = o.handle_turn("что там было про проект?", persona_id="brain", space_id="personal")
    assert result.reply == "Нашёл: важный факт про проект"
    assert len(result.tool_trace) == 1
    assert result.tool_trace[0]["name"] == "get_fragment"
    assert result.tool_trace[0]["result"]["found"] is True




def test_handle_turn_executes_multiple_tools_as_one_pool_before_next_llm_call(orch, monkeypatch):
    """All tool calls emitted by one LLM response run concurrently and the
    next LLM request is made only after every result is available."""
    o, llm = orch
    barrier = __import__("threading").Barrier(2, timeout=2)

    def dispatch_in_parallel(self, name, arguments, persona=None):
        # Both calls must reach this point before either can finish. A
        # sequential implementation would time out here.
        barrier.wait()
        return {"name": name, "value": arguments["value"]}

    monkeypatch.setattr(
        __import__("pai.tools", fromlist=["ToolDispatcher"]).ToolDispatcher,
        "dispatch",
        dispatch_in_parallel,
    )

    llm.queue_response(tool_calls=_plan(
        {"name": "calculate_time_delta", "arguments": {"date1": "2026-01-01", "date2": "now", "value": "a"}},
        {"name": "calculate_time_delta", "arguments": {"date1": "2026-01-02", "date2": "now", "value": "b"}},
    ))
    llm.queue_response(content="Оба результата получены.")

    result = o.handle_turn("выполни два инструмента", persona_id="brain")
    assert result.reply == "Оба результата получены."
    chat_calls = [c for c in llm.calls if "messages" in c]
    assert len(chat_calls) == 2

    assert len(chat_calls) == 2
    assert any(t["function"]["name"] == "plan_tool_pool" for t in chat_calls[0]["tools"])
    analysis_messages = chat_calls[1]["messages"]
    results_text = "".join(str(m["content"]) for m in analysis_messages if m["role"] == "tool")
    assert '"value": "a"' in results_text
    assert '"value": "b"' in results_text

def test_handle_turn_tool_loop_forces_final_answer_after_max_iterations(orch):
    """After MAX_TOOL_ITERATIONS rounds of tool calls with no answer, the
    orchestrator makes one last tools-free call instead of surfacing a raw
    iteration-limit placeholder — the model gets a real chance to
    synthesize an answer from what it already gathered."""
    o, llm = orch
    for i in range(MAX_TOOL_ITERATIONS):
        llm.queue_response(tool_calls=_plan({"name": "calculate_time_delta", "arguments": {"date1": f"2026-01-{i + 1:02d}", "date2": "now"}}))
    llm.queue_response(content="Не удалось найти нужный факт, уточните запрос.")

    result = o.handle_turn("сколько дней с нового года?", persona_id="brain")
    assert result.reply == "Не удалось найти нужный факт, уточните запрос."
    assert len(result.tool_trace) == MAX_TOOL_ITERATIONS
    # the forced final call must not offer tools — the model can't keep looping
    assert next(c for c in reversed(llm.calls) if "messages" in c)["tool_choice"] == "none"


def test_handle_turn_tool_loop_falls_back_if_forced_call_also_empty(orch):
    """If even the forced tools-free call comes back empty (or with more
    tool calls, since a MockLLM queue doesn't enforce tools=None), the user
    still gets a plain, understandable message — never raw placeholder text."""
    o, llm = orch
    for i in range(MAX_TOOL_ITERATIONS + 1):
        llm.queue_response(tool_calls=_plan({"name": "calculate_time_delta", "arguments": {"date1": f"2026-01-{i + 1:02d}", "date2": "now"}}))

    result = o.handle_turn("сколько дней с нового года?", persona_id="brain")
    assert "лимит" not in result.reply.lower()
    assert result.reply == _ITERATION_LIMIT_FALLBACK


def test_handle_turn_tool_loop_falls_back_if_forced_call_errors(tmp_path):
    """If the forced tools-free call itself raises (e.g. backend down),
    the orchestrator still returns a plain fallback message rather than
    propagating the exception to the caller."""
    class FlakyLLM(MockLLM):
        def chat(self, messages, tools=None, **kwargs):
            if kwargs.get("tool_choice") == "none":  # the forced final call
                raise LLMError("backend unreachable")
            return super().chat(messages, tools=tools, **kwargs)

    storage = Storage(tmp_path / "data")
    flaky = FlakyLLM()
    for _ in range(MAX_TOOL_ITERATIONS):
        flaky.queue_response(tool_calls=_plan({"name": "calculate_time_delta", "arguments": {"date1": "2026-01-01", "date2": "now"}}))
    o = Orchestrator(storage, flaky, default_personas(), default_skill_catalog(), timezone_name="UTC")

    result = o.handle_turn("сколько дней с нового года?", persona_id="brain")
    assert result.reply == _ITERATION_LIMIT_FALLBACK
    storage.close()


def test_handle_turn_annotates_relative_dates(orch):
    o, llm = orch
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    llm.queue_response(content="понял")
    o.handle_turn("вчера мы говорили об этом", persona_id="brain", reference_now=ref)

    nodes = o.storage.get_by_level(0, space_id="personal")
    user_node = next(n for n in nodes if "вчера" in n.raw_content)
    assert user_node.annotations[0].resolved_date == "2026-08-29"


def test_build_system_prompt_includes_persona_and_time(orch):
    o, llm = orch
    ref = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    persona = o.personas.get("friend")
    prompt = o.build_system_prompt(persona, "personal", reference_now=ref)
    assert "Friend" in prompt
    assert "2026-08-30" in prompt


def test_background_compression_runs_and_creates_summary(orch):
    o, llm = orch
    for i in range(5):
        llm.queue_response(content=f"ответ {i}")
        o.handle_turn(f"сообщение {i}", persona_id="brain", space_id="personal")

    created = o.run_background_compression(space_id="personal")
    assert len(created) >= 1
    # background summary usage recorded
    assert o.usage.summary()["calls_by_kind"].get("compression", 0) >= 1


def test_persona_tool_permissions_enforced_in_turn(orch):
    o, llm = orch
    persona = o.personas.get("brain")
    persona.tool_permissions = []  # strip all tool access
    llm.queue_response(tool_calls=_plan({"name": "get_fragment", "arguments": {"id": "x"}}))
    llm.queue_response(content="fallback reply")

    result = o.handle_turn("test", persona_id="brain")
    # calls outside the persona's filtered schema are never executed, so nothing is traced
    assert result.tool_trace == []
    assert result.reply == "fallback reply"


def test_handle_turn_stream_forces_final_answer_after_max_iterations(orch):
    """The streaming path hits the same iteration-limit fallback as the
    non-streaming one, via the same forced tools-free call."""
    o, llm = orch
    for i in range(MAX_TOOL_ITERATIONS):
        llm.queue_response(tool_calls=_plan({"name": "calculate_time_delta", "arguments": {"date1": f"2026-01-{i + 1:02d}", "date2": "now"}}))
    llm.queue_response(content="Готовый ответ после форс-завершения.")

    events = list(o.handle_turn_stream("сколько дней с нового года?", persona_id="brain"))
    done = next(e for e in events if e["type"] == "done")
    assert done["reply"] == "Готовый ответ после форс-завершения."
    assert next(c for c in reversed(llm.calls) if "messages" in c)["tool_choice"] == "none"


def test_handle_turn_returns_reasoning_when_provided(orch):
    o, llm = orch
    llm.queue_response(content="Финальный ответ", reasoning="Сначала подумаем шаг за шагом...")
    result = o.handle_turn("Реши задачу", persona_id="brain")
    assert result.reply == "Финальный ответ"
    assert result.reasoning == "Сначала подумаем шаг за шагом..."


def test_handle_turn_reasoning_none_when_not_provided(orch):
    o, llm = orch
    llm.queue_response(content="Просто ответ")
    result = o.handle_turn("Привет", persona_id="brain")
    assert result.reasoning is None


def test_handle_turn_with_images_builds_content_blocks(orch):
    o, llm = orch
    llm.queue_response(content="Вижу на фото кота.")
    data_url = "data:image/png;base64,AAAA"
    result = o.handle_turn("Что на фото?", persona_id="brain", images=[data_url])
    assert result.reply == "Вижу на фото кота."
    sent_messages = next(c for c in reversed(llm.calls) if "messages" in c)["messages"]
    user_msg = next(m for m in sent_messages if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0] == {"type": "text", "text": "Что на фото?"}
    assert user_msg["content"][1] == {"type": "image_url", "image_url": {"url": data_url}}


def test_handle_turn_stream_yields_reasoning_delta(orch):
    o, llm = orch
    llm.queue_response(content="Готовый ответ", reasoning="цепочка мыслей")
    events = list(o.handle_turn_stream("Привет", persona_id="brain"))
    reasoning_events = [e for e in events if e["type"] == "reasoning_delta"]
    assert reasoning_events
    assert "".join(e["text"] for e in reasoning_events).strip() == "цепочка мыслей"
    done = next(e for e in events if e["type"] == "done")
    assert done["reasoning"] == "цепочка мыслей"
    assert done["reply"] == "Готовый ответ"


def test_tool_planner_creates_pool_then_analysis_call(orch, monkeypatch):
    o, llm = orch
    started = []
    barrier = __import__("threading").Barrier(2, timeout=2)

    def dispatch(self, name, arguments, persona=None):
        started.append(arguments["value"])
        barrier.wait()
        return {"value": arguments["value"]}

    monkeypatch.setattr(
        __import__("pai.tools", fromlist=["ToolDispatcher"]).ToolDispatcher,
        "dispatch",
        dispatch,
    )

    llm.queue_response(tool_calls=[{
        "id": "planner-1",
        "name": "plan_tool_pool",
        "arguments": {"calls": [
            {"name": "calculate_time_delta", "arguments": {"date1": "2026-01-01", "date2": "2026-01-02", "value": "a"}},
            {"name": "calculate_time_delta", "arguments": {"date1": "2026-02-01", "date2": "2026-02-02", "value": "b"}},
        ]},
    }])
    llm.queue_response(content="Оба результата проверены.")

    result = o.handle_turn("сделай два расчёта", persona_id="brain")
    assert result.reply == "Оба результата проверены."
    assert len(result.tool_trace) == 2
    chat_calls = [c for c in llm.calls if "messages" in c]
    assert len(chat_calls) == 2
    assert chat_calls[0]["tool_choice"] == "auto"
    assert chat_calls[1]["tools"][0]["function"]["name"] == "plan_tool_pool"
    assert sorted(started) == ["a", "b"]


def test_call_written_as_text_is_executed_not_shown(orch):
    """A call written as plain text (name({...})) runs as a tool call and does not
    become the visible answer."""
    o, llm = orch
    llm.queue_response(content='calculate_time_delta({"date1": "2026-01-01", "date2": "now"})')
    llm.queue_response(content="Прошло несколько месяцев.")
    result = o.handle_turn("сколько дней с нового года?", persona_id="brain")
    assert [t["name"] for t in result.tool_trace] == ["calculate_time_delta"]
    assert result.reply == "Прошло несколько месяцев."
