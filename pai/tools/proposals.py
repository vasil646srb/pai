"""Tools that propose new custom functions and self-generated rules."""

from __future__ import annotations

from .errors import ToolError


class ProposalToolsMixin:
    """Tools that propose new custom functions and self-generated rules."""

    def _tool_propose_function(self, args, persona):
        if not self.custom_functions:
            raise ToolError("Custom function registry is not configured")
        try:
            from .custom_functions import CustomFunction

            # LLM proposals are always saved as a draft. Even if the
            # implementation doesn't yet pass strict validation (e.g. it
            # includes inline_code), the owner must see it on the
            # "Functions" page and decide what to do.
            fn = self.custom_functions.add(
                CustomFunction(
                    name=args["name"],
                    description=args.get("description", ""),
                    parameters=args.get("parameters", {"type": "object", "properties": {}}),
                    kind=args.get("kind", "llm_prompt"),
                    implementation=args.get("implementation", {}),
                ),
                validate=False,
            )
        except (KeyError, ValueError) as e:
            raise ToolError(f"Cannot create function proposal: {e}") from e
        validation_error = None
        try:
            self.custom_functions._validate(fn)
        except ValueError as e:
            validation_error = str(e)

        return {
            "created": True,
            "id": fn.id,
            "name": fn.name,
            "status": fn.status,
            "valid": validation_error is None,
            "validation_error": validation_error,
            "message": (
                "Function saved as a draft. It is not active and cannot be called until "
                'the owner confirms it on the "Functions" page.'
                if validation_error is None
                else f"Function saved as a draft, but needs fixing before it can be confirmed: {validation_error}"
            ),
        }

    def _tool_propose_rule(self, args, persona):
        if not self.rules_manager:
            raise ToolError("Rules manager is not configured")
        from .models import RuleScope, RuleStatus, SelfGeneratedRule

        rule_text = str(args.get("rule_text", "")).strip()
        if not rule_text:
            raise ToolError("propose_rule requires a non-empty rule_text")

        scope_raw = str(args.get("scope", "global") or "global").strip().lower()
        try:
            scope = RuleScope(scope_raw)
        except ValueError:
            raise ToolError(f"Unsupported rule scope: {scope_raw}") from None

        persona_id = args.get("persona_id")
        # Auto-attach: a rule with scope='persona' proposed without an
        # explicit persona_id is automatically attached to the currently
        # active persona (the one the dialogue is being conducted as). This
        # lets the model propose rules "for itself" without knowing the
        # exact persona_id in advance.
        if scope == RuleScope.PERSONA and not persona_id:
            if persona is None:
                raise ToolError("scope='persona' requires an active persona in this context")
            persona_id = persona.persona_id

        space_id = args.get("space_id")
        if scope in (RuleScope.SPACE, RuleScope.PROJECT) and not space_id:
            raise ToolError(f"scope='{scope.value}' requires space_id")

        rule = self.rules_manager.add(
            SelfGeneratedRule(
                rule_text=rule_text,
                scope=scope,
                persona_id=persona_id,
                space_id=space_id,
                trigger_context=str(args.get("trigger_context", "") or ""),
                confidence=float(args.get("confidence", 0.5) or 0.5),
                derived_from=list(args.get("derived_from") or []),
                status=RuleStatus.DRAFT,
            )
        )

        return {
            "created": True,
            "id": rule.id,
            "scope": rule.scope.value,
            "persona_id": rule.persona_id,
            "space_id": rule.space_id,
            "status": rule.status.value,
            "message": (
                "Rule saved as a draft. It has no effect and does not influence "
                'behavior until the owner confirms it on the "Rules" page.'
            ),
        }
