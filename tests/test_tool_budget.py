import tempfile

from pai.app import create_app
from pai.llm import MockLLM
from pai.models import NodeType
from pai.orchestrator.context import ContextBuilder
from pai.orchestrator.tool_pool import ToolPoolPlanner


def _app(max_rounds: int = 7):
    app = create_app(data_dir=tempfile.mkdtemp(), llm=MockLLM())
    app.state.orchestrator.max_tool_iterations = max_rounds
    return app


def test_planner_tells_the_model_its_budget_for_this_answer_only():
    app = _app(max_rounds=7)
    planner = ToolPoolPlanner(app.state.orchestrator)
    for phase in ("planning", "analysis"):
        msgs = planner._planner_messages([{"role": "system", "content": "SYS"}], [], phase)
        text = msgs[0]["content"]
        assert "at most 7 plan/execute rounds" in text, phase
        assert "{max_rounds}" not in text, phase
        assert "next user message starts with a fresh budget" in text, phase


def test_system_notices_are_not_fed_back_to_the_model():
    app = _app()
    orch = app.state.orchestrator
    orch.tree.ingest_raw(content="Question", space_id="personal", node_type=NodeType.TEXT, tags=["user"])
    orch.tree.ingest_raw(content="Real answer", space_id="personal", node_type=NodeType.TEXT,
                         tags=["assistant"])
    orch.tree.ingest_raw(content="⚠ Ответ не прошёл проверку", space_id="personal", node_type=NodeType.TEXT,
                         tags=["assistant", "notice"])
    tail = ContextBuilder(orch)._tail_nodes("personal")
    contents = [n.raw_content for n in tail]
    assert "Real answer" in contents
    assert all("notice" not in (n.tags or []) for n in tail)
    assert not any("Ответ не прошёл проверку" in c for c in contents)
