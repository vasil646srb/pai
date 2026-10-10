"""KV-cache-driven auto-compression."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..cache_monitor import CacheReading
from ..models import Persona
from ..usage import estimate_tokens

if TYPE_CHECKING:
    from .base import Orchestrator


class CacheManager:
    """Decides when and how deeply to compress based on cache pressure."""

    def __init__(self, orch: Orchestrator):
        # Nothing is cached from `orch` here: reconfigure_llm() replaces
        # orch.cache_monitor at runtime (new context window / trigger %), and
        # attribute access falls through __getattr__ to the live object.
        self.orch = orch

    def __getattr__(self, name):
        """Delegate unknown attributes to Orchestrator."""
        return getattr(self.orch, name)

    def get_cache_status(self) -> dict:
        """Last known reading, or a fresh local-only read if we haven't
        handled a turn yet (no live-context text to estimate from)."""
        if self._last_cache_status:
            return self._last_cache_status
        reading = self.cache_monitor.read(local_estimate_ratio=0.0)
        return {
            "usage_ratio": round(reading.usage_ratio, 4),
            "free_ratio": round(reading.free_ratio, 4),
            "source": reading.source,
            "pressure_level": self.cache_monitor.pressure_level(reading),
            "target_free_ratio": self.cache_monitor.target_free_ratio,
            "auto_compressed": False,
            "compressed_nodes": 0,
        }


    def _local_cache_estimate(self, persona: Persona, system_prompt: str, user_text: str) -> float:
        """Fallback usage ratio against the configured LLM context window.
        Context Window is a model/runtime limit; persona BudgetProfile remains
        an allocation/preview mechanism and must not override this limit."""
        used = estimate_tokens(system_prompt) + estimate_tokens(user_text)
        # NB: the old `max(1, known or 0)` made the "unknown" branch below
        # unreachable, so without a known context length usage_ratio was
        # ~used/1 (>>1) and max-pressure compression ran after EVERY turn.
        context_window = int(getattr(self.cache_monitor, "known_context_length", 0) or 0)
        if context_window <= 0:
            context_window = max(1, persona.budget_profile.total_budget)
        return used / context_window


    def _check_cache_and_maybe_compress(
        self, persona: Persona, space_id: str, system_prompt: str, user_text: str,
    ) -> dict:
        """Read KV-cache occupancy and, if we're past the 50%-free target,
        run compression automatically — the more pressure, the deeper/more
        aggressive the pass. Returns a status dict for the API response so
        the UI can show what happened and why."""
        local_ratio = self._local_cache_estimate(persona, system_prompt, user_text)
        live_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_text)
        reading: CacheReading = self.cache_monitor.read(
            local_estimate_ratio=local_ratio, live_context_tokens=live_tokens,
        )
        pressure = self.cache_monitor.pressure_level(reading)

        status = {
            "usage_ratio": round(reading.usage_ratio, 4),
            "free_ratio": round(reading.free_ratio, 4),
            "source": reading.source,
            "pressure_level": pressure,
            "target_free_ratio": self.cache_monitor.target_free_ratio,
            "auto_compressed": False,
            "compressed_nodes": 0,
        }

        if pressure == 0:
            self._last_cache_status = status
            return status

        if self._other_turn_in_progress():
            # Another chat turn is still being handled (assistant actively
            # responding elsewhere) — compression mutates the memory tree
            # that turn is reading/writing, so defer to the next quiet turn
            # instead of running concurrently with it.
            status["auto_compress_deferred"] = True
            self._last_cache_status = status
            return status

        # pressure 1: normal upward pass, no force_top (let partial groups
        #             wait unless they're already full).
        # pressure >=2: also force-collapse remaining roots to a single top —
        #             this is what actually shrinks tree_tops/tail pressure.
        #             (level 3 uses the same pass; it's already the most
        #             aggressive mode compress_all_levels supports.)
        created = self.tree.compress_all_levels(space_id=space_id, force_top=(pressure >= 2))

        status["auto_compressed"] = len(created) > 0
        status["compressed_nodes"] = len(created)
        self._last_cache_status = status
        return status


