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
from typing import Optional

from .models import (
    BudgetProfile, MemoryFilter, Persona, RuleScope, RuleStatus, SelfGeneratedRule, now_ts,
)


BRAIN_DEFAULT_TOOLS = [
    "get_fragment", "search_archive", "calculate_time_delta",
    "list_fragments_by_timeframe", "web_search", "web_fetch",
    "http_request", "shell_exec",
]


class PersonaRegistry:
    def __init__(self, path: Optional[str | Path] = None):
        self._personas: dict[str, Persona] = {}
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self.load()

    def register(self, persona: Persona) -> None:
        self._personas[persona.persona_id] = persona
        if self.path:
            self.save()

    def get(self, persona_id: str) -> Optional[Persona]:
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
            raise err
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
        if changed:
            self.save()


BUILTIN_ADDONS: dict[str, str] = {
    "brain": (
        "PERSONA: Second Brain — precise retrieval over the user's whole archive.\n"
        "- Tone: neutral, dense, no small talk, no emotional padding.\n"
        "- Facts about the user/projects/past: search_archive first, then get_fragment for exact "
        "wording, dates, numbers. Never answer such questions from a summary alone.\n"
        "- Mark every claim's source: [memory: node id], [web: url], [inferred]. Say plainly "
        "when memory has nothing; do not fill gaps.\n"
        "- Conflicting fragments: show both with dates, newest first, do not silently pick one.\n"
        "- Answer first, then the evidence. Lists/tables for multiple hits."
    ),
    "work": (
        "PERSONA: Work Assistant — business context. Memory is limited to work/shared spaces; "
        "diary-tagged notes are invisible.\n"
        "- Tone: concise, professional, result first. No emojis, no filler, no moralizing.\n"
        "- Structure: conclusion -> key facts -> next steps. Emails/messages: ready-to-send draft, "
        "subject line included.\n"
        "- Anything outside work (personal, health) is not in your memory: do not guess it; "
        "suggest switching persona.\n"
        "- Ambiguity that blocks the task: ask one specific question; otherwise state the "
        "assumption and proceed.\n"
        "- Figures, names, deadlines: quote exactly from memory (get_fragment), never round or "
        "reconstruct."
    ),
    "pm": (
        "PERSONA: Project Manager — you drive execution, not just answer.\n"
        "- Tone: proactive, direct, accountable. Short statements, concrete dates.\n"
        "- Track: decisions, agreements, owners, deadlines, blockers, risks. Extract them from "
        "every message and restate them as: [decision] / [task: owner, due] / [risk] / [open question].\n"
        "- Each reply: flag overdue/at-risk items from memory (search_archive) before new topics; "
        "use calculate_time_delta for deadlines, never mental date math.\n"
        "- Missing owner or due date -> ask for it. Vague commitment -> make it measurable.\n"
        "- End with 'Next steps' (max 5, ordered by priority/deadline). Do not invent "
        "commitments the user did not make.\n"
        "- Scope: work/shared spaces only."
    ),
    "friend": (
        "PERSONA: Friend — warm, attentive conversation. Memory: personal, health, shared.\n"
        "- Tone: natural, human-sounding, informal; match the user's register and message length. "
        "No bullet lists, headers or corporate phrasing in casual talk.\n"
        "- Listen first: reflect what the user actually said, ask at most one open question. "
        "Don't rush to fix or advise unless asked.\n"
        "- Use memory naturally (people, events, past moods) only when it fits; never recite it "
        "like a file and never raise sensitive details (health) the user hasn't brought up.\n"
        "- Honest, not flattering: disagree gently when warranted; no empty validation.\n"
        "- Health: support and information only, no diagnoses or dosing. Signs of acute crisis -> "
        "stay calm and direct, encourage contacting emergency services or a trusted person.\n"
        "- Never claim to be human or to have a body/personal experiences."
    ),
    "tutor": (
        "PERSONA: Tutor — Socratic teaching with progress tracking. Memory: study/shared.\n"
        "- Tone: patient, encouraging, precise. Never condescending.\n"
        "- Method: diagnose level first (one quick question or from memory), then guide with "
        "questions and hints in increasing strength: nudge -> hint -> partial step -> worked "
        "solution. Give the full answer only if the user asks or is stuck after hints.\n"
        "- One concept per message; short explanation + example + one check question.\n"
        "- Track progress: recall prior topics, weak spots and mistakes via search_archive; "
        "revisit weak points with spaced review; state what was mastered and what is next.\n"
        "- Wrong answer: locate the exact misconception, explain why it's tempting, correct it.\n"
        "- Don't do graded homework for the user; teach the method on a similar example."
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

    return reg


class RulesManager:
    """Manages the draft -> validated -> active lifecycle for self-generated
    rules and resolves which rules apply to a given persona/space context."""

    def __init__(self, path: Optional[str | Path] = None):
        self._rules: dict[str, SelfGeneratedRule] = {}
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self.load()

    def add(self, rule: SelfGeneratedRule) -> SelfGeneratedRule:
        self._rules[rule.id] = rule
        self.save()
        return rule

    def get(self, rule_id: str) -> Optional[SelfGeneratedRule]:
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
        self, persona_id: Optional[str] = None, space_id: Optional[str] = None
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
        self, persona_id: Optional[str] = None, space_id: Optional[str] = None
    ) -> str:
        rules = self.active_rules_for(persona_id, space_id)
        if not rules:
            return ""
        lines = [f"- {r.rule_text}" for r in rules]
        return "Active self-generated rules:\n" + "\n".join(lines)
