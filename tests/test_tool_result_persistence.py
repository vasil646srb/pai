from pathlib import Path

from pai.models import NodeType, Persona
from pai.orchestrator import Orchestrator
from pai.storage import Storage


def test_tool_result_is_persisted(tmp_path):
    # Проверяем непосредственно механизм, который используется обоими
    # путями выполнения инструментов: обычным и streaming.
    storage = Storage(tmp_path)
    orch = Orchestrator.__new__(Orchestrator)
    orch.storage = storage

    # Минимальный TreeBuilder, которому достаточно storage.
    from pai.tree import TreeBuilder
    orch.tree = TreeBuilder(storage, llm=None)

    from pai.orchestrator.tool_pool import ToolPoolPlanner
    pool = ToolPoolPlanner.__new__(ToolPoolPlanner)
    pool.tree = orch.tree
    node = pool._persist_tool_result(
        "web_fetch",
        {"url": "https://example.test", "mode": "text"},
        {"status": 200, "result": "важные данные"},
        "personal",
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    loaded = storage.get_fragment(node.id, record_retrieval=False)
    assert loaded is not None
    assert loaded.type == NodeType.SKILL
    assert "важные данные" in loaded.raw_content
    assert "web_fetch" in loaded.raw_content
