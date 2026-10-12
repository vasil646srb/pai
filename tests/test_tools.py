from datetime import datetime, timezone

import pytest

from pai.models import MemoryFilter, MemoryNode, Persona
from pai.storage import Storage
from pai.tools import ToolDispatcher, ToolError


@pytest.fixture
def storage(tmp_path):
    s = Storage(tmp_path / "data")
    yield s
    s.close()


def test_get_fragment_found(storage):
    node = storage.put_node(MemoryNode(raw_content="точный текст", space_id="work"))
    dispatcher = ToolDispatcher(storage)
    result = dispatcher.dispatch("get_fragment", {"id": node.id})
    assert result["found"] is True
    assert result["raw_content"] == "точный текст"


def test_get_fragment_not_found(storage):
    dispatcher = ToolDispatcher(storage)
    result = dispatcher.dispatch("get_fragment", {"id": "nope"})
    assert result["found"] is False


def test_get_fragment_blocked_by_persona_filter(storage):
    node = storage.put_node(MemoryNode(raw_content="secret", space_id="health"))
    persona = Persona(persona_id="work", name="Work", memory_filter=MemoryFilter(allow_spaces=["work"]))
    dispatcher = ToolDispatcher(storage)
    result = dispatcher.dispatch("get_fragment", {"id": node.id}, persona=persona)
    assert result["found"] is False
    assert result["reason"] == "access_denied_by_persona_filter"


def test_tool_permission_denied_raises(storage):
    persona = Persona(persona_id="p", name="P", tool_permissions=["get_fragment"])
    dispatcher = ToolDispatcher(storage)
    with pytest.raises(ToolError):
        dispatcher.dispatch("search_archive", {"query": "x"}, persona=persona)


def test_unknown_tool_returns_tool_failed(storage):
    # Unknown tools are reported back to the model as a failed call, not raised.
    dispatcher = ToolDispatcher(storage)
    result = dispatcher.dispatch("nonexistent_tool", {})
    assert result["tool_failed"] is True and "nonexistent_tool" in result["error"]


def test_search_archive_via_dispatcher(storage):
    storage.put_node(MemoryNode(raw_content="обсуждение архитектуры системы", space_id="work"))
    dispatcher = ToolDispatcher(storage)
    result = dispatcher.dispatch("search_archive", {"query": "архитектур"})
    assert len(result["results"]) == 1


def test_search_archive_filters_by_persona_spaces(storage):
    storage.put_node(MemoryNode(raw_content="проектная информация тест", space_id="health"))
    persona = Persona(persona_id="work", name="Work", memory_filter=MemoryFilter(allow_spaces=["work"]))
    dispatcher = ToolDispatcher(storage)
    result = dispatcher.dispatch("search_archive", {"query": "проектная"}, persona=persona)
    assert result["results"] == []


def test_calculate_time_delta_via_dispatcher(storage):
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    dispatcher = ToolDispatcher(storage, reference_now=ref)
    result = dispatcher.dispatch("calculate_time_delta", {"date1": "2026-07-15", "date2": "now"})
    assert result["days"] == 46


def test_list_fragments_by_timeframe_via_dispatcher(storage):
    storage.put_node(MemoryNode(raw_content="a", occurred_at=1000.0))
    storage.put_node(MemoryNode(raw_content="b", occurred_at=5000.0))
    dispatcher = ToolDispatcher(storage)
    result = dispatcher.dispatch(
        "list_fragments_by_timeframe",
        {"date_from": "1970-01-01T00:00:00+00:00", "date_to": "1970-01-01T01:30:00+00:00"},
    )
    assert len(result["results"]) == 2


def test_retrieval_is_logged(storage):
    node = storage.put_node(MemoryNode(raw_content="x"))
    dispatcher = ToolDispatcher(storage)
    dispatcher.dispatch("get_fragment", {"id": node.id})
    log = storage.get_retrieval_log()
    assert log[0]["tool"] == "get_fragment"


def test_schemas_for_persona_respects_permissions(storage):
    persona = Persona(persona_id="p", name="P", tool_permissions=["get_fragment"])
    dispatcher = ToolDispatcher(storage)
    schemas = dispatcher.schemas_for_persona(persona)
    names = [s["function"]["name"] for s in schemas]
    assert names == ["get_fragment"]


def test_propose_function_saves_invalid_draft_for_review(storage, tmp_path):
    from pai.custom_functions import CustomFunctionRegistry

    registry = CustomFunctionRegistry(tmp_path / "functions.json")
    dispatcher = ToolDispatcher(storage, custom_functions=registry)
    result = dispatcher.dispatch("propose_function", {
        "name": "create_file",
        "description": "Создание файла",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_path": {"type": "string"},
                "file_name": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["folder_path", "file_name", "content"],
        },
        "kind": "llm_prompt",
        "implementation": {"inline_code": "import os\\nprint('test')"},
    })
    assert result["created"] is True
    assert result["status"] == "draft"
    assert result["valid"] is False
    assert result["validation_error"] == "llm_prompt requires implementation.prompt"
    saved = registry.get(result["id"])
    assert saved is not None
    assert saved.status == "draft"
    assert saved.implementation["inline_code"].startswith("import os")


def test_dynamic_function_proposal_can_be_validated_and_activated(storage, tmp_path):
    from pai.custom_functions import CustomFunctionRegistry

    registry = CustomFunctionRegistry(tmp_path / "functions.json")
    dispatcher = ToolDispatcher(storage, custom_functions=registry)
    result = dispatcher.dispatch("propose_function", {
        "name": "calculate_age",
        "description": "Рассчитывает полные годы человека по году и дате рождения.",
        "parameters": {"type": "object", "properties": {"birth_date": {"type": "string"}}, "required": ["birth_date"]},
        "kind": "dynamic",
        "implementation": {"dynamic_sign": "calculate_age_from_birthdate"},
    })
    assert result["created"] is True
    assert result["valid"] is True
    fid = result["id"]
    assert registry.validate(fid).status == "validated"
    assert registry.activate(fid).status == "active"
