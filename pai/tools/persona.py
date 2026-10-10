"""Persona CRUD tools (brain-only): list, get, create, edit."""

from __future__ import annotations

from ..models import BudgetProfile, MemoryFilter, Persona, now_ts
from .errors import ToolError
from .schemas import (
    _DEFAULT_NEW_PERSONA_TOOLS,
    _MAX_ADDON_CHARS,
    _PERSONA_ID_RE,
    _PERSONA_LIST_FIELDS,
    _PERSONA_STR_LIMITS,
    BRAIN_ONLY_TOOLS,
    BRAIN_PERSONA_ID,
    MODEL_GRANT_DENYLIST,
    TOOL_SCHEMAS,
)


class PersonaToolsMixin:
    """Persona CRUD tools (brain-only): list, get, create, edit."""

    def _require_brain(self, persona):
        if persona is None or getattr(persona, "persona_id", None) != BRAIN_PERSONA_ID:
            raise ToolError("This tool is available only to the Second Brain persona")
        if self.persona_registry is None:
            raise ToolError("Persona registry is not configured")

    def _persona_known_tools(self):
        known = {s["function"]["name"] for s in TOOL_SCHEMAS} - BRAIN_ONLY_TOOLS
        if self.custom_functions:
            known.update(f.name for f in self.custom_functions.active())
        return known

    def _clean_persona_fields(self, args, caller):
        """Validate the optional persona fields in args; return only those present."""
        out = {}
        for key, limit in _PERSONA_STR_LIMITS.items():
            if args.get(key) is not None:
                val = str(args[key]).strip()
                if key == "name" and not val:
                    raise ToolError("name must not be empty")
                if len(val) > limit:
                    raise ToolError(f"{key} is too long (max {limit} characters)")
                out[key] = val
        if args.get("system_core_addon") is not None:
            val = str(args["system_core_addon"]).strip()
            if len(val) > _MAX_ADDON_CHARS:
                raise ToolError(f"system_core_addon is too long (max {_MAX_ADDON_CHARS} characters)")
            out["system_core_addon"] = val
        for key in _PERSONA_LIST_FIELDS:
            if args.get(key) is None:
                continue
            raw = args[key]
            if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
                raise ToolError(f"{key} must be an array of non-empty strings")
            out[key] = list(dict.fromkeys(x.strip() for x in raw))
        if "tool_permissions" in out:
            tools = out["tool_permissions"]
            denied = [t for t in tools if t in MODEL_GRANT_DENYLIST or t in BRAIN_ONLY_TOOLS]
            if denied:
                raise ToolError(f"These tools cannot be granted through this tool: {denied}")
            unknown = [t for t in tools if t not in self._persona_known_tools()]
            if unknown:
                raise ToolError(f"Unknown tool(s): {unknown}. Known: {sorted(self._persona_known_tools())}")
            beyond = [t for t in tools if t not in (caller.tool_permissions or [])]
            if beyond:
                raise ToolError(f"Cannot grant tools you do not have yourself: {beyond}")
        if "active_skill_ids" in out and self.skills is not None:
            unknown = [x for x in out["active_skill_ids"] if self.skills.get(x) is None]
            if unknown:
                raise ToolError(f"Unknown skill id(s): {unknown}")
        return out

    @staticmethod
    def _persona_brief(p):
        mf = p.memory_filter
        return {
            "persona_id": p.persona_id,
            "name": p.name,
            "description": p.description,
            "tone": p.tone,
            "status": p.status,
            "tool_permissions": list(p.tool_permissions),
            "allow_spaces": list(mf.allow_spaces),
            "deny_spaces": list(mf.deny_spaces),
            "allow_tags": list(mf.allow_tags),
            "deny_tags": list(mf.deny_tags),
        }

    @staticmethod
    def _persona_full(p):
        return {
            **PersonaToolsMixin._persona_brief(p),
            "system_core_addon": p.system_core_addon,
            "active_skill_ids": list(p.active_skill_ids),
        }

    def _tool_list_personas(self, args, persona):
        self._require_brain(persona)
        return {"personas": [self._persona_brief(p) for p in self.persona_registry.all()]}

    def _tool_get_persona(self, args, persona):
        self._require_brain(persona)
        pid = str(args.get("persona_id", "")).strip()
        target = self.persona_registry.get(pid)
        if target is None:
            return {"found": False, "persona_id": pid}
        return {"found": True, "persona": self._persona_full(target)}

    def _tool_create_persona(self, args, persona):
        self._require_brain(persona)
        persona_id = str(args.get("persona_id", "")).strip()
        if not _PERSONA_ID_RE.fullmatch(persona_id):
            raise ToolError("persona_id must match [a-z][a-z0-9_]{1,31}")
        if self.persona_registry.get(persona_id) is not None:
            raise ToolError(f"Persona '{persona_id}' already exists; use edit_persona")
        f = self._clean_persona_fields(args, persona)
        if not f.get("name"):
            raise ToolError("name is required")
        new = Persona(
            persona_id=persona_id,
            name=f["name"],
            description=f.get("description", ""),
            system_core_addon=f.get("system_core_addon", ""),
            active_skill_ids=f.get("active_skill_ids", []),
            memory_filter=MemoryFilter(
                allow_spaces=f.get("allow_spaces", []),
                deny_spaces=f.get("deny_spaces", []),
                allow_tags=f.get("allow_tags", []),
                deny_tags=f.get("deny_tags", []),
            ),
            tool_permissions=f.get("tool_permissions", list(_DEFAULT_NEW_PERSONA_TOOLS)),
            budget_profile=BudgetProfile(),
            tone=f.get("tone", "neutral"),
        )
        self.persona_registry.register(new)
        return {"created": True, "persona": self._persona_full(new)}

    def _tool_edit_persona(self, args, persona):
        self._require_brain(persona)
        pid = str(args.get("persona_id", "")).strip()
        if pid == BRAIN_PERSONA_ID:
            raise ToolError("The Second Brain persona cannot be edited with this tool")
        target = self.persona_registry.get(pid)
        if target is None:
            raise ToolError(f"Persona '{pid}' not found")
        f = self._clean_persona_fields(args, persona)
        if not f:
            raise ToolError("Nothing to change: pass at least one field besides persona_id")
        for key in ("name", "description", "system_core_addon", "tone", "active_skill_ids", "tool_permissions"):
            if key in f:
                setattr(target, key, f[key])
        for key in ("allow_spaces", "deny_spaces", "allow_tags", "deny_tags"):
            if key in f:
                setattr(target.memory_filter, key, f[key])
        target.updated_at = now_ts()
        self.persona_registry.register(target)
        return {"updated": True, "changed": sorted(f), "persona": self._persona_full(target)}
