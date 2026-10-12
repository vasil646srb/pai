"""Health and usage/pricing endpoints."""
from __future__ import annotations

import json
import logging
import time

from fastapi import Depends, FastAPI

from ...llm import OpenAICompatibleClient
from ...orchestrator import Orchestrator
from ..deps import get_orchestrator

_log = logging.getLogger("pai.web")


def _openrouter_get(llm, path: str) -> dict:
    """GET {base_url}{path} with the configured key; returns the body's "data" object."""
    with llm._open(llm.base_url + path, None, method="GET") as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("data", body) if isinstance(body, dict) else {}


def register(app: FastAPI) -> None:
    @app.get("/health")
    def health():
        return {"status": "ok"}


    @app.get("/usage/balance")
    def usage_balance(refresh: bool = False):
        """OpenRouter balance for the configured key. The key itself is never returned."""
        llm = app.state.llm
        if not isinstance(llm, OpenAICompatibleClient) or not llm.is_openrouter:
            return {"supported": False}
        now = time.time()
        cache = getattr(app.state, "_openrouter_balance_cache", None)
        if not refresh and cache and now - cache["ts"] < 60:
            return cache["data"]
        out: dict = {"supported": True, "ts": now, "key": None, "account": None, "error": None}
        keep = ("label", "limit", "limit_remaining", "limit_reset", "usage", "usage_daily",
                "usage_weekly", "usage_monthly", "is_free_tier")
        try:
            key = _openrouter_get(llm, "/key")
            out["key"] = {k: key.get(k) for k in keep}
        except Exception as e:  # balance is optional: report the reason, keep the page working
            _log.warning("openrouter key lookup failed: %s", e)
            out["error"] = f"{type(e).__name__}: {e}"
        try:  # account-level credits: only if the key is allowed to read them
            credits = _openrouter_get(llm, "/credits")
            out["account"] = {"total_credits": credits.get("total_credits"), "total_usage": credits.get("total_usage")}
        except Exception:
            out["account"] = None
        app.state._openrouter_balance_cache = {"ts": now, "data": out}
        return out

    @app.get("/usage")
    def usage(orch: Orchestrator = Depends(get_orchestrator)):
        summary = orch.usage.summary()
        # OpenRouter's /models is the live pricing/context source. Cache briefly
        # so refreshing the Usage page does not hammer the provider.
        pricing = {}
        llm = app.state.llm
        now = time.time()
        cache = getattr(app.state, "_openrouter_model_cache", None)
        if isinstance(llm, OpenAICompatibleClient):
            if not cache or now - cache.get("ts", 0) > 300:
                try:
                    models = llm.list_models()
                    pricing = {m["id"]: m for m in models}
                    app.state._openrouter_model_cache = {"ts": now, "models": pricing}
                except Exception as e:  # pricing is optional: fall back to cached values
                    _log.warning("pricing lookup failed, using cached values: %s", e, exc_info=True)
                    pricing = (cache or {}).get("models", {})
            else:
                pricing = cache.get("models", {})

        model_rows = {}
        for event in summary["events"]:
            mid = event.get("model") or getattr(llm, "model", "")
            meta = pricing.get(mid) or {}
            event["model"] = mid
            event["context_length"] = event.get("context_length") or meta.get("context_length")
            event["prompt_price_per_1m"] = meta.get("price_prompt_per_1m", event.get("prompt_price_per_1m"))
            event["completion_price_per_1m"] = meta.get("price_completion_per_1m", event.get("completion_price_per_1m"))
            event["context_utilization"] = (
                round(event["input_tokens"] / event["context_length"], 4)
                if event.get("context_length") else None
            )
            row = model_rows.setdefault(mid, {
                "model": mid, "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "context_length": event.get("context_length"),
                "prompt_price_per_1m": event.get("prompt_price_per_1m"),
                "completion_price_per_1m": event.get("completion_price_per_1m"),
            })
            row["calls"] += 1
            row["input_tokens"] += event["input_tokens"]
            row["output_tokens"] += event["output_tokens"]
            row["cost_usd"] += event["cost_usd"]
            row["context_length"] = row["context_length"] or event.get("context_length")
        for row in model_rows.values():
            row["total_tokens"] = row["input_tokens"] + row["output_tokens"]
            row["avg_input_tokens"] = round(row["input_tokens"] / row["calls"]) if row["calls"] else 0
            row["avg_output_tokens"] = round(row["output_tokens"] / row["calls"]) if row["calls"] else 0
            row["cost_usd"] = round(row["cost_usd"], 8)

        summary["models"] = list(model_rows.values())
        summary["pricing_source"] = "openrouter /models" if pricing else "provider response / local fallback"
        summary["pricing_updated_at"] = cache.get("ts") if cache else None
        summary["active_model"] = getattr(llm, "model", "")
        return summary
