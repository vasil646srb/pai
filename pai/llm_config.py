"""Persisted LLM-provider configuration, editable from the Settings page.

Stored as its own small JSON file under the data dir — deliberately kept
out of the SQLite memory store, since an API key is a different kind of
secret from persona/dialogue data and shouldn't ride along with database
backups or exports. File permissions are tightened to 0600 (owner
read/write only) on every write, best-effort (no-op on platforms where
chmod semantics don't apply, e.g. some container filesystems).
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path


def _clamp_checks(value) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 2


def _clamp_strictness(value) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    # Model used for background summarization (tree compression). Empty
    # string = fall back to `model` (single-model setup, the default).
    # Shares base_url/api_key/site_* and all generation params below with
    # the main assistant model — only the model id differs.
    summarizer_model: str = ""
    # Model that checks the main model's final answer (answer check). Empty
    # string = fall back to the summarizer model. Same base_url/api_key.
    verify_model: str = ""
    # Vision model: describes attached images for the main model. Empty = no
    # separate model; images go straight to the main model (current behavior).
    vision_model: str = ""
    # Answer-check strictness, 1 (lenient) .. 5 (very strict). Minimum usefulness score
    # the checker must give for an answer to pass is derived from it (see dialog.py).
    verify_strictness: int = 3
    # How many answer checks run per turn (1 = check and show only, 2 = one rewrite + recheck).
    verify_max_checks: int = 2
    site_url: str = ""
    site_name: str = ""
    # Generation parameters — the ones that actually change what comes back
    # from the model on every call, as opposed to routing/auth fields above.
    temperature: float = 0.3
    max_tokens: int = 1024
    top_p: float | None = None  # None = omit from the request, let the provider default apply
    # Reasoning ("thinking") tokens — OpenRouter's unified `reasoning` request
    # param (https://openrouter.ai/docs/use-cases/reasoning-tokens). Models
    # that don't support it (or providers that aren't OpenRouter) simply
    # ignore an unknown field, so this is safe to send unconditionally once
    # enabled. gpt-oss models expose their chain-of-thought this way.
    reasoning_enabled: bool = False
    reasoning_effort: str = "medium"  # one of: low | medium | high
    # Advanced parameters
    context_window: int = 8192  # maximum context size
    max_tool_iterations: int = 10  # maximum consecutive function calls
    # KV-cache occupancy percentage (0-100) at which automatic memory-tree
    # compression starts. 50 = start compressing once 50% of the cache is
    # occupied (i.e. 50% free space remains) — the previous hardcoded
    # behavior. Translated into CacheMonitor.target_free_ratio as
    # (100 - compression_trigger_pct) / 100.
    compression_trigger_pct: int = 50
    top_k: int | None = None  # top-k sampling
    frequency_penalty: float | None = None  # penalty for repetition
    presence_penalty: float | None = None  # penalty for new tokens

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def effective_summarizer_model(self) -> str:
        return self.summarizer_model or self.model

    def effective_vision_model(self) -> str:
        return self.vision_model

    def effective_verify_model(self) -> str:
        return self.verify_model or self.effective_summarizer_model()

    def target_free_ratio(self) -> float:
        """CacheMonitor wants 'fraction of cache to keep free', the user
        configures 'occupancy % at which to start compressing' — this
        converts one to the other."""
        return max(0.0, min(1.0, (100 - self.compression_trigger_pct) / 100))

    def masked(self) -> dict:
        """Safe-to-return-to-the-browser view: the key is never sent back
        in full once saved, only enough of the tail to recognize which key
        is active."""
        d = asdict(self)
        key = d.pop("api_key", "")
        if key:
            tail = key[-4:] if len(key) >= 4 else key
            d["api_key_set"] = True
            d["api_key_preview"] = f"{'*' * max(0, len(key) - 4)}{tail}"
        else:
            d["api_key_set"] = False
            d["api_key_preview"] = ""
        return d


class LLMConfigStore:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "llm_config.json"

    def load(self) -> LLMConfig:
        if not self.path.exists():
            return LLMConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return LLMConfig()
        temperature = raw.get("temperature", 0.3)
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            temperature = 0.3
        max_tokens = raw.get("max_tokens", 1024)
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 1024
        top_p = raw.get("top_p")
        if top_p is not None:
            try:
                top_p = float(top_p)
            except (TypeError, ValueError):
                top_p = None
        reasoning_effort = raw.get("reasoning_effort", "medium") or "medium"
        if reasoning_effort not in ("low", "medium", "high"):
            reasoning_effort = "medium"
        context_window = raw.get("context_window", 8192)
        try:
            context_window = int(context_window)
            context_window = max(1024, min(context_window, 200000))  # Clamp to reasonable range
        except (TypeError, ValueError):
            context_window = 8192
        max_tool_iterations = raw.get("max_tool_iterations", 10)
        try:
            max_tool_iterations = int(max_tool_iterations)
            max_tool_iterations = max(1, min(max_tool_iterations, 50))  # Clamp to 1-50
        except (TypeError, ValueError):
            max_tool_iterations = 10
        top_k = raw.get("top_k")
        if top_k is not None:
            try:
                top_k = int(top_k)
                if top_k <= 0:
                    top_k = None
            except (TypeError, ValueError):
                top_k = None
        frequency_penalty = raw.get("frequency_penalty")
        if frequency_penalty is not None:
            try:
                frequency_penalty = float(frequency_penalty)
                frequency_penalty = max(-2.0, min(frequency_penalty, 2.0))
            except (TypeError, ValueError):
                frequency_penalty = None
        presence_penalty = raw.get("presence_penalty")
        if presence_penalty is not None:
            try:
                presence_penalty = float(presence_penalty)
                presence_penalty = max(-2.0, min(presence_penalty, 2.0))
            except (TypeError, ValueError):
                presence_penalty = None
        compression_trigger_pct = raw.get("compression_trigger_pct", 50)
        try:
            compression_trigger_pct = int(compression_trigger_pct)
            compression_trigger_pct = max(1, min(compression_trigger_pct, 95))  # Clamp to 1-95
        except (TypeError, ValueError):
            compression_trigger_pct = 50
        return LLMConfig(
            base_url=raw.get("base_url", "") or "",
            api_key=raw.get("api_key", "") or "",
            model=raw.get("model", "") or "",
            summarizer_model=raw.get("summarizer_model", "") or "",
            verify_model=raw.get("verify_model", "") or "",
            vision_model=raw.get("vision_model", "") or "",
            verify_strictness=_clamp_strictness(raw.get("verify_strictness", 3)),
            verify_max_checks=_clamp_checks(raw.get("verify_max_checks", 2)),
            site_url=raw.get("site_url", "") or "",
            site_name=raw.get("site_name", "") or "",
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            reasoning_enabled=bool(raw.get("reasoning_enabled", False)),
            reasoning_effort=reasoning_effort,
            context_window=context_window,
            max_tool_iterations=max_tool_iterations,
            top_k=top_k,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            compression_trigger_pct=compression_trigger_pct,
        )

    def save(self, config: LLMConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass  # best-effort; not fatal on filesystems that don't support it
