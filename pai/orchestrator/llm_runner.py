"""LLM call wrapper with usage tracking and fallback."""

from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

import json
import threading

from ..llm import LLMClient, LLMError
from ..usage import estimate_tokens

if TYPE_CHECKING:
    from .base import Orchestrator

_ITERATION_LIMIT_FALLBACK = "I've reached my tool iteration limit. Let me give you my best answer based on what I know."
_ITERATION_LIMIT_NUDGE = (
    "You have reached the tool-calling iteration limit. Do not request any "
    "more tools. Using only the information and tool results already "
    "available above, give your best final answer to the user now."
)


def _msg_text(m: dict) -> str:
    if m.get("tool_calls"):
        return json.dumps(m["tool_calls"], ensure_ascii=False)
    c = m.get("content")
    if isinstance(c, list):  # multimodal parts
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return str(c or "")


def _msg_images(m: dict) -> int:
    c = m.get("content")
    if isinstance(c, list):
        return sum(1 for p in c if isinstance(p, dict) and p.get("type") == "image_url")
    return 0


_IMG_TOK = 768


def build_request_breakdown(messages: list[dict], tools, system_segments: Optional[list[dict]],
                            phase: Optional[str]) -> dict:
    """Decompose the *actual* outgoing messages/tools into labelled parts.
    If system_segments (from ContextBuilder) are supplied and are a prefix
    of messages[0], they are reported exactly; whatever the runner appended
    after them (planner instruction + tool catalog) becomes its own part."""
    parts: list[dict] = []
    sys_text = _msg_text(messages[0]) if messages and messages[0].get("role") == "system" else ""
    rest = sys_text
    if system_segments:
        joined = "\n\n".join(sg["text"] for sg in system_segments)
        if sys_text.startswith(joined):
            for sg in system_segments:
                parts.append({"key": sg["key"], "label": sg["label"], "group": sg["group"],
                              "tokens_est": sg["tokens_est"], "chars": len(sg["text"]),
                              "meta": sg.get("meta") or {}, "text": sg["text"]})
            rest = sys_text[len(joined):].lstrip("\n")
    if rest.strip():
        key = "planner_instruction" if phase in ("planning", "analysis") else "system"
        label = {"planning": "Инструкция планировщика (planning)",
                 "analysis": "Инструкция анализа (analysis)"}.get(phase, "System prompt")
        parts.append({"key": key, "label": label, "group": "instructions",
                      "tokens_est": estimate_tokens(rest), "chars": len(rest), "meta": {}, "text": rest})

    # remaining conversation messages (index 0 was the system prompt)
    tail_start = 1 if sys_text or (messages and messages[0].get("role") == "system") else 0
    user_msgs = assistant_msgs = 0
    for m in messages[tail_start:]:
        role = m.get("role")
        txt = _msg_text(m)
        imgs = _msg_images(m)
        tok = estimate_tokens(txt) + imgs * _IMG_TOK
        if role == "user":
            first = txt.startswith("Results of the full pool execution:") or txt.startswith("Tool pool is empty") \
                or txt.startswith("You have reached the tool-calling")
            if txt.startswith("Results of the full pool execution:"):
                key, label, group = "tool_results", "Результаты пула инструментов", "tools"
            elif first:
                key, label, group = "nudge", "Служебная подсказка", "instructions"
            else:
                key, label, group = "user_message", "Сообщение пользователя", "user"
                user_msgs += 1
        elif role == "tool":
            key, label, group = "tool_results", "Результаты пула инструментов", "tools"
        elif role == "assistant":
            if m.get("tool_calls") or txt.startswith("Tool pool plan:"):
                key, label, group = "tool_plan", "План пула (ответ модели → контекст)", "tools"
            else:
                key, label, group = "assistant_message", "Сообщение ассистента", "history"
                assistant_msgs += 1
        else:
            key, label, group = f"msg_{role}", f"Сообщение ({role})", "other"
        parts.append({"key": key, "label": label, "group": group, "tokens_est": tok,
                      "chars": len(txt), "meta": {"images": imgs} if imgs else {}, "text": txt})

    if tools:
        tj = json.dumps(tools, ensure_ascii=False)
        parts.append({"key": "tool_schemas", "label": "Схемы инструментов (tools)", "group": "tools",
                      "tokens_est": estimate_tokens(tj), "chars": len(tj),
                      "meta": {"count": len(tools), "names": [(t.get("function") or {}).get("name") for t in tools]},
                      "text": ""})

    total_est = sum(p["tokens_est"] for p in parts)
    tail = next((p for p in parts if p["key"] == "tail"), None)
    return {
        "parts": parts,
        "total_tokens_est": total_est,
        "chat_history": {
            "messages_in_tail": (tail["meta"].get("count") if tail else 0) or 0,
            "user": (tail["meta"].get("user_msgs") if tail else 0) or 0,
            "assistant": (tail["meta"].get("assistant_msgs") if tail else 0) or 0,
            "tail_length_cfg": (tail["meta"].get("tail_length_cfg") if tail else None),
            "extra_messages_in_request": len(messages) - tail_start,
        },
        "instruction_keys": [p["key"] for p in parts if p["group"] == "instructions"],
    }


def calibrate_breakdown(breakdown: dict, usage: dict) -> dict:
    """Scale estimate → provider-reported prompt_tokens. Estimator is
    len//4, which under-counts Cyrillic; the factor is shown in the UI."""
    real = int((usage or {}).get("prompt_tokens") or 0)
    est = breakdown.get("total_tokens_est") or 0
    if real and est:
        k = real / est
        for p in breakdown["parts"]:
            p["tokens"] = round(p["tokens_est"] * k)
        breakdown["calibration"] = round(k, 3)
        breakdown["tokens_source"] = "provider-scaled"
    else:
        for p in breakdown["parts"]:
            p["tokens"] = p["tokens_est"]
        breakdown["calibration"] = None
        breakdown["tokens_source"] = "estimate"
    return breakdown


def sanitize_messages_for_log(messages: list[dict]) -> list[dict]:
    """Replace inline base64 images with a short placeholder so the action
    log (SQLite) does not balloon by megabytes per request."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    url = str((p.get("image_url") or {}).get("url") or "")
                    parts.append({"type": "image_url", "image_url": {"url": f"[image omitted, {len(url)} chars]"}})
                else:
                    parts.append(p)
            m = dict(m, content=parts)
        out.append(m)
    return out


class LLMRunner:
    """Wraps LLM calls with usage tracking, logging and graceful fallback.

    NOTE: this object deliberately does NOT cache ``orch.llm`` /
    ``orch.usage`` / ``orch.storage``. ``Orchestrator.reconfigure_llm()``
    swaps the client at runtime (Settings page); attributes are resolved
    through ``__getattr__`` on every access so the new client — new model,
    reasoning toggle, sampling params — takes effect immediately instead of
    after a restart."""

    def __init__(self, orch: "Orchestrator"):
        self.orch = orch

    def __getattr__(self, name):
        """Delegate unknown attributes to Orchestrator."""
        return getattr(self.orch, name)

    # ---------- streaming core ----------

    def _tracked_chat_stream(
        self,
        messages,
        tools=None,
        space_id: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        tool_choice=None,
        phase: Optional[str] = None,
        system_segments: Optional[list[dict]] = None,
        purpose: str = "chat",
    ):
        """Generator. Streams one LLM call, forwarding ``reasoning`` and
        ``content`` events as they arrive, while collecting everything needed
        for usage accounting and the action log. The generator *returns*
        (``result = yield from ...``) a dict shaped like ``LLMClient.chat``:
        {content, reasoning, tool_calls, usage, model, context_length,
        finish_reason}."""
        if system_segments is None:
            system_segments = getattr(getattr(self.orch, "_turn_local", None), "system_segments", None)
        llm = self.llm
        request_log = dict(llm.request_params())
        if temperature is not None:
            request_log["temperature"] = temperature
        if max_tokens is not None:
            request_log["max_tokens"] = max_tokens
        if top_p is not None:
            request_log["top_p"] = top_p
        if not tools:
            request_log.pop("tool_choice", None)
        request_log["messages"] = sanitize_messages_for_log(messages)
        request_log["tools"] = tools
        if tool_choice is not None:
            request_log["tool_choice"] = tool_choice
        if phase:
            request_log["phase"] = phase
        request_log["purpose"] = purpose
        breakdown = build_request_breakdown(messages, tools, system_segments, phase)
        request_log["breakdown"] = breakdown

        t0 = time.time()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict] = []
        usage: dict = {}
        model = getattr(llm, "model", "") or ""
        context_length = getattr(llm, "context_length", None)
        finish_reason = None
        try:
            for ev in llm.chat_stream(
                messages=messages, tools=tools, temperature=temperature,
                max_tokens=max_tokens, top_p=top_p, tool_choice=tool_choice,
            ):
                et = ev.get("type")
                if et == "reasoning":
                    reasoning_parts.append(ev["text"])
                    yield ev
                elif et == "content":
                    content_parts.append(ev["text"])
                    yield ev
                elif et == "tool_calls":
                    tool_calls = ev.get("tool_calls") or []
                elif et == "usage":
                    usage = ev.get("usage") or usage
                    model = ev.get("model") or model
                    context_length = ev.get("context_length") or context_length
                elif et == "finish":
                    finish_reason = ev.get("finish_reason")
        except GeneratorExit:
            raise
        except Exception as e:
            self.storage.log_action(
                "llm_request", model or "llm", request=request_log, space_id=space_id,
                latency_seconds=time.time() - t0, error=str(e),
                response={"content": "".join(content_parts) or None,
                          "reasoning": "".join(reasoning_parts) or None, "partial": True},
            )
            raise

        latency = time.time() - t0
        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts) or None
        calibrate_breakdown(breakdown, usage)
        if usage:
            self.usage.record_api(
                "chat", usage, latency, model=model, context_length=context_length,
                tool_calls=len(tool_calls),
            )
        else:
            input_text = "\n".join(str(m.get("content") or "") for m in messages)
            self.usage.record("chat", input_text, content + (reasoning or ""), len(tool_calls), latency)
        self.storage.log_action(
            "llm_request", model or "llm", request=request_log,
            response={
                "content": content, "reasoning": reasoning, "tool_calls": tool_calls or None,
                "usage": usage, "finish_reason": finish_reason,
                "tokens": {
                    "context": int(usage.get("prompt_tokens") or breakdown["total_tokens_est"]),
                    "completion": int(usage.get("completion_tokens") or estimate_tokens(content)),
                    "reasoning": int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                    "cached": int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                    "source": "provider" if usage else "estimate",
                },
                "context_length": context_length,
            },
            space_id=space_id, latency_seconds=latency,
        )
        return {
            "content": content, "reasoning": reasoning, "tool_calls": tool_calls,
            "usage": usage, "model": model, "context_length": context_length,
            "finish_reason": finish_reason,
        }

    def _tracked_chat(self, messages, tools=None, **kw):
        """Non-streaming convenience wrapper: drains the stream and returns
        the final result."""
        gen = self._tracked_chat_stream(messages, tools=tools, **kw)
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            return stop.value
