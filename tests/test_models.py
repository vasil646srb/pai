import pytest

from pai.models import (
    BudgetProfile, MemoryFilter, MemoryNode, NodeStatus, NodeType, Persona,
    RuleScope, RuleStatus, SelfGeneratedRule, TemporalAnnotation,
)


def test_memory_node_roundtrip_json():
    node = MemoryNode(
        space_id="work", level=0, type=NodeType.TEXT, summary_text="hi", raw_content="hello world",
        tags=["a", "b"], annotations=[TemporalAnnotation("вчера", "2026-08-29", "high")],
    )
    s = node.to_json()
    restored = MemoryNode.from_json(s)
    assert restored.id == node.id
    assert restored.space_id == "work"
    assert restored.type == NodeType.TEXT
    assert restored.status == NodeStatus.ACTIVE
    assert restored.tags == ["a", "b"]
    assert restored.annotations[0].resolved_date == "2026-08-29"


def test_memory_filter_permits_defaults_allow_all():
    f = MemoryFilter()
    assert f.permits("anything", []) is True
    assert f.permits("personal", ["diary"]) is True


def test_memory_filter_allow_spaces_restricts():
    f = MemoryFilter(allow_spaces=["work", "shared"])
    assert f.permits("work", []) is True
    assert f.permits("personal", []) is False


def test_memory_filter_deny_spaces_wins():
    f = MemoryFilter(allow_spaces=["work", "personal"], deny_spaces=["personal"])
    assert f.permits("work", []) is True
    assert f.permits("personal", []) is False


def test_memory_filter_tags():
    f = MemoryFilter(allow_tags=["project_x"], deny_tags=["diary"])
    assert f.permits("work", ["project_x"]) is True
    assert f.permits("work", ["diary"]) is False
    assert f.permits("work", ["other"]) is False  # allow_tags set but not matched


def test_budget_profile_free_space():
    bp = BudgetProfile(
        system_core=100, persona_header=0, current_time=0, skill_triggers=0,
        tree_tops=0, tail=0, retrieved_fragments=0, reasoning_reserve=0, total_budget=1000,
    )
    assert bp.allocated() == 100
    assert bp.free_space() == 900


def test_budget_profile_free_space_never_negative():
    bp = BudgetProfile(total_budget=10, system_core=1000)
    assert bp.free_space() == 0


def test_persona_roundtrip():
    p = Persona(persona_id="work", name="Work", memory_filter=MemoryFilter(allow_spaces=["work"]))
    d = p.to_dict()
    restored = Persona.from_dict(d)
    assert restored.persona_id == "work"
    assert restored.memory_filter.allow_spaces == ["work"]
    assert isinstance(restored.budget_profile, BudgetProfile)


def test_rule_lifecycle_happy_path():
    rule = SelfGeneratedRule(rule_text="test", scope=RuleScope.GLOBAL)
    assert rule.status == RuleStatus.DRAFT
    rule.promote(RuleStatus.VALIDATED)
    assert rule.status == RuleStatus.VALIDATED
    assert rule.validated_at is not None
    rule.promote(RuleStatus.ACTIVE)
    assert rule.status == RuleStatus.ACTIVE


def test_rule_lifecycle_cannot_skip_stage():
    rule = SelfGeneratedRule(rule_text="test")
    with pytest.raises(ValueError):
        rule.promote(RuleStatus.ACTIVE)  # must go through VALIDATED first


def test_rule_can_be_rejected_from_any_state():
    rule = SelfGeneratedRule(rule_text="test")
    rule.promote(RuleStatus.REJECTED)
    assert rule.status == RuleStatus.REJECTED

    rule2 = SelfGeneratedRule(rule_text="test2")
    rule2.promote(RuleStatus.VALIDATED)
    rule2.promote(RuleStatus.ARCHIVED)
    assert rule2.status == RuleStatus.ARCHIVED


def test_rule_roundtrip():
    rule = SelfGeneratedRule(rule_text="x", scope=RuleScope.SPACE, space_id="work")
    d = rule.to_dict()
    restored = SelfGeneratedRule.from_dict(d)
    assert restored.scope == RuleScope.SPACE
    assert restored.space_id == "work"
