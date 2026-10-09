"""create_persona / edit_persona: Second Brain only."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.custom_functions import CustomFunctionRegistry
from pai.personas import default_personas
from pai.skills import default_skill_catalog
from pai.storage import Storage
from pai.tools import ToolDispatcher


def _setup():
    d = tempfile.mkdtemp()
    reg = default_personas()
    reg.path = Path(d) / "personas.json"
    disp = ToolDispatcher(Storage(Path(d) / "pai.db"), custom_functions=CustomFunctionRegistry(),
                          skills=default_skill_catalog(), persona_registry=reg)
    return disp, reg


def _names(disp, persona):
    return {s["function"]["name"] for s in disp.schemas_for_persona(persona)}


def test_schemas_only_for_brain():
    disp, reg = _setup()
    assert {"create_persona", "edit_persona"} <= _names(disp, reg.get("brain"))
    for pid in ("work", "pm", "friend", "tutor"):
        assert not ({"create_persona", "edit_persona"} & _names(disp, reg.get(pid)))
    # granting through tool_permissions does not help
    work = reg.get("work")
    work.tool_permissions += ["create_persona", "edit_persona"]
    assert not ({"create_persona", "edit_persona"} & _names(disp, work))


def test_other_persona_cannot_call():
    disp, reg = _setup()
    work = reg.get("work")
    work.tool_permissions += ["create_persona"]
    r = disp.dispatch("create_persona", {"persona_id": "x1", "name": "X"}, work)
    assert r.get("tool_failed") and reg.get("x1") is None


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
    # persisted
    assert "coach" in (reg.path.read_text(encoding="utf-8"))


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
        ("edit_persona", {"persona_id": "brain", "tone": "x"}),
        ("edit_persona", {"persona_id": "ghost", "tone": "x"}),
        ("edit_persona", {"persona_id": "work"}),
    ]
    for name, args in bad:
        assert disp.dispatch(name, args, brain).get("tool_failed"), (name, args)
    # cannot grant a tool the brain itself lacks
    brain.tool_permissions = ["get_fragment"]
    r = disp.dispatch("create_persona", {"persona_id": "n5", "name": "x",
                                         "tool_permissions": ["shell_exec"]}, brain)
    assert r.get("tool_failed") and reg.get("n5") is None
