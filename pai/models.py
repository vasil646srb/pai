"""Core data models: memory nodes, personas, rules."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_ts() -> float:
    return time.time()


class NodeType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    NOTE = "note"
    DECISION = "decision"
    TASK = "task"
    SELF_GENERATED_RULE = "self_generated_rule"
    SKILL = "skill"
    ANNOTATION = "annotation"
    SUMMARY = "summary"


class NodeStatus(str, Enum):
    ACTIVE = "active"
    PINNED = "pinned"
    EXCLUDED = "excluded"
    ARCHIVED = "archived"
    HIDDEN = "hidden"


class RuleScope(str, Enum):
    GLOBAL = "global"
    PERSONA = "persona"
    SPACE = "space"
    PROJECT = "project"


class RuleStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ACTIVE = "active"
    REJECTED = "rejected"
    ARCHIVED = "archived"


@dataclass
class TemporalAnnotation:
    """Resolved relative-date annotation, stored separately from raw text."""
    raw_phrase: str
    resolved_date: str | None  # ISO date string, None if unresolved
    confidence: str = "medium"  # low | medium | high

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> TemporalAnnotation:
        return TemporalAnnotation(**d)


@dataclass
class MemoryNode:
    """A single node in the hierarchical memory tree.

    level 0 = raw fragment. level N>0 = summary of children.
    """
    id: str = field(default_factory=new_id)
    space_id: str = "personal"
    level: int = 0
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)
    type: NodeType = NodeType.TEXT
    summary_text: str = ""
    raw_content: str = ""
    media_path: str | None = None
    timestamp: float = field(default_factory=now_ts)
    occurred_at: float | None = None
    ingested_at: float = field(default_factory=now_ts)
    position_in_history: int = 0
    timeframe_start: float | None = None
    timeframe_end: float | None = None
    tags: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.ACTIVE
    annotations: list[TemporalAnnotation] = field(default_factory=list)
    retrieval_count: int = 0
    # Assistant chain-of-thought, kept so the UI can show it after a reload.
    # Display-only: never included in prompts or summaries.
    reasoning: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value if isinstance(self.type, NodeType) else self.type
        d["status"] = self.status.value if isinstance(self.status, NodeStatus) else self.status
        d["annotations"] = [a if isinstance(a, dict) else a.to_dict() for a in self.annotations]
        return d

    @staticmethod
    def from_dict(d: dict) -> MemoryNode:
        d = dict(d)
        d["type"] = NodeType(d.get("type", "text"))
        d["status"] = NodeStatus(d.get("status", "active"))
        d["annotations"] = [
            a if isinstance(a, TemporalAnnotation) else TemporalAnnotation.from_dict(a)
            for a in d.get("annotations", [])
        ]
        return MemoryNode(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> MemoryNode:
        return MemoryNode.from_dict(json.loads(s))


@dataclass
class MemoryFilter:
    allow_spaces: list[str] = field(default_factory=list)  # empty = all allowed
    deny_spaces: list[str] = field(default_factory=list)
    allow_tags: list[str] = field(default_factory=list)  # empty = no tag restriction
    allow_prefixes: list[str] = field(default_factory=list)  # e.g. 'guest:' -> any guest:* space
    deny_tags: list[str] = field(default_factory=list)

    def permits(self, space_id: str, tags: list[str]) -> bool:
        if self.deny_spaces and space_id in self.deny_spaces:
            return False
        if (self.allow_spaces or self.allow_prefixes) and not (
            space_id in self.allow_spaces or any(space_id.startswith(p) for p in self.allow_prefixes)
        ):
            return False
        if self.deny_tags and any(t in self.deny_tags for t in tags):
            return False
        if self.allow_tags and not any(t in self.allow_tags for t in tags):
            return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> MemoryFilter:
        return MemoryFilter(**d)


@dataclass
class BudgetProfile:
    """Token budget allocation across live-context categories."""
    system_core: int = 800
    persona_header: int = 200
    current_time: int = 50
    skill_triggers: int = 400
    tree_tops: int = 3000
    tail: int = 4000
    retrieved_fragments: int = 4000
    reasoning_reserve: int = 1000
    total_budget: int = 16000

    def allocated(self) -> int:
        return (
            self.system_core + self.persona_header + self.current_time
            + self.skill_triggers + self.tree_tops + self.tail
            + self.retrieved_fragments + self.reasoning_reserve
        )

    def free_space(self) -> int:
        return max(0, self.total_budget - self.allocated())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Persona:
    persona_id: str
    name: str
    description: str = ""
    system_core_addon: str = ""
    active_skill_ids: list[str] = field(default_factory=list)
    memory_filter: MemoryFilter = field(default_factory=MemoryFilter)
    tool_permissions: list[str] = field(default_factory=lambda: [
        "get_fragment", "search_archive", "calculate_time_delta"
    ])
    budget_profile: BudgetProfile = field(default_factory=BudgetProfile)
    tone: str = "neutral"
    status: str = "active"
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    # One-shot data migrations already applied to this persona (see
    # PersonaRegistry.load) — lets us ship new built-in defaults without
    # re-applying them over the user's later edits on every start.
    migrations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> Persona:
        d = dict(d)
        mf = d.get("memory_filter", {})
        d["memory_filter"] = MemoryFilter.from_dict(mf) if isinstance(mf, dict) else mf
        bp = d.get("budget_profile", {})
        d["budget_profile"] = BudgetProfile(**bp) if isinstance(bp, dict) else bp
        return Persona(**d)


@dataclass
class SelfGeneratedRule:
    id: str = field(default_factory=new_id)
    type: str = "self_generated_rule"
    scope: RuleScope = RuleScope.GLOBAL
    persona_id: str | None = None
    space_id: str | None = None
    trigger_context: str = ""
    rule_text: str = ""
    confidence: float = 0.5
    derived_from: list[str] = field(default_factory=list)  # source node ids
    status: RuleStatus = RuleStatus.DRAFT
    created_at: float = field(default_factory=now_ts)
    validated_at: float | None = None
    last_reviewed_at: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scope"] = self.scope.value if isinstance(self.scope, RuleScope) else self.scope
        d["status"] = self.status.value if isinstance(self.status, RuleStatus) else self.status
        return d

    @staticmethod
    def from_dict(d: dict) -> SelfGeneratedRule:
        d = dict(d)
        d["scope"] = RuleScope(d.get("scope", "global"))
        d["status"] = RuleStatus(d.get("status", "draft"))
        return SelfGeneratedRule(**d)

    def promote(self, new_status: RuleStatus) -> None:
        """Enforce draft -> validated -> active lifecycle. No skipping stages forward,
        but rejection/archival is allowed from any state."""
        order = [RuleStatus.DRAFT, RuleStatus.VALIDATED, RuleStatus.ACTIVE]
        if new_status in (RuleStatus.REJECTED, RuleStatus.ARCHIVED):
            self.status = new_status
            self.last_reviewed_at = now_ts()
            return
        if new_status not in order:
            raise ValueError(f"Unknown status {new_status}")
        cur_idx = order.index(self.status) if self.status in order else -1
        new_idx = order.index(new_status)
        if new_idx != cur_idx + 1:
            raise ValueError(
                f"Cannot promote rule from {self.status} directly to {new_status}; "
                f"lifecycle is draft -> validated -> active"
            )
        self.status = new_status
        if new_status == RuleStatus.VALIDATED:
            self.validated_at = now_ts()
        self.last_reviewed_at = now_ts()
