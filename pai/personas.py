"""Persona registry and self-generated rules manager.

Personas are NOT part of the memory tree (per spec section 13): they are an
overlay of Category A core-addon text, a filtered subset of active Category B
skills, a memory filter, tool permissions and a budget profile. Switching
personas never forks the archive.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .models import (
    MemoryFilter,
    Persona,
    RuleScope,
    RuleStatus,
    SelfGeneratedRule,
    now_ts,
)
from .prompts import load_prompt

# Process-only personas used by background jobs (see telegram_agent.py). Not listed in the UI.
INTERNAL_PERSONA_IDS = frozenset({"telegram_group"})

BRAIN_DEFAULT_TOOLS = [
    "get_fragment", "search_archive", "calculate_time_delta",
    "list_fragments_by_timeframe", "web_search", "web_fetch",
    "http_request", "shell_exec",
]


class PersonaRegistry:
    def __init__(self, path: str | Path | None = None):
        self._personas: dict[str, Persona] = {}
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self.load()

    def register(self, persona: Persona) -> None:
        self._personas[persona.persona_id] = persona
        if self.path:
            self.save()

    def ephemeral(self, persona: Persona) -> None:
        """Register a persona for this process only: it is never written to personas.json."""
        self._personas[persona.persona_id] = persona

    def get(self, persona_id: str) -> Persona | None:
        return self._personas.get(persona_id)

    def all(self) -> list[Persona]:
        return list(self._personas.values())

    def save(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [p.to_dict() for p in self._personas.values()]
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _recover(self, text: str, err: json.JSONDecodeError) -> list:
        """Damaged file (e.g. two JSON documents glued together): keep the first complete
        array, save the whole damaged file as a backup, and start instead of crashing."""
        try:
            data, _end = json.JSONDecoder().raw_decode(text.lstrip())
        except json.JSONDecodeError:
            raise err from None
        if not isinstance(data, list):
            raise err
        backup = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
        backup.write_text(text, encoding="utf-8")
        logging.getLogger(__name__).warning(
            "personas file was damaged (%s); loaded the first document, original saved to %s", err, backup)
        return data

    def remove(self, persona_id: str) -> None:
        self._personas.pop(persona_id, None)
        if self.path:
            self.save()

    def load(self) -> None:
        assert self.path is not None
        text = self.path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            data = self._recover(text, e)
        known = set(Persona.__dataclass_fields__)
        self._personas = {
            d["persona_id"]: Persona.from_dict({k: v for k, v in d.items() if k in known})
            for d in data
        }
        # One-shot migrations for the built-in personas: ship new defaults to
        # an existing personas.json exactly once, without overriding later
        # edits (e.g. a tool the user deliberately removed from "brain").
        changed = False
        for pid, addon in BUILTIN_ADDONS.items():
            bp = self._personas.get(pid)
            if bp is not None and "builtin_addon_v1" not in bp.migrations:
                if not (bp.system_core_addon or "").strip():
                    bp.system_core_addon = addon
                bp.migrations.append("builtin_addon_v1")
                bp.updated_at = now_ts()
                changed = True
        guest = self._personas.get("guest")
        if guest is not None and "guest_space_v2" not in guest.migrations:
            guest.memory_filter = MemoryFilter(allow_spaces=["guest", "shared"], deny_tags=["diary"])
            guest.migrations.append("guest_space_v2")
            guest.updated_at = now_ts()
            changed = True
        brain = self._personas.get("brain")
        if brain is not None and "brain_default_tools_v1" not in brain.migrations:
            brain.tool_permissions = list(dict.fromkeys((brain.tool_permissions or []) + BRAIN_DEFAULT_TOOLS))
            brain.migrations.append("brain_default_tools_v1")
            brain.updated_at = now_ts()
            changed = True
        if brain is not None and "brain_persona_tools_v1" not in brain.migrations:
            line = ("- Персоны: list_personas и get_persona показывают настройки; create_persona и "
                    "edit_persona меняют их только по прямой просьбе владельца.")
            brain.system_core_addon = (brain.system_core_addon or "").rstrip() + "\n" + line
            brain.migrations.append("brain_persona_tools_v1")
            brain.updated_at = now_ts()
            changed = True
        for np_ in _builtin_new_personas():
            if np_.persona_id not in self._personas:
                self._personas[np_.persona_id] = np_
                changed = True
        if changed:
            self.save()


def _builtin_new_personas() -> list[Persona]:
    """Added to every install that does not have them yet (see PersonaRegistry.load)."""
    return [
        Persona(
            persona_id="assistant",
            name="Помощник",
            description=(
                "Стандартный помощник: отвечает по делу, сначала ответ, потом детали. Видит всю память, "
                "ищет в вебе. Без shell и HTTP."
            ),
            system_core_addon=load_prompt("addons/assistant"),
            active_skill_ids=[],
            memory_filter=MemoryFilter(),
            tool_permissions=[
                "get_fragment", "search_archive", "calculate_time_delta", "web_search", "web_fetch",
            ],
            tone="neutral, clear",
        ),
        Persona(
            persona_id="guest",
            name="Гость",
            description=(
                "Публичная персона для посетителей сайта. Видит общее пространство guest и shared (diary скрыт), "
                "ищет в вебе, читает изображения. Без personal, shell, HTTP и изменения правил."
            ),
            system_core_addon=load_prompt("addons/guest"),
            active_skill_ids=[],
            memory_filter=MemoryFilter(allow_spaces=["guest", "shared"], deny_tags=["diary"]),
            tool_permissions=["get_fragment", "search_archive", "web_search", "web_fetch"],
            tone="friendly, brief",
        ),
    ]


BUILTIN_ADDONS: dict[str, str] = {
    "brain": (
        load_prompt("addons/brain")
    ),
    "work": (
        load_prompt("addons/work")
    ),
    "pm": (
        load_prompt("addons/pm")
    ),
    "friend": (
        load_prompt("addons/friend")
    ),
    "tutor": (
        load_prompt("addons/tutor")
    ),
}


def default_personas() -> PersonaRegistry:
    reg = PersonaRegistry()

    reg.register(Persona(
        persona_id="brain",
        name="Second Brain",
        description=(
            "Точный поиск по всей памяти без ограничений по пространствам. Нейтральный тон, "
            "ответы со ссылками на источники (фрагмент памяти / веб), без домыслов. "
            "Есть веб, HTTP и shell."
        ),
        system_core_addon=BUILTIN_ADDONS["brain"],
        active_skill_ids=[],
        memory_filter=MemoryFilter(),  # empty = all spaces allowed
        tone="neutral, precise",
        tool_permissions=list(BRAIN_DEFAULT_TOOLS),
    ))

    reg.register(Persona(
        persona_id="work",
        name="Work Assistant",
        description=(
            "Деловой ассистент: коротко, по делу, результат вперёд. Видит только пространства "
            "work и shared (заметки с тегом diary скрыты). Планы дня, протоколы встреч, "
            "готовые черновики писем."
        ),
        system_core_addon=BUILTIN_ADDONS["work"],
        active_skill_ids=["daily_plan", "meeting_notes"],
        memory_filter=MemoryFilter(allow_spaces=["work", "shared"], deny_tags=["diary"]),
        tone="business, concise",
    ))

    reg.register(Persona(
        persona_id="pm",
        name="Project Manager",
        description=(
            "Проактивно ведёт договорённости, решения, задачи и дедлайны: сам вытаскивает их из "
            "сообщений, напоминает о просроченном, требует владельца и срок. Видит work и shared."
        ),
        system_core_addon=BUILTIN_ADDONS["pm"],
        active_skill_ids=["daily_plan", "meeting_notes"],
        memory_filter=MemoryFilter(allow_spaces=["work", "shared"]),
        tone="proactive, accountable",
    ))

    reg.register(Persona(
        persona_id="friend",
        name="Friend",
        description=(
            "Тёплый собеседник: сначала слушает, говорит живым языком без списков и канцелярита, "
            "честен без лести. Видит personal, health и shared; чувствительное сам не поднимает."
        ),
        system_core_addon=BUILTIN_ADDONS["friend"],
        active_skill_ids=[],
        memory_filter=MemoryFilter(allow_spaces=["personal", "health", "shared"]),
        tone="warm, empathetic",
    ))

    reg.register(Persona(
        persona_id="tutor",
        name="Tutor",
        description=(
            "Сократический репетитор: определяет уровень, ведёт вопросами и подсказками по "
            "нарастающей, по одной теме за раз, помнит прогресс и слабые места. Видит study и shared."
        ),
        system_core_addon=BUILTIN_ADDONS["tutor"],
        active_skill_ids=[],
        memory_filter=MemoryFilter(allow_spaces=["study", "shared"]),
        tone="socratic, patient",
    ))

    for p in _builtin_new_personas():
        reg.register(p)
    return reg


class RulesManager:
    """Manages the draft -> validated -> active lifecycle for self-generated
    rules and resolves which rules apply to a given persona/space context."""

    def __init__(self, path: str | Path | None = None):
        self._rules: dict[str, SelfGeneratedRule] = {}
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self.load()

    def add(self, rule: SelfGeneratedRule) -> SelfGeneratedRule:
        self._rules[rule.id] = rule
        self.save()
        return rule

    def get(self, rule_id: str) -> SelfGeneratedRule | None:
        return self._rules.get(rule_id)

    def all(self) -> list[SelfGeneratedRule]:
        return list(self._rules.values())

    def validate(self, rule_id: str) -> SelfGeneratedRule:
        rule = self._require(rule_id)
        rule.promote(RuleStatus.VALIDATED)
        self.save()
        return rule

    def activate(self, rule_id: str) -> SelfGeneratedRule:
        rule = self._require(rule_id)
        rule.promote(RuleStatus.ACTIVE)
        self.save()
        return rule

    def reject(self, rule_id: str) -> SelfGeneratedRule:
        rule = self._require(rule_id)
        rule.promote(RuleStatus.REJECTED)
        self.save()
        return rule

    def archive(self, rule_id: str) -> SelfGeneratedRule:
        rule = self._require(rule_id)
        rule.promote(RuleStatus.ARCHIVED)
        self.save()
        return rule

    def remove(self, rule_id: str) -> None:
        self._require(rule_id)
        self._rules.pop(rule_id, None)
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in self._rules.values()]
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> None:
        if not self.path:
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._rules = {d["id"]: SelfGeneratedRule.from_dict(d) for d in data}

    def _require(self, rule_id: str) -> SelfGeneratedRule:
        rule = self._rules.get(rule_id)
        if rule is None:
            raise KeyError(f"No such rule: {rule_id}")
        return rule

    def active_rules_for(
        self, persona_id: str | None = None, space_id: str | None = None
    ) -> list[SelfGeneratedRule]:
        """Only ACTIVE rules apply to behavior. Global rules always apply;
        persona/space-scoped rules apply only within matching context."""
        result = []
        for rule in self._rules.values():
            if rule.status != RuleStatus.ACTIVE:
                continue
            if rule.scope == RuleScope.GLOBAL:
                result.append(rule)
            elif rule.scope == RuleScope.PERSONA and rule.persona_id == persona_id:
                result.append(rule)
            elif rule.scope == RuleScope.SPACE and rule.space_id == space_id:
                result.append(rule)
            elif rule.scope == RuleScope.PROJECT and rule.space_id == space_id:
                result.append(rule)
        return result

    def render_active_rules_block(
        self, persona_id: str | None = None, space_id: str | None = None
    ) -> str:
        rules = self.active_rules_for(persona_id, space_id)
        if not rules:
            return ""
        lines = [f"- {r.rule_text}" for r in rules]
        return "Active self-generated rules:\n" + "\n".join(lines)
