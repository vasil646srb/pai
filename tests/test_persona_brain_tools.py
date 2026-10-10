"""Second Brain can inspect and change personas; other personas cannot."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from pai.app import create_app
from pai.custom_functions import CustomFunctionRegistry
from pai.llm import MockLLM
from pai.personas import PersonaRegistry, default_personas
from pai.skills import default_skill_catalog
from pai.tools import ToolDispatcher


def _setup():
    d = tempfile.mkdtemp()
    reg = default_personas()
    reg.path = Path(d) / "personas.json"
    disp = ToolDispatcher(None, custom_functions=CustomFunctionRegistry(),
                          skills=default_skill_catalog(), persona_registry=reg)
    return disp, reg


def _names(disp, persona):
    return {s["function"]["name"] for s in disp.schemas_for_persona(persona)}


PERSONA_TOOLS = {"list_personas", "get_persona", "create_persona", "edit_persona"}


def test_schemas_only_for_brain():
    disp, reg = _setup()
    assert PERSONA_TOOLS <= _names(disp, reg.get("brain"))
    for pid in ("work", "tutor"):
        assert not (PERSONA_TOOLS & _names(disp, reg.get(pid)))
    work = reg.get("work")
    work.tool_permissions += list(PERSONA_TOOLS)          # granting does not help
    assert not (PERSONA_TOOLS & _names(disp, work))


def test_other_persona_cannot_call():
    disp, reg = _setup()
    work = reg.get("work")
    work.tool_permissions += ["create_persona"]
    r = disp.dispatch("create_persona", {"persona_id": "x1", "name": "X"}, work)
    assert r.get("tool_failed") and reg.get("x1") is None
    assert disp.dispatch("list_personas", {}, work).get("tool_failed")


def test_list_and_get_persona():
    disp, reg = _setup()
    brain = reg.get("brain")
    ids = {p["persona_id"] for p in disp.dispatch("list_personas", {}, brain)["personas"]}
    assert {"brain", "work"} <= ids
    got = disp.dispatch("get_persona", {"persona_id": "work"}, brain)
    assert got["found"] and got["persona"]["persona_id"] == "work"
    assert "system_core_addon" in got["persona"]
    assert disp.dispatch("get_persona", {"persona_id": "ghost"}, brain) == {"found": False, "persona_id": "ghost"}


def test_create_and_edit_by_brain():
    disp, reg = _setup()
    brain = reg.get("brain")
    r = disp.dispatch("create_persona", {
        "persona_id": "coach", "name": "Coach", "system_core_addon": "Be a coach.",
        "allow_spaces": ["personal"], "tool_permissions": ["get_fragment", "web_search"],
        "tone": "firm"}, brain)
    assert r.get("created"), r
    p = reg.get("coach")
    assert p.name == "Coach" and p.memory_filter.allow_spaces == ["personal"]
    assert p.tool_permissions == ["get_fragment", "web_search"]

    r = disp.dispatch("edit_persona", {"persona_id": "coach", "tone": "gentle",
                                       "deny_tags": ["diary"]}, brain)
    assert r.get("updated") and r["changed"] == ["deny_tags", "tone"], r
    p = reg.get("coach")
    assert p.tone == "gentle" and p.memory_filter.deny_tags == ["diary"]
    assert p.memory_filter.allow_spaces == ["personal"] and p.system_core_addon == "Be a coach."
    assert "coach" in reg.path.read_text(encoding="utf-8")       # persisted


def test_guardrails():
    disp, reg = _setup()
    brain = reg.get("brain")
    bad = [
        ("create_persona", {"persona_id": "Bad Id", "name": "x"}),
        ("create_persona", {"persona_id": "work", "name": "dup"}),
        ("create_persona", {"persona_id": "n1", "name": "x", "tool_permissions": ["nope"]}),
        ("create_persona", {"persona_id": "n2", "name": "x", "tool_permissions": ["create_persona"]}),
        ("create_persona", {"persona_id": "n3", "name": "x", "active_skill_ids": ["nope"]}),
        ("create_persona", {"persona_id": "n4", "name": "x", "system_core_addon": "a" * 6001}),
        ("create_persona", {"persona_id": "n6", "name": "x", "tool_permissions": ["shell_exec"]}),
        ("edit_persona", {"persona_id": "brain", "tone": "x"}),
        ("edit_persona", {"persona_id": "ghost", "tone": "x"}),
        ("edit_persona", {"persona_id": "work"}),
    ]
    for name, args in bad:
        assert disp.dispatch(name, args, brain).get("tool_failed"), (name, args)
    # cannot grant a tool the brain itself lacks
    brain.tool_permissions = ["get_fragment"]
    r = disp.dispatch("create_persona", {"persona_id": "n5", "name": "x",
                                         "tool_permissions": ["web_search"]}, brain)
    assert r.get("tool_failed") and reg.get("n5") is None


def test_brain_migration_adds_instruction_once():
    d = tempfile.mkdtemp()
    path = Path(d) / "personas.json"
    reg = default_personas()
    reg.path = path
    reg.save()
    # simulate an install from before this feature
    data = __import__("json").loads(path.read_text(encoding="utf-8"))
    for p in data:
        if p["persona_id"] == "brain":
            p["migrations"] = [m for m in p["migrations"] if m != "brain_persona_tools_v1"]
    path.write_text(__import__("json").dumps(data), encoding="utf-8")
    once = PersonaRegistry(path).get("brain").system_core_addon
    assert "list_personas" in once
    again = PersonaRegistry(path).get("brain").system_core_addon
    assert again == once                                  # applied exactly once


def test_brain_chat_lists_personas_via_tool_pool():
    llm = MockLLM()
    llm.queue_response(tool_calls=[{"id": "1", "name": "plan_tool_pool", "arguments": {
        "calls": [{"name": "list_personas", "arguments": {}}]}}])
    llm.queue_response(content="listed")
    c = TestClient(create_app(data_dir=tempfile.mkdtemp(), llm=llm))
    import json
    events = []
    with c.stream("POST", "/chat/stream", json={"text": "какие есть персоны?", "persona_id": "brain"}) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    trace = events[-1]["tool_trace"]
    assert trace and trace[0]["result"]["personas"], trace
    assert "work" in {p["persona_id"] for p in trace[0]["result"]["personas"]}


def test_brain_chat_creates_persona_visible_in_api():
    import json
    llm = MockLLM()
    llm.queue_response(tool_calls=[{"id": "1", "name": "plan_tool_pool", "arguments": {
        "calls": [{"name": "create_persona", "arguments": {
            "persona_id": "coach", "name": "Coach", "tone": "firm",
            "tool_permissions": ["get_fragment", "web_search"]}}]}}])
    llm.queue_response(content="created")
    c = TestClient(create_app(data_dir=tempfile.mkdtemp(), llm=llm))
    with c.stream("POST", "/chat/stream", json={"text": "создай персону coach", "persona_id": "brain"}) as r:
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert events[-1]["tool_trace"][0]["result"]["created"], events[-1]["tool_trace"]
    coach = next(p for p in c.get("/personas").json() if p["persona_id"] == "coach")
    assert coach["name"] == "Coach" and coach["tool_permissions"] == ["get_fragment", "web_search"]
    # brain-only tools are hidden from the owner's permission lists
    assert "create_persona" not in {t["name"] for t in c.get("/tools").json()}


def test_markup_plan_in_text_is_executed_and_hidden():
    import json
    llm = MockLLM()
    llm.queue_response(content=(
        "Проверю файлы.\n<tool_call>plan_tool_pool<arg_key>calls</arg_key>"
        "<arg_value>[{\"name\": \"calculate_time_delta\", \"arguments\": {\"date1\": \"2026-01-01\", "
        "\"date2\": \"2026-01-11\"}, \"id\": \"d\"}]</arg_value></tool_call>"))
    llm.queue_response(content="10 дней")
    c = TestClient(create_app(data_dir=tempfile.mkdtemp(), llm=llm))
    with c.stream("POST", "/chat/stream", json={"text": "сколько дней", "persona_id": "brain"}) as r:
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    done = events[-1]
    assert done["tool_trace"] and done["tool_trace"][0]["result"]["days"] == 10, done["tool_trace"]
    assert "<tool_call>" not in done["reply"] and "<arg_key>" not in done["reply"]


def test_damaged_personas_file_loads_and_is_backed_up():
    import json
    d = Path(tempfile.mkdtemp())
    path = d / "personas.json"
    first = [{"persona_id": "work", "name": "Work", "description": "", "tool_permissions": ["get_fragment"]}]
    path.write_text(json.dumps(first) + "\n" + json.dumps([{"persona_id": "x"}]), encoding="utf-8")
    reg = PersonaRegistry(path)
    assert reg.get("work") is not None and reg.get("x") is None
    backups = list(d.glob("personas.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8").count("[") >= 2


def test_announced_action_without_call_gets_one_retry():
    import json
    llm = MockLLM()
    llm.queue_response(content="Прочитаю файл через терминал.")          # announces, no call
    llm.queue_response(tool_calls=[{"id": "1", "name": "plan_tool_pool", "arguments": {
        "calls": [{"name": "calculate_time_delta", "arguments": {"date1": "2026-01-01", "date2": "2026-01-04"}}]}}])
    llm.queue_response(content="3 дня")
    c = TestClient(create_app(data_dir=tempfile.mkdtemp(), llm=llm))
    with c.stream("POST", "/chat/stream", json={"text": "сколько дней", "persona_id": "brain"}) as r:
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    done = events[-1]
    assert done["tool_trace"] and done["tool_trace"][0]["result"]["days"] == 3, done["tool_trace"]
    assert done["reply"] == "3 дня"


def test_plain_answer_is_not_retried():
    from pai.orchestrator.dialog import _announces_unfinished_action
    assert not _announces_unfinished_action("Ответ: 3 дня.")
    assert _announces_unfinished_action("Прочитаю файл через терминал.")
    assert _announces_unfinished_action("I'll read the file now.")
