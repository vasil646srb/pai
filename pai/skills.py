"""Modular instruction system. Not part of the memory tree."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import now_ts
from .prompts import load_prompt

DEFAULT_CORE_RULES = load_prompt("core_rules")


@dataclass
class Skill:
    skill_id: str
    trigger_description: str
    full_instruction_text: str
    version: int = 1
    last_updated: float = field(default_factory=now_ts)


class SkillCatalog:
    def __init__(self, path: str | Path | None = None):
        self._skills: dict[str, Skill] = {}
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self.load()

    def register(self, skill: Skill) -> None:
        self._skills[skill.skill_id] = skill
        if self.path:
            self.save()

    def remove(self, skill_id: str) -> None:
        self._skills.pop(skill_id, None)
        if self.path:
            self.save()

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def save(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(s) for s in self._skills.values()]
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> None:
        assert self.path is not None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._skills = {d["skill_id"]: Skill(**d) for d in data}

    def triggers_block(self, skill_ids: list[str]) -> str:
        lines = []
        for sid in skill_ids:
            sk = self._skills.get(sid)
            if sk:
                lines.append(f"- [{sk.skill_id}] {sk.trigger_description}")
        return "\n".join(lines)

    def fetch_full_text(self, skill_id: str) -> str | None:
        sk = self._skills.get(skill_id)
        return sk.full_instruction_text if sk else None


def default_skill_catalog(path: str | Path | None = None) -> SkillCatalog:
    cat = SkillCatalog(path)
    if cat._skills:
        # loaded from disk — don't clobber user-saved custom skills with defaults
        return cat
    cat.register(Skill(
        skill_id="daily_plan",
        trigger_description="Use when the user asks to prepare a plan for the day.",
        full_instruction_text=(
            "When building a daily plan: first show open commitments and "
            "deadlines from memory (space=work), then propose time blocks. "
            "Ask about priorities if there are more than 5 tasks."
        ),
    ))
    cat.register(Skill(
        skill_id="meeting_notes",
        trigger_description="Use when the user sends notes from a meeting.",
        full_instruction_text=(
            "Structure meeting notes as: participants, decisions, open "
            "questions, next steps with owners. Save as a decision/task node."
        ),
    ))
    return cat
