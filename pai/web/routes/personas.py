"""Persona endpoints."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from ...api_schemas import (
    BudgetProfileUpdate,
    PersonaCreate,
    PersonaOut,
    PersonaToolsUpdate,
    PersonaUpdate,
)
from ...custom_functions import CustomFunctionRegistry
from ...models import (
    BudgetProfile,
    MemoryFilter,
    Persona,
    now_ts,
)
from ...personas import INTERNAL_PERSONA_IDS, PersonaRegistry
from ...tools import BRAIN_ONLY_TOOLS, TOOL_SCHEMAS
from ..deps import get_custom_functions, get_personas


def register(app: FastAPI) -> None:
    def _persona_out(p: Persona) -> PersonaOut:
        return PersonaOut(
            persona_id=p.persona_id, name=p.name, description=p.description,
            system_core_addon=p.system_core_addon,
            active_skill_ids=p.active_skill_ids,
            allow_spaces=p.memory_filter.allow_spaces,
            deny_spaces=p.memory_filter.deny_spaces,
            allow_tags=p.memory_filter.allow_tags,
            deny_tags=p.memory_filter.deny_tags,
            tone=p.tone, tool_permissions=p.tool_permissions,
            budget_profile=BudgetProfileUpdate(**p.budget_profile.to_dict()),
        )

    @app.get("/personas", response_model=list[PersonaOut])
    def list_personas(personas: PersonaRegistry = Depends(get_personas)):
        return [_persona_out(p) for p in personas.all() if p.persona_id not in INTERNAL_PERSONA_IDS]

    @app.post("/personas", response_model=PersonaOut, status_code=201)
    def create_persona(
        req: PersonaCreate,
        personas: PersonaRegistry = Depends(get_personas),
        functions: CustomFunctionRegistry = Depends(get_custom_functions),
    ):
        if personas.get(req.persona_id) is not None:
            raise HTTPException(409, f"Persona '{req.persona_id}' already exists")
        known_tools = {s["function"]["name"] for s in TOOL_SCHEMAS if s["function"]["name"] not in BRAIN_ONLY_TOOLS}
        known_tools.update(f.name for f in functions.active())
        unknown = [t for t in req.tool_permissions if t not in known_tools]
        if unknown:
            raise HTTPException(
                422, f"Unknown tool(s): {', '.join(unknown)}. Known: {sorted(known_tools)}"
            )
        persona = Persona(
            persona_id=req.persona_id,
            name=req.name,
            description=req.description,
            system_core_addon=req.system_core_addon,
            active_skill_ids=req.active_skill_ids,
            memory_filter=MemoryFilter(
                allow_spaces=req.allow_spaces, deny_spaces=req.deny_spaces,
                allow_tags=req.allow_tags, deny_tags=req.deny_tags,
            ),
            tool_permissions=req.tool_permissions,
            budget_profile=BudgetProfile(),
            tone=req.tone,
        )
        personas.register(persona)
        return _persona_out(persona)

    @app.patch("/personas/{persona_id}", response_model=PersonaOut)
    def update_persona(
        persona_id: str,
        req: PersonaUpdate,
        personas: PersonaRegistry = Depends(get_personas),
        functions: CustomFunctionRegistry = Depends(get_custom_functions),
    ):
        persona = personas.get(persona_id)
        if persona is None:
            raise HTTPException(404, f"Persona '{persona_id}' not found")
        if req.tool_permissions is not None:
            known_tools = {s["function"]["name"] for s in TOOL_SCHEMAS if s["function"]["name"] not in BRAIN_ONLY_TOOLS}
            known_tools.update(f.name for f in functions.active())
            unknown = [t for t in req.tool_permissions if t not in known_tools]
            if unknown:
                raise HTTPException(
                    422, f"Unknown tool(s): {', '.join(unknown)}. Known: {sorted(known_tools)}"
                )
            persona.tool_permissions = req.tool_permissions
        if req.name is not None:
            persona.name = req.name
        if req.description is not None:
            persona.description = req.description
        if req.system_core_addon is not None:
            persona.system_core_addon = req.system_core_addon
        if req.active_skill_ids is not None:
            persona.active_skill_ids = req.active_skill_ids
        if req.tone is not None:
            persona.tone = req.tone
        if req.budget_profile is not None:
            for key, value in req.budget_profile.model_dump(exclude_none=True).items():
                setattr(persona.budget_profile, key, value)
        if req.allow_spaces is not None:
            persona.memory_filter.allow_spaces = req.allow_spaces
        if req.deny_spaces is not None:
            persona.memory_filter.deny_spaces = req.deny_spaces
        if req.allow_tags is not None:
            persona.memory_filter.allow_tags = req.allow_tags
        if req.deny_tags is not None:
            persona.memory_filter.deny_tags = req.deny_tags
        persona.updated_at = now_ts()
        personas.register(persona)
        return _persona_out(persona)

    @app.delete("/personas/{persona_id}", status_code=204)
    def delete_persona(persona_id: str, personas: PersonaRegistry = Depends(get_personas)):
        persona = personas.get(persona_id)
        if persona is None:
            raise HTTPException(404, f"Persona '{persona_id}' not found")
        personas.remove(persona_id)

    @app.patch("/personas/{persona_id}/tools", response_model=PersonaOut)
    def update_persona_tools(
        persona_id: str,
        req: PersonaToolsUpdate,
        personas: PersonaRegistry = Depends(get_personas),
        functions: CustomFunctionRegistry = Depends(get_custom_functions),
    ):
        persona = personas.get(persona_id)
        if persona is None:
            raise HTTPException(404, f"Persona '{persona_id}' not found")
        known_tools = {s["function"]["name"] for s in TOOL_SCHEMAS if s["function"]["name"] not in BRAIN_ONLY_TOOLS}
        known_tools.update(f.name for f in functions.active())
        unknown = [t for t in req.tool_permissions if t not in known_tools]
        if unknown:
            raise HTTPException(
                422, f"Unknown tool(s): {', '.join(unknown)}. Known: {sorted(known_tools)}"
            )
        persona.tool_permissions = req.tool_permissions
        persona.updated_at = now_ts()
        personas.register(persona)
        return _persona_out(persona)
