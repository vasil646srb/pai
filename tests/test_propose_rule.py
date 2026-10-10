from pai.models import RuleScope, RuleStatus
from pai.tools.dispatcher import ToolDispatcher


class _FakeRules:
    def __init__(self):
        self.added = []

    def add(self, rule):
        rule.id = "rule-1"
        self.added.append(rule)
        return rule


def test_propose_rule_creates_draft_rule():
    rules = _FakeRules()
    dispatcher = ToolDispatcher(storage=None, rules_manager=rules)
    result = dispatcher.dispatch(
        "propose_rule", {"rule_text": "Отвечай кратко", "scope": "global"}, persona=None,
    )
    assert result["created"] is True
    assert result["id"] == "rule-1"
    assert len(rules.added) == 1
    assert rules.added[0].scope == RuleScope.GLOBAL
    assert rules.added[0].status == RuleStatus.DRAFT
