"""Pydantic schemas for the FastAPI layer. Kept separate from pai/models.py
(dataclasses) so the storage layer stays framework-agnostic."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    text: str
    persona_id: str = "brain"
    space_id: str = "personal"
    reference_now: str | None = None
    images: list[str] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)


class ToolTraceItem(BaseModel):
    name: str
    arguments: dict
    result: Any


class ChatResponse(BaseModel):
    reply: str
    reasoning: str | None = None
    tool_trace: list[ToolTraceItem]
    live_context_preview: dict
    usage: dict
    cache_status: dict | None = None


class NodeOut(BaseModel):
    id: str
    space_id: str
    level: int
    parent_id: str | None
    child_ids: list[str]
    type: str
    summary_text: str
    raw_content: str
    occurred_at: float | None
    timeframe_start: float | None
    timeframe_end: float | None
    tags: list[str]
    status: str
    retrieval_count: int
    position_in_history: int


class BudgetProfileUpdate(BaseModel):
    system_core: int | None = Field(default=None, ge=0)
    persona_header: int | None = Field(default=None, ge=0)
    current_time: int | None = Field(default=None, ge=0)
    skill_triggers: int | None = Field(default=None, ge=0)
    tree_tops: int | None = Field(default=None, ge=0)
    tail: int | None = Field(default=None, ge=0)
    retrieved_fragments: int | None = Field(default=None, ge=0)
    reasoning_reserve: int | None = Field(default=None, ge=0)
    total_budget: int | None = Field(default=None, ge=1)


class PersonaOut(BaseModel):
    persona_id: str
    name: str
    description: str
    system_core_addon: str = ""
    active_skill_ids: list[str]
    allow_spaces: list[str]
    deny_spaces: list[str]
    allow_tags: list[str] = Field(default_factory=list)
    deny_tags: list[str] = Field(default_factory=list)
    tone: str
    tool_permissions: list[str]
    budget_profile: BudgetProfileUpdate | None = None


class PersonaCreate(BaseModel):
    persona_id: str
    name: str
    description: str = ""
    system_core_addon: str = ""
    active_skill_ids: list[str] = Field(default_factory=list)
    allow_spaces: list[str] = Field(default_factory=list)
    deny_spaces: list[str] = Field(default_factory=list)
    allow_tags: list[str] = Field(default_factory=list)
    deny_tags: list[str] = Field(default_factory=list)
    tool_permissions: list[str] = Field(
        default_factory=lambda: ["get_fragment", "search_archive", "calculate_time_delta", "web_search", "web_fetch"]
    )
    tone: str = "neutral"


class PersonaToolsUpdate(BaseModel):
    tool_permissions: list[str]


class PersonaUpdate(BaseModel):
    """Partial update of a persona's settings. Any field left as None is unchanged."""
    name: str | None = None
    description: str | None = None
    system_core_addon: str | None = None
    active_skill_ids: list[str] | None = None
    allow_spaces: list[str] | None = None
    deny_spaces: list[str] | None = None
    allow_tags: list[str] | None = None
    deny_tags: list[str] | None = None
    tool_permissions: list[str] | None = None
    tone: str | None = None
    budget_profile: BudgetProfileUpdate | None = None


class RuleCreate(BaseModel):
    rule_text: str
    scope: str = "global"
    persona_id: str | None = None
    space_id: str | None = None
    trigger_context: str = ""
    confidence: float = 0.5


class RuleOut(BaseModel):
    id: str
    scope: str
    persona_id: str | None
    space_id: str | None
    trigger_context: str
    rule_text: str
    confidence: float
    status: str
    created_at: float
    validated_at: float | None


class SkillCreate(BaseModel):
    skill_id: str
    trigger_description: str
    full_instruction_text: str


class SkillOut(BaseModel):
    skill_id: str
    trigger_description: str
    full_instruction_text: str
    version: int
    last_updated: float


class SearchRequest(BaseModel):
    query: str
    space_id: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    limit: int = 10


class TimeDeltaRequest(BaseModel):
    date1: str
    date2: str


class CompressRequest(BaseModel):
    space_id: str = "personal"
    force_top: bool = False


class NodeStatusUpdate(BaseModel):
    status: str


class LLMSettingsUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    summarizer_model: str | None = None
    planner_model: str | None = None
    verify_model: str | None = None
    vision_model: str | None = None
    verify_strictness: int | None = Field(default=None, ge=1, le=5)
    verify_max_checks: int | None = Field(default=None, ge=1, le=5)
    site_url: str | None = None
    site_name: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    clear_top_p: bool = False
    reasoning_enabled: bool | None = None
    reasoning_effort: str | None = Field(default=None, pattern="^(low|medium|high)$")
    # Advanced parameters
    context_window: int | None = Field(default=None, ge=1024, le=200000)
    max_tool_iterations: int | None = Field(default=None, ge=1, le=50)
    compression_trigger_pct: int | None = Field(default=None, ge=1, le=95)
    top_k: int | None = Field(default=None, ge=1)
    clear_top_k: bool = False
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    clear_frequency_penalty: bool = False
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    clear_presence_penalty: bool = False


class FunctionCreate(BaseModel):
    name: str
    description: str = ""
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    kind: str = "llm_prompt"
    implementation: dict = Field(default_factory=dict)


class FunctionOut(BaseModel):
    id: str
    name: str
    description: str
    parameters: dict
    kind: str
    implementation: dict
    status: str
    created_at: float
    validated_at: float | None
    activated_at: float | None


class SpaceRename(BaseModel):
    new_name: str
