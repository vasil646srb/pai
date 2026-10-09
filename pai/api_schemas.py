"""Pydantic schemas for the FastAPI layer. Kept separate from pai/models.py
(dataclasses) so the storage layer stays framework-agnostic."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    text: str
    persona_id: str = "brain"
    space_id: str = "personal"
    reference_now: Optional[str] = None
    images: list[str] = Field(default_factory=list)


class ToolTraceItem(BaseModel):
    name: str
    arguments: dict
    result: Any


class ChatResponse(BaseModel):
    reply: str
    reasoning: Optional[str] = None
    tool_trace: list[ToolTraceItem]
    live_context_preview: dict
    usage: dict
    cache_status: Optional[dict] = None


class NodeOut(BaseModel):
    id: str
    space_id: str
    level: int
    parent_id: Optional[str]
    child_ids: list[str]
    type: str
    summary_text: str
    raw_content: str
    occurred_at: Optional[float]
    timeframe_start: Optional[float]
    timeframe_end: Optional[float]
    tags: list[str]
    status: str
    retrieval_count: int
    position_in_history: int


class BudgetProfileUpdate(BaseModel):
    system_core: Optional[int] = Field(default=None, ge=0)
    persona_header: Optional[int] = Field(default=None, ge=0)
    current_time: Optional[int] = Field(default=None, ge=0)
    skill_triggers: Optional[int] = Field(default=None, ge=0)
    tree_tops: Optional[int] = Field(default=None, ge=0)
    tail: Optional[int] = Field(default=None, ge=0)
    retrieved_fragments: Optional[int] = Field(default=None, ge=0)
    reasoning_reserve: Optional[int] = Field(default=None, ge=0)
    total_budget: Optional[int] = Field(default=None, ge=1)


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
    budget_profile: Optional[BudgetProfileUpdate] = None


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
    name: Optional[str] = None
    description: Optional[str] = None
    system_core_addon: Optional[str] = None
    active_skill_ids: Optional[list[str]] = None
    allow_spaces: Optional[list[str]] = None
    deny_spaces: Optional[list[str]] = None
    allow_tags: Optional[list[str]] = None
    deny_tags: Optional[list[str]] = None
    tool_permissions: Optional[list[str]] = None
    tone: Optional[str] = None
    budget_profile: Optional[BudgetProfileUpdate] = None


class RuleCreate(BaseModel):
    rule_text: str
    scope: str = "global"
    persona_id: Optional[str] = None
    space_id: Optional[str] = None
    trigger_context: str = ""
    confidence: float = 0.5


class RuleOut(BaseModel):
    id: str
    scope: str
    persona_id: Optional[str]
    space_id: Optional[str]
    trigger_context: str
    rule_text: str
    confidence: float
    status: str
    created_at: float
    validated_at: Optional[float]


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
    space_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
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
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    summarizer_model: Optional[str] = None
    verify_model: Optional[str] = None
    vision_model: Optional[str] = None
    site_url: Optional[str] = None
    site_name: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=131072)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    clear_top_p: bool = False
    reasoning_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = Field(default=None, pattern="^(low|medium|high)$")
    # Advanced parameters
    context_window: Optional[int] = Field(default=None, ge=1024, le=200000)
    max_tool_iterations: Optional[int] = Field(default=None, ge=1, le=50)
    compression_trigger_pct: Optional[int] = Field(default=None, ge=1, le=95)
    top_k: Optional[int] = Field(default=None, ge=1)
    clear_top_k: bool = False
    frequency_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    clear_frequency_penalty: bool = False
    presence_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
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
    validated_at: Optional[float]
    activated_at: Optional[float]
