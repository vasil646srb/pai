"""System prompt and context assembly."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

try:  # zoneinfo is stdlib on 3.9+, but tz data may be missing (Windows)
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from ..models import Persona
from ..skills import DEFAULT_CORE_RULES
from ..time_utils import TimeContext
from ..usage import estimate_tokens

if TYPE_CHECKING:
    from .base import Orchestrator


class ContextBuilder:
    """Builds system prompts and context blocks from storage state."""

    def __init__(self, orch: Orchestrator):
        self.orch = orch
        self.storage = orch.storage
        self.tree = orch.tree
        self.personas = orch.personas
        self.skills = orch.skills

    def __getattr__(self, name):
        """Delegate unknown attributes to Orchestrator."""
        return getattr(self.orch, name)

    def _build_time_context(self, reference_now: datetime | None = None) -> TimeContext:
        """Wall-clock context in the user's timezone (PAI_TIMEZONE / the
        `timezone_name` setting; falls back to the machine's local zone).
        Previously the UTC time was printed under the label of the configured
        zone, so "сегодня/вчера" resolved wrongly around local midnight."""
        now = reference_now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        name = (self.timezone_name or "").strip()
        tz = None
        if name and name.upper() != "UTC" and ZoneInfo is not None:
            try:
                tz = ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                tz = None
        if tz is not None:
            now = now.astimezone(tz)
        elif name.upper() == "UTC":
            now = now.astimezone(timezone.utc)
        else:
            now = now.astimezone()  # system local zone
            name = now.tzname() or name or "local"
        return TimeContext(now=now, timezone_name=name or "UTC")

    def _tail_nodes(self, space_id: str, persona: Persona | None = None):
        """Last `tail_length` dialogue nodes (tool/skill results excluded —
        they are internal state, not conversation), honoring the persona's
        memory filter (deny_tags etc.)."""
        recent = self.storage.get_recent_level0(space_id, limit=self.tail_length * 3)
        recent = [n for n in recent if "notice" not in (n.tags or [])]
        if persona is not None:
            recent = [n for n in recent if persona.memory_filter.permits(n.space_id, n.tags)]
        return recent[-self.tail_length:]

    @staticmethod
    def _speaker(n) -> str:
        tags = n.tags or []
        if "assistant" in tags:
            return "Assistant"
        return "User"

    def _tail_block(self, space_id: str, persona: Persona | None = None, budget_tokens: int | None = None) -> str:
        recent = self._tail_nodes(space_id, persona)
        local_tz = self._build_time_context().now.tzinfo or timezone.utc
        lines = []
        for n in recent:
            ts = ""
            if n.occurred_at:
                ts = datetime.fromtimestamp(n.occurred_at, tz=local_tz).strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{ts}] {self._speaker(n)}: {n.raw_content}")
        if budget_tokens and budget_tokens > 0:
            lines = self._fit_lines_to_budget(lines, budget_tokens, keep="last")
        return "\n".join(lines)

    @staticmethod
    def _fit_lines_to_budget(lines: list[str], budget_tokens: int, keep: str = "last") -> list[str]:
        """Drop whole lines (oldest first for `keep="last"`) until the block
        fits the persona's token budget; a single over-long line is cut."""
        order = list(reversed(lines)) if keep == "last" else list(lines)
        kept: list[str] = []
        used = 0
        for line in order:
            cost = estimate_tokens(line) + 1
            if used + cost > budget_tokens:
                if not kept:  # newest line alone is too big: truncate it
                    max_chars = max(80, int(budget_tokens * 2))
                    kept.append(line[:max_chars] + " …[truncated]")
                break
            kept.append(line)
            used += cost
        if keep == "last":
            kept.reverse()
        return kept

    def _tree_tops_block(self, space_id: str, max_nodes: int = 8, budget_tokens: int | None = None) -> str:
        tops = self.tree.get_tree_tops(space_id=space_id, max_nodes=max_nodes, min_level=1)
        lines = []
        for n in tops:
            tf = ""
            if n.timeframe_start and n.timeframe_end:
                start = datetime.fromtimestamp(n.timeframe_start, tz=timezone.utc).strftime("%Y-%m")
                end = datetime.fromtimestamp(n.timeframe_end, tz=timezone.utc).strftime("%Y-%m")
                tf = f"{start} — {end}"
            lines.append(f"[level {n.level}] id={n.id} | {tf} | {n.summary_text[:200]}")
        if budget_tokens and budget_tokens > 0:
            lines = self._fit_lines_to_budget(lines, budget_tokens, keep="first")
        return "\n".join(lines)


    def build_system_segments(
        self, persona: Persona, space_id: str, reference_now: datetime | None = None
    ) -> list[dict]:
        """System prompt as labelled segments. Single source of truth: the
        final prompt is "\\n\\n".join(seg["text"]) — the log breakdown is
        therefore exact, never re-parsed from text.
        seg: {key, label, group, text, tokens_est, meta}"""
        tctx = self._build_time_context(reference_now)
        segs: list[dict] = []

        def add(key, label, group, text, **meta):
            if text and text.strip():
                segs.append({"key": key, "label": label, "group": group, "text": text,
                             "tokens_est": estimate_tokens(text), "meta": meta})

        mf = persona.memory_filter
        budget = persona.budget_profile

        add("core_rules", "Базовые правила (core rules)", "instructions", DEFAULT_CORE_RULES)
        add("persona_addon", f"Инструкции персоны «{persona.name}»", "instructions", persona.system_core_addon)
        filt = [f"Current memory space: {space_id}",
                f"Readable spaces: {', '.join(mf.allow_spaces) or 'all'}"]
        if mf.deny_spaces:
            filt.append(f"Denied spaces: {', '.join(mf.deny_spaces)}")
        if mf.deny_tags:
            filt.append(f"Hidden tags: {', '.join(mf.deny_tags)}")
        add("space_filter", "Фильтр пространств", "instructions", "\n".join(filt))

        skill_triggers = self.skills.triggers_block(persona.active_skill_ids)
        if skill_triggers:
            add("skills", "Триггеры активных навыков", "instructions",
                "Active skills (when a trigger matches the user's request, call "
                "get_skill with its id FIRST and follow the returned instructions):\n" + skill_triggers,
                count=len(persona.active_skill_ids),
                items=list(persona.active_skill_ids))

        rules = self.rules.active_rules_for(persona.persona_id, space_id)
        rules_block = self.rules.render_active_rules_block(persona.persona_id, space_id)
        if rules_block:
            add("rules", "Активные правила (self-generated)", "instructions", rules_block,
                count=len(rules or []),
                items=[getattr(r, "rule_text", str(r))[:160] for r in (rules or [])])

        tree_tops = self._tree_tops_block(space_id, budget_tokens=budget.tree_tops)
        if tree_tops:
            add("tree_tops", "Сжатая память (tree tops)", "memory",
                "Tree tops:\n" + tree_tops, count=len(tree_tops.splitlines()))

        tail_nodes = self._tail_nodes(space_id, persona)
        tail = self._tail_block(space_id, persona, budget_tokens=budget.tail)
        if tail:
            n_user = sum(1 for n in tail_nodes if self._speaker(n) == "User")
            add("tail", "История чата (tail)", "history", "Tail:\n" + tail,
                count=len(tail_nodes), user_msgs=n_user,
                assistant_msgs=len(tail_nodes) - n_user,
                tail_length_cfg=self.tail_length)
        # Time goes last: it changes every minute, so placing it before memory and history
        # would invalidate the KV cache for everything after it.
        add("time_header", "Время / персона", "instructions",
            tctx.header(persona_name=persona.name, tone=persona.tone))
        return segs

    def build_system_prompt(
        self, persona: Persona, space_id: str, reference_now: datetime | None = None
    ) -> str:
        segs = self.build_system_segments(persona, space_id, reference_now)
        return "\n\n".join(sg["text"] for sg in segs)

    # ---------- main turn loop ----------

    # ---------- KV-cache-driven auto-compression ----------


