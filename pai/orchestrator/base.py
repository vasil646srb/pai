"""Orchestrator core: initialization, configuration, turn synchronization."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from ..llm import LLMClient, LLMError
from ..models import MemoryNode, NodeType, Persona, now_ts
from ..personas import PersonaRegistry, RulesManager
from ..cache_monitor import CacheMonitor, CacheReading, TARGET_FREE_RATIO
from ..skills import DEFAULT_CORE_RULES, SkillCatalog
from ..storage import Storage
from ..time_utils import TimeContext, normalize_relative_dates
from ..tools import ToolDispatcher, ToolError
from ..tree import TreeBuilder
from ..usage import UsageTracker, estimate_tokens

from .context import ContextBuilder
from .cache import CacheManager
from .llm_runner import LLMRunner
from .tool_pool import ToolPoolPlanner

MAX_TOOL_ITERATIONS = 6
from .llm_runner import _ITERATION_LIMIT_FALLBACK, _ITERATION_LIMIT_NUDGE  # noqa: F401


@dataclass
class TurnResult:
    """Result of a single user turn."""
    reply: str
    reasoning: Optional[str] = None
    tool_trace: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    cache_status: dict = field(default_factory=dict)
    # Snapshot of the assembled live-context budget for this turn
    # (system/user/reply token estimates + persona budget profile).
    # Consumed by api_schemas.ChatResponse and rendered by the chat widget.
    live_context_preview: dict = field(default_factory=dict)
    compressed: bool = False
    compressed_nodes: int = 0


def _build_user_content(user_text: str, images: Optional[list[str]] = None) -> str:
    """Build user message content, optionally with images."""
    if not images:
        return user_text
    parts = [{"type": "text", "text": user_text}]
    for img in images:
        if img.startswith("data:"):
            parts.append({"type": "image_url", "image_url": {"url": img}})
        else:
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img}"}
            })
    return parts  # type: ignore[return-value]


class Orchestrator:
    """Facade for all orchestration components."""

    def __init__(
        self,
        storage: Storage,
        llm: LLMClient,
        persona_registry: PersonaRegistry,
        skill_catalog: SkillCatalog,
        rules_manager: Optional[RulesManager] = None,
        tail_length: int = 6,
        timezone_name: str = "",
        cache_monitor: Optional[CacheMonitor] = None,
        llm_base_url: Optional[str] = None,
        known_context_length: Optional[int] = None,
        max_tool_iterations: int = MAX_TOOL_ITERATIONS,
        custom_functions=None,
        summarizer_llm: Optional[LLMClient] = None,
        verifier_llm: Optional[LLMClient] = None,
        vision_llm: Optional[LLMClient] = None,
        verify_strictness: int = 3,
        target_free_ratio: float = TARGET_FREE_RATIO,
    ):
        self.storage = storage
        self.llm = llm
        self.personas = persona_registry
        self.skills = skill_catalog
        self.rules = rules_manager or RulesManager()
        self.custom_functions = custom_functions
        # Summarization (tree compression) can run on a different model —
        # falls back to the main assistant client when none is given.
        self.summarizer_llm = summarizer_llm or llm
        # Answer check model; falls back to the summarizer when not given.
        self.verifier_llm = verifier_llm or self.summarizer_llm
        # Image description model; None = images go to the main model directly.
        self.vision_llm = vision_llm
        self.verify_strictness = verify_strictness
        self.tree = TreeBuilder(storage, self.summarizer_llm)
        self.usage = UsageTracker()
        self.tree.usage = self.usage
        self.tail_length = tail_length
        self.timezone_name = timezone_name
        self.max_tool_iterations = max_tool_iterations
        self._history: list[MemoryNode] = []  # raw level-0 nodes not yet compressed, this session
        self.cache_monitor = cache_monitor or CacheMonitor(
            metrics_url=llm_base_url, known_context_length=known_context_length,
            target_free_ratio=target_free_ratio,
        )
        self._last_cache_status: dict = {}
        # Tracks turns currently being handled (any persona/space) so
        # auto-compression can defer instead of running while the user is
        # actively mid-conversation with the assistant — see
        # _check_cache_and_maybe_compress / _turn_in_progress.
        self._active_turns = 0
        self._active_turns_lock = threading.Lock()
        # Per-thread (per-turn) system prompt segments, read by LLMRunner
        # for the request breakdown in the action log.
        self._turn_local = threading.local()

        # Compose specialized components
        self.context = ContextBuilder(self)
        self.cache = CacheManager(self)
        self.llm_runner = LLMRunner(self)
        self.tool_pool = ToolPoolPlanner(self)
        self.dialog = DialogLoop(self)


    def reconfigure_llm(
        self,
        llm: LLMClient,
        llm_base_url: Optional[str] = None,
        known_context_length: Optional[int] = None,
        max_tool_iterations: Optional[int] = None,
        summarizer_llm: Optional[LLMClient] = None,
        verifier_llm: Optional[LLMClient] = None,
        vision_llm: Optional[LLMClient] = None,
        verify_strictness: int = 3,
        target_free_ratio: float = TARGET_FREE_RATIO,
    ) -> None:
        """Hot-swap the active LLM client (and everything derived from it —
        TreeBuilder holds its own reference, and CacheMonitor's vLLM-metrics
        URL / known context length are tied to the old backend) without
        restarting the process. Safe to call while requests are in flight:
        Python attribute assignment is atomic under the GIL, so an
        in-progress call keeps using whatever object it already grabbed a
        reference to; only calls starting after this returns see the new
        client. `summarizer_llm` defaults to `llm` when not given, matching
        single-model setups."""
        self.llm = llm
        self.summarizer_llm = summarizer_llm or llm
        self.verifier_llm = verifier_llm or self.summarizer_llm
        self.vision_llm = vision_llm
        self.verify_strictness = verify_strictness
        self.tree.llm = self.summarizer_llm
        if max_tool_iterations is not None:
            self.max_tool_iterations = max_tool_iterations
        self.cache_monitor = CacheMonitor(
            metrics_url=llm_base_url, known_context_length=known_context_length,
            target_free_ratio=target_free_ratio,
        )

    # ---------- live context assembly ----------


    def _turn_started(self) -> None:
        with self._active_turns_lock:
            self._active_turns += 1


    def _turn_finished(self) -> None:
        with self._active_turns_lock:
            self._active_turns = max(0, self._active_turns - 1)


    def _other_turn_in_progress(self) -> bool:
        """True if some turn besides the one calling this is still active —
        i.e. there's a live chat exchange in flight and the assistant is
        (or was, a moment ago) responding to it."""
        with self._active_turns_lock:
            return self._active_turns > 1


    # ---------- Delegated to sub-components ----------

    def get_cache_status(self) -> dict:
        """Get current cache status without triggering compression."""
        return self.cache.get_cache_status()

    def handle_turn(self, user_text: str, persona_id: str, space_id: str = "personal", reference_now=None, images=None,
                    origin=None):
        """Process a single user turn (non-streaming)."""
        return self.dialog.handle_turn(user_text, persona_id, space_id, reference_now, images, origin)

    def handle_turn_stream(self, user_text: str, persona_id: str, space_id: str = "personal", reference_now=None,
                           images=None, origin=None):
        """Process a single user turn with streaming response."""
        return self.dialog.handle_turn_stream(user_text, persona_id, space_id, reference_now, images, origin)

    def run_background_compression(self, space_id: str = "personal", force_top: bool = False):
        """Run tree compression in the background."""
        return self.tree.compress_all_levels(space_id=space_id, force_top=force_top)


    def __getattr__(self, name):
        """Delegate unknown attributes to sibling components.

        DialogLoop / LLMRunner / ToolPoolPlanner / CacheManager each hold a
        reference to the orchestrator. Without this fallback, a call to a
        method owned by a *sibling* (e.g. DialogLoop calling
        self._build_time_context, which lives on ContextBuilder) raises
        AttributeError on the very first turn.
        """
        for attr in ("context", "cache", "llm_runner", "tool_pool", "dialog"):
            comp = self.__dict__.get(attr)
            if comp is None:
                continue
            # Only consider attributes actually defined on the component's
            # class, so we never trigger a component's own __getattr__
            # (which delegates back to Orchestrator and would recurse).
            # `getattr(comp, name)` — not a manual __get__ call — is what
            # correctly resolves staticmethod / classmethod / regular
            # methods. cls_attr.__get__(comp, ...) would bind `self` to a
            # @staticmethod, turning `f(x)` into `f(comp, x)`.
            if not hasattr(type(comp), name):
                continue
            return getattr(comp, name)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )


# Import DialogLoop at end to avoid circular import
from .dialog import DialogLoop  # noqa: E402
