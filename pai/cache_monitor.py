"""KV-cache load monitoring, used to drive automatic compression depth.

Three sources, tried in order:
1. Real vLLM Prometheus metrics (`gpu_cache_usage_perc`) scraped from the
   inference host's /metrics endpoint — the actual paged-attention KV cache
   occupancy. Only exists for a self-hosted vLLM server.
2. Context-window estimate: live-context tokens vs. the model's real
   context_length (fetched once from the provider, e.g. OpenRouter's
   GET /models). Used for cloud/API backends (OpenRouter, OpenAI, etc.)
   that don't expose GPU-level metrics but do have a known, fixed context
   window — this is a much better proxy than the persona's configured
   budget, since that budget was tuned for a specific self-hosted model
   and may not match whatever model is actually being billed per call.
3. Local fallback: estimate this dialogue's contribution to the live
   context against the persona's configured token budget. Used when
   neither of the above is available (MockLLM, offline dev, provider
   without a /models context_length field).

Either way, the caller only needs `usage_ratio() -> float in [0, 1]` and
`pressure_level() -> int` (0 = fine, higher = compress harder). The 50%
free-cache target lives here as TARGET_FREE_RATIO, not scattered across
callers.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

# We want to keep at least this fraction of the cache free for the next
# turn. Compression pressure ramps up as usage approaches (1 - this).
# This is the default only — CacheMonitor takes target_free_ratio in its
# constructor (derived from the user-configured compression_trigger_pct
# setting), so this module-level constant is just the fallback when no
# override is supplied.
TARGET_FREE_RATIO = 0.5

# Re-check the real vLLM metrics endpoint at most this often — it's a
# network call, and cache occupancy doesn't need sub-second freshness for
# a compression decision.
_METRICS_MIN_INTERVAL_S = 5.0

_GPU_CACHE_RE = re.compile(r"^vllm:gpu_cache_usage_perc(?:\{[^}]*\})?\s+([0-9.eE+-]+)\s*$", re.MULTILINE)


@dataclass
class CacheReading:
    usage_ratio: float          # 0..1, fraction of KV cache currently occupied
    source: str                 # "vllm_metrics" | "context_estimate" | "local_estimate"
    free_ratio: float = 0.0

    def __post_init__(self):
        self.usage_ratio = max(0.0, min(1.0, self.usage_ratio))
        self.free_ratio = 1.0 - self.usage_ratio


class CacheMonitor:
    """Tracks KV-cache / context-window occupancy and translates it into a
    compression pressure level the orchestrator can act on, without any
    backend-specific knowledge leaking into tree.py."""

    def __init__(
        self,
        metrics_url: Optional[str] = None,
        timeout: float = 2.0,
        known_context_length: Optional[int] = None,
        target_free_ratio: float = TARGET_FREE_RATIO,
    ):
        # metrics_url is the vLLM base (same host as PAI_LLM_BASE_URL,
        # typically .../v1) — we derive /metrics from it. For cloud
        # backends this simply won't resolve to anything and stays unused.
        self.metrics_url = self._derive_metrics_url(metrics_url) if metrics_url else None
        self.timeout = timeout
        # Real context window of the active model, if known (e.g. fetched
        # once at startup from OpenRouter's GET /models). None means "use
        # the persona's configured budget instead" (local_estimate).
        self.known_context_length: Optional[int] = known_context_length
        # Fraction of the cache we want to keep free. Compression starts
        # (pressure >= 1) once usage crosses (1 - target_free_ratio) — i.e.
        # this is derived from the user's compression_trigger_pct setting:
        # target_free_ratio = 1 - trigger_pct / 100.
        self.target_free_ratio = max(0.0, min(1.0, target_free_ratio))
        self._last_check_ts = 0.0
        self._last_reading: Optional[CacheReading] = None

    @staticmethod
    def _derive_metrics_url(base_url: str) -> str:
        # base_url is usually ".../v1" — vLLM serves /metrics at the server
        # root, one level up.
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        return root + "/metrics"

    def _fetch_vllm_usage(self) -> Optional[float]:
        if not self.metrics_url:
            return None
        now = time.time()
        if now - self._last_check_ts < _METRICS_MIN_INTERVAL_S and self._last_reading is not None:
            return self._last_reading.usage_ratio if self._last_reading.source == "vllm_metrics" else None
        self._last_check_ts = now
        try:
            req = urllib.request.Request(self.metrics_url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            return None
        m = _GPU_CACHE_RE.search(body)
        if not m:
            return None
        try:
            val = float(m.group(1))
        except ValueError:
            return None
        # vLLM reports this as a 0..1 fraction already.
        return val

    def read(
        self,
        local_estimate_ratio: Optional[float] = None,
        live_context_tokens: Optional[int] = None,
    ) -> CacheReading:
        """Return the best available reading, trying sources in order:
        1) real vLLM metrics, 2) live_context_tokens / known_context_length
        (if the model's real context window is known — cloud backends),
        3) the caller-supplied local_estimate_ratio (persona-budget-based)."""
        vllm_ratio = self._fetch_vllm_usage()
        if vllm_ratio is not None:
            reading = CacheReading(usage_ratio=vllm_ratio, source="vllm_metrics")
        elif self.known_context_length and live_context_tokens is not None:
            reading = CacheReading(
                usage_ratio=live_context_tokens / self.known_context_length,
                source="context_estimate",
            )
        else:
            reading = CacheReading(usage_ratio=local_estimate_ratio or 0.0, source="local_estimate")
        self._last_reading = reading
        return reading

    def pressure_level(self, reading: CacheReading) -> int:
        """0 = below target, no action needed.
        1 = at/above the configured free-cache target — trigger a normal
            compression pass.
        2 = well past it (free ratio dropped to half the target or below)
            — force-compress to a single top too.
        3 = critical (free ratio dropped to a fifth of the target or below)
            — force-compress aggressively, ignore partial-group thresholds
            everywhere.

        The 0.25 / 0.10 breakpoints from the old hardcoded 0.5 target are
        kept proportional to whatever target_free_ratio is now configured
        (half and a fifth of it, respectively), so a lower trigger_pct
        (= higher target_free_ratio) still escalates sensibly instead of
        jumping straight to level 2/3."""
        free = reading.free_ratio
        target = self.target_free_ratio
        if free >= target:
            return 0
        if free >= target * 0.5:
            return 1
        if free >= target * 0.2:
            return 2
        return 3
