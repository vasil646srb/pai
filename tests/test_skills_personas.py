import pytest

from pai.models import MemoryFilter, Persona, RuleScope, RuleStatus, SelfGeneratedRule
from pai.personas import PersonaRegistry, RulesManager, default_personas
from pai.skills import Skill, SkillCatalog, default_skill_catalog


def test_skill_catalog_register_and_get():
    cat = SkillCatalog()
    cat.register(Skill("s1", "trigger text", "full instructions"))
    assert cat.get("s1").full_instruction_text == "full instructions"
    assert cat.get("missing") is None


def test_skill_catalog_triggers_block_only_has_triggers_not_full_text():
    cat = default_skill_catalog()
    block = cat.triggers_block(["daily_plan"])
    assert "plan for the day" in block
    assert "тайм-блоки" not in block  # full instruction text must not leak into triggers


def test_skill_catalog_fetch_full_text():
    cat = default_skill_catalog()
    full = cat.fetch_full_text("daily_plan")
    assert "time blocks" in full


def test_default_personas_have_distinct_filters():
    reg = default_personas()
    work = reg.get("work")
    friend = reg.get("friend")
    assert "work" in work.memory_filter.allow_spaces
    assert "personal" not in work.memory_filter.allow_spaces
    assert "personal" in friend.memory_filter.allow_spaces
    assert "work" not in friend.memory_filter.allow_spaces


def test_persona_registry_save_and_load(tmp_path):
    path = tmp_path / "personas.json"
    reg = PersonaRegistry(path)
    reg.register(Persona(persona_id="x", name="X", memory_filter=MemoryFilter(allow_spaces=["work"])))

    reg2 = PersonaRegistry(path)
    restored = reg2.get("x")
    assert restored is not None
    assert restored.memory_filter.allow_spaces == ["work"]


def test_rules_manager_lifecycle_and_activation():
    mgr = RulesManager()
    rule = mgr.add(SelfGeneratedRule(rule_text="always greet warmly", scope=RuleScope.GLOBAL))
    assert mgr.active_rules_for() == []  # draft, not active yet

    mgr.validate(rule.id)
    assert mgr.active_rules_for() == []  # validated, still not active

    mgr.activate(rule.id)
    active = mgr.active_rules_for()
    assert len(active) == 1
    assert active[0].id == rule.id


def test_rules_manager_persona_scoped_rule_only_applies_to_matching_persona():
    mgr = RulesManager()
    rule = mgr.add(SelfGeneratedRule(
        rule_text="be proactive", scope=RuleScope.PERSONA, persona_id="pm",
    ))
    mgr.validate(rule.id)
    mgr.activate(rule.id)

    assert len(mgr.active_rules_for(persona_id="pm")) == 1
    assert len(mgr.active_rules_for(persona_id="friend")) == 0


def test_rules_manager_space_scoped_rule():
    mgr = RulesManager()
    rule = mgr.add(SelfGeneratedRule(
        rule_text="no medical advice", scope=RuleScope.SPACE, space_id="health",
    ))
    mgr.validate(rule.id)
    mgr.activate(rule.id)

    assert len(mgr.active_rules_for(space_id="health")) == 1
    assert len(mgr.active_rules_for(space_id="work")) == 0


def test_rules_manager_render_block_empty_when_no_active_rules():
    mgr = RulesManager()
    assert mgr.render_active_rules_block() == ""


def test_rules_manager_render_block_lists_active_rules():
    mgr = RulesManager()
    rule = mgr.add(SelfGeneratedRule(rule_text="test rule text", scope=RuleScope.GLOBAL))
    mgr.validate(rule.id)
    mgr.activate(rule.id)
    block = mgr.render_active_rules_block()
    assert "test rule text" in block
