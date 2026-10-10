"""Self-generated rule endpoints."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from ...api_schemas import (
    RuleCreate,
    RuleOut,
)
from ...models import (
    RuleScope,
    SelfGeneratedRule,
)
from ...personas import RulesManager
from ..deps import get_rules


def register(app: FastAPI) -> None:
    @app.get("/rules", response_model=list[RuleOut])
    def list_rules(rules: RulesManager = Depends(get_rules)):
        return [
            RuleOut(
                id=r.id, scope=r.scope.value, persona_id=r.persona_id, space_id=r.space_id,
                trigger_context=r.trigger_context, rule_text=r.rule_text, confidence=r.confidence,
                status=r.status.value, created_at=r.created_at, validated_at=r.validated_at,
            )
            for r in rules.all()
        ]

    @app.post("/rules", response_model=RuleOut, status_code=201)
    def create_rule(req: RuleCreate, rules: RulesManager = Depends(get_rules)):
        try:
            scope = RuleScope(req.scope)
        except ValueError:
            raise HTTPException(400, f"Invalid scope: {req.scope}") from None
        rule = SelfGeneratedRule(
            scope=scope, persona_id=req.persona_id, space_id=req.space_id,
            trigger_context=req.trigger_context, rule_text=req.rule_text, confidence=req.confidence,
        )
        rules.add(rule)
        return RuleOut(
            id=rule.id, scope=rule.scope.value, persona_id=rule.persona_id, space_id=rule.space_id,
            trigger_context=rule.trigger_context, rule_text=rule.rule_text, confidence=rule.confidence,
            status=rule.status.value, created_at=rule.created_at, validated_at=rule.validated_at,
        )

    def _rule_out(r: SelfGeneratedRule) -> RuleOut:
        return RuleOut(
            id=r.id, scope=r.scope.value, persona_id=r.persona_id, space_id=r.space_id,
            trigger_context=r.trigger_context, rule_text=r.rule_text, confidence=r.confidence,
            status=r.status.value, created_at=r.created_at, validated_at=r.validated_at,
        )

    @app.post("/rules/{rule_id}/validate", response_model=RuleOut)
    def validate_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            return _rule_out(rules.validate(rule_id))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/rules/{rule_id}/activate", response_model=RuleOut)
    def activate_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            return _rule_out(rules.activate(rule_id))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/rules/{rule_id}/reject", response_model=RuleOut)
    def reject_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            return _rule_out(rules.reject(rule_id))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/rules/{rule_id}/archive", response_model=RuleOut)
    def archive_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            return _rule_out(rules.archive(rule_id))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.delete("/rules/{rule_id}", status_code=204)
    def delete_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            rules.remove(rule_id)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
