"""Accurate LLM usage/cost tracking with OpenRouter usage metadata."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


def estimate_tokens(text: str, tokenizer_id: str = "approx-4chars") -> int:
    """Cheap, tokenizer-free estimate. ASCII/Latin text averages ~4 chars per
    token, but Cyrillic and other non-ASCII scripts are far denser (~2 chars
    per token), so counting them at 1/4 under-estimates Russian prompts by
    almost half and breaks context-window and compression decisions."""
    if not text:
        return 0
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ascii_chars = len(text) - non_ascii
    return max(1, int(ascii_chars / 4 + non_ascii / 2.2) + 1)


@dataclass
class UsageEvent:
    ts: float
    kind: str
    input_tokens: int
    output_tokens: int
    tool_calls: int
    latency_seconds: float
    cost_usd: float
    model: str = ""
    context_length: Optional[int] = None
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    source: str = "estimate"
    prompt_price_per_1m: Optional[float] = None
    completion_price_per_1m: Optional[float] = None
    total_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "ts": self.ts, "kind": self.kind,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens or self.input_tokens + self.output_tokens,
            "tool_calls": self.tool_calls, "latency_seconds": round(self.latency_seconds, 4),
            "generation_tokens_per_second": round(
                self.output_tokens / self.latency_seconds, 3
            ) if self.latency_seconds > 0 else 0,
            "cost_usd": round(self.cost_usd, 8), "model": self.model,
            "context_length": self.context_length,
            "context_utilization": round(self.input_tokens / self.context_length, 4)
                if self.context_length else None,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "source": self.source,
            "prompt_price_per_1m": self.prompt_price_per_1m,
            "completion_price_per_1m": self.completion_price_per_1m,
        }


@dataclass
class UsageTracker:
    input_price_per_1k: float = 0.15
    output_price_per_1k: float = 0.60
    events: list[UsageEvent] = field(default_factory=list)

    def record_api(
        self, kind: str, usage: dict[str, Any], latency_seconds: float,
        *, model: str = "", context_length: Optional[int] = None,
        tool_calls: int = 0, prompt_price_per_1m: Optional[float] = None,
        completion_price_per_1m: Optional[float] = None,
    ) -> UsageEvent:
        """Record provider-reported usage. OpenRouter is the source of truth."""
        inp = int(usage.get("prompt_tokens") or 0)
        out = int(usage.get("completion_tokens") or 0)
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        cost = usage.get("cost")
        if cost is None:
            pin = prompt_price_per_1m if prompt_price_per_1m is not None else self.input_price_per_1k * 1000
            pout = completion_price_per_1m if completion_price_per_1m is not None else self.output_price_per_1k * 1000
            cost = inp * pin / 1_000_000 + out * pout / 1_000_000
        ev = UsageEvent(
            ts=time.time(), kind=kind, input_tokens=inp, output_tokens=out,
            tool_calls=tool_calls, latency_seconds=latency_seconds, cost_usd=float(cost or 0),
            model=model or str(usage.get("model") or ""), context_length=context_length,
            cached_input_tokens=cached, reasoning_tokens=reasoning, source="openrouter",
            prompt_price_per_1m=prompt_price_per_1m,
            completion_price_per_1m=completion_price_per_1m,
            total_tokens=int(usage.get("total_tokens") or inp + out),
        )
        self.events.append(ev)
        return ev

    def record(self, kind: str, input_text: str, output_text: str,
               tool_calls: int, latency_seconds: float) -> UsageEvent:
        in_tok, out_tok = estimate_tokens(input_text), estimate_tokens(output_text)
        cost = (in_tok / 1000) * self.input_price_per_1k + (out_tok / 1000) * self.output_price_per_1k
        ev = UsageEvent(time.time(), kind, in_tok, out_tok, tool_calls, latency_seconds, cost,
                        source="estimate", total_tokens=in_tok + out_tok)
        self.events.append(ev)
        return ev

    def summary(self) -> dict:
        total_in = sum(e.input_tokens for e in self.events)
        total_out = sum(e.output_tokens for e in self.events)
        total_cost = sum(e.cost_usd for e in self.events)
        calls = len(self.events)
        lat = sum(e.latency_seconds for e in self.events)
        reasoning = sum(e.reasoning_tokens for e in self.events)
        cached = sum(e.cached_input_tokens for e in self.events)
        return {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "total_cost_usd": round(total_cost, 8),
            "total_calls": calls,
            "calls_by_kind": self._by_kind(),
            "avg_latency_seconds": round(lat / calls, 3) if calls else 0.0,
            "avg_generation_tokens_per_second": round(total_out / lat, 3) if lat else 0.0,
            "total_reasoning_tokens": reasoning,
            "total_cached_input_tokens": cached,
            "last_event": self.events[-1].to_dict() if self.events else None,
            "events": [e.to_dict() for e in self.events[-100:]],
        }

    def events_since(self, index: int) -> list[dict]:
        """Dicts for every event appended at or after `index`, uncapped.
        summary()["events"] is capped at the last 100, so it must not be
        sliced by a running counter — after 100 calls the slice would come
        back empty and the per-turn usage widget would read all zeros."""
        return [e.to_dict() for e in self.events[index:]]

    def _by_kind(self):
        out: dict[str, int] = {}
        for e in self.events:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out
