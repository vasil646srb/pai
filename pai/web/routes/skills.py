"""Skill catalogue endpoints."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from ...api_schemas import (
    SkillCreate,
    SkillOut,
)
from ...personas import PersonaRegistry
from ...skills import Skill
from ..deps import get_personas


def register(app: FastAPI) -> None:
    @app.get("/skills")
    def list_skills(skills=Depends(lambda: app.state.skills)):
        return [
            {
                "skill_id": s.skill_id,
                "trigger_description": s.trigger_description,
                "full_instruction_text": s.full_instruction_text,
                "version": s.version,
                "last_updated": s.last_updated,
            }
            for s in skills.all()
        ]

    @app.post("/skills", response_model=SkillOut, status_code=201)
    def create_skill(req: SkillCreate, skills=Depends(lambda: app.state.skills)):
        if skills.get(req.skill_id) is not None:
            raise HTTPException(409, f"Skill '{req.skill_id}' already exists")
        skill = Skill(
            skill_id=req.skill_id,
            trigger_description=req.trigger_description,
            full_instruction_text=req.full_instruction_text,
        )
        skills.register(skill)
        return SkillOut(
            skill_id=skill.skill_id, trigger_description=skill.trigger_description,
            full_instruction_text=skill.full_instruction_text, version=skill.version,
            last_updated=skill.last_updated,
        )

    @app.delete("/skills/{skill_id}", status_code=204)
    def delete_skill(
        skill_id: str,
        skills=Depends(lambda: app.state.skills),
        personas: PersonaRegistry = Depends(get_personas),
    ):
        if skills.get(skill_id) is None:
            raise HTTPException(404, f"Skill '{skill_id}' not found")
        skills.remove(skill_id)
        # Detach the skill from any persona that referenced it, so it doesn't
        # linger as a dangling id in active_skill_ids.
        for persona in personas.all():
            if skill_id in persona.active_skill_ids:
                persona.active_skill_ids = [s for s in persona.active_skill_ids if s != skill_id]
                personas.register(persona)
