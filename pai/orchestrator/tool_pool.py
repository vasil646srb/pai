"""Tool pool planning and execution: plan → pool execution → analysis."""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING

from ..models import MemoryNode, NodeType
from ..prompts import load_prompt

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .base import Orchestrator

MAX_TOOL_POOL_SIZE = 8
_TEXT_PLAN_RE = re.compile(r"^\s*Tool pool plan:\s*(\[.*\])\s*$", re.DOTALL)
# Some models write a call as text, one per line: name({"arg": ...}). Only names the
# persona may use are executed (normalization checks that); the text is removed from the reply.
_TEXT_CALL_RE = re.compile(r"(?m)^\s*([A-Za-z_]\w*)\s*\((\{.*\})\)\s*$")
# Some models write the planning call as markup in the text instead of a real call:
#   <tool_call>plan_tool_pool<arg_key>calls</arg_key><arg_value>[...]</arg_value></tool_call>
_MARKUP_PLAN_RE = re.compile(
    r"<tool_call>\s*plan_tool_pool\s*<arg_key>\s*calls\s*</arg_key>\s*<arg_value>(.*?)</arg_value>\s*(?:</tool_call>)?",
    re.DOTALL,
)

DESCRIBE_TOOL_NAME = "describe_tools"
MAX_DESCRIBE_STEPS = 2   # describe rounds inside one planning call; they do not use up a tool round
MAX_DESCRIBE_NAMES = 12


def _purpose(description: str, limit: int = 120) -> str:
    """First sentence of a tool description, for the compact catalog."""
    text = " ".join(str(description or "").split())
    cut = text.find(". ")
    if 0 < cut < limit:
        text = text[: cut + 1]
    return text[:limit]


def extract_describe_request(result: dict) -> tuple[list[str], str] | None:
    """(names, call_id) when the model asked for schemas and did not plan a pool in the same reply."""
    calls = result.get("tool_calls") or []
    if any(tc.get("name") == "plan_tool_pool" for tc in calls):
        return None
    call = next((tc for tc in calls if tc.get("name") == DESCRIBE_TOOL_NAME), None)
    if call is None:
        return None
    names = (call.get("arguments") or {}).get("names")
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except json.JSONDecodeError:
            names = [names]
    if not isinstance(names, list):
        names = []
    clean: list[str] = []
    for n in names:
        s = str(n).strip()
        if s and s not in clean:
            clean.append(s)
    return clean[:MAX_DESCRIBE_NAMES], str(call.get("id") or f"describe-{len(calls)}")


def describe_payload(names: list[str], tool_schemas: list[dict]) -> dict:
    """Full schemas for the requested names. tool_schemas is already the persona's filtered list,
    so a tool the persona may not use is reported as not_available and is never described."""
    by_name = {s.get("function", {}).get("name"): s.get("function", {}) for s in tool_schemas}
    tools, missing = [], []
    for n in names:
        fn = by_name.get(n)
        if fn is None:
            missing.append(n)
            continue
        tools.append({"name": n, "description": fn.get("description", ""), "parameters": fn.get("parameters", {})})
    return {"tools": tools, "not_available": missing}


class ToolPoolPlanner:
    """Plans and executes tool pools according to the protocol:
    planning → pool execution → analysis."""

    def __init__(self, orch: Orchestrator):
        self.orch = orch

    def __getattr__(self, name):
        """Delegate unknown attributes to Orchestrator."""
        return getattr(self.orch, name)

    def _persist_tool_result(
        self,
        tool_name: str,
        arguments: dict,
        result: dict,
        space_id: str,
        occurred_at: datetime,
    ) -> MemoryNode:
        """Persists the exact skill/tool result into long-term memory."""
        args_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        content = (
            "[Skill/tool execution result]\n"
            f"Tool: {tool_name}\n"
            f"Arguments: {args_json}\n"
            f"Result: {result_json}"
        )
        return self.tree.ingest_raw(
            content=content,
            space_id=space_id,
            node_type=NodeType.SKILL,
            occurred_at=occurred_at.timestamp(),
            tags=["tool_result", "skill_result", f"tool:{tool_name}"],
        )

    @staticmethod

    def _run_single_tool(dispatcher, tool_call, persona):
        """Execute one tool call and return a stable result tuple.

        ToolDispatcher currently exposes a synchronous API, so the pool uses
        worker threads rather than forcing an async rewrite through the whole
        application. Exceptions are isolated per tool call.
        """
        t0 = time.time()
        try:
            result = dispatcher.dispatch(
                tool_call["name"], tool_call["arguments"], persona=persona
            )
            error = None
        except Exception as exc:  # per-call isolation: the model gets the error, the log gets the traceback
            _log.exception("tool %s crashed", tool_call["name"])
            result = {
                "tool_failed": True,
                "error": str(exc),
                "tool": tool_call["name"],
            }
            error = str(exc)
        return tool_call, result, time.time() - t0, error


    def _execute_tool_pool(self, dispatcher, tool_calls, persona):
        """Run all tool calls from one LLM response concurrently.

        The returned list keeps the original tool-call order, but the method
        does not return until every submitted call has completed. Consequently
        the caller can safely perform exactly one subsequent LLM request with
        the complete batch of tool results.
        """
        if not tool_calls:
            return []

        # Bound the pool so a malformed/model-generated batch cannot create an
        # unbounded number of worker threads.
        max_workers = min(len(tool_calls), 8)
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="pai-tool",
        ) as executor:
            futures = [
                executor.submit(self._run_single_tool, dispatcher, tc, persona)
                for tc in tool_calls
            ]
            # Calling result() in submission order preserves deterministic
            # message ordering; the executor still runs all calls concurrently.
            return [future.result() for future in futures]


    def _tool_catalog_block(self, tool_schemas: list[dict]) -> str:
        """Compact catalog for the planner: name and purpose only. Parameter schemas come from describe_tools."""
        lines = []
        for schema in sorted(tool_schemas, key=lambda s: s.get("function", {}).get("name", "")):
            fn = schema.get("function", {})
            lines.append(f"- {fn.get('name')}: {_purpose(fn.get('description', ''))}")
        return "\n".join(lines)

    def _planner_messages(self, messages: list[dict], tool_schemas: list[dict], phase: str) -> list[dict]:
        if phase == "planning":
            instruction = load_prompt("planner_first_pass")
        else:
            instruction = load_prompt("planner_analysis")
        instruction += (
            "\n\nTOOL CATALOG (name: purpose). Parameter schemas are not shown here. "
            "Before using a tool in plan_tool_pool, call describe_tools with its names unless its schema "
            "already appears in this conversation. describe_tools runs no tool.\n"
            + self._tool_catalog_block(tool_schemas)
        )
        instruction = instruction.replace("{max_rounds}", str(self.max_tool_iterations))
        result = [dict(m) for m in messages]
        result[0] = {"role": "system", "content": (result[0].get("content") or "") + "\n\n" + instruction}
        return result

    def _extract_tool_pool(self, result: dict) -> tuple[list[dict] | None, str | None]:
        """Return (pool, error). Supports the new plan_tool_pool call and
        legacy direct tool_calls so old MockLLM/tests remain compatible."""
        calls = result.get("tool_calls") or []
        plan_call = next((tc for tc in calls if tc.get("name") == "plan_tool_pool"), None)
        if plan_call is not None:
            args = plan_call.get("arguments") or {}
            pool = args.get("calls")
            if isinstance(pool, str):
                try:
                    pool = json.loads(pool)
                except json.JSONDecodeError:
                    pass
            if not isinstance(pool, list):
                return None, "plan_tool_pool.calls must be an array"
            return pool, None
        if not calls:
            # Fallback: some models write the plan as plain text
            # ("Tool pool plan: [...]") instead of making the function call.
            content = result.get("content") or ""
            m = _TEXT_PLAN_RE.match(content)
            if m:
                try:
                    pool = json.loads(m.group(1))
                    if isinstance(pool, list) and pool:
                        return pool, None
                except json.JSONDecodeError:
                    pass
            # ...or as <tool_call> markup inside the reply. Take the plan out of the
            # visible text so the user sees the answer, not the call syntax.
            m = _MARKUP_PLAN_RE.search(content)
            if m:
                try:
                    pool = json.loads(m.group(1).strip())
                except json.JSONDecodeError:
                    pool = None
                if isinstance(pool, list) and pool:
                    result["content"] = (content[:m.start()] + content[m.end():]).strip()
                    return pool, None
            text_calls = []
            for tm in _TEXT_CALL_RE.finditer(content):
                try:
                    args = json.loads(tm.group(2))
                except json.JSONDecodeError:
                    continue
                if isinstance(args, dict):
                    text_calls.append({"name": tm.group(1), "arguments": args})
            if text_calls:
                result["content"] = _TEXT_CALL_RE.sub("", content).strip()
                return text_calls, None
        if calls:
            # Backward-compatible interpretation for providers/models that
            # still emit ordinary tool calls despite the planning schema.
            return [
                {"name": tc.get("name", ""), "arguments": tc.get("arguments") or {}, "id": tc.get("id", ""), "_legacy": True}
                for tc in calls
            ], None
        return None, None

    @staticmethod

    def _normalize_pool(pool: list[dict], tool_schemas: list[dict], max_size: int = MAX_TOOL_POOL_SIZE) -> list[dict]:
        allowed = {s.get("function", {}).get("name") for s in tool_schemas}
        normalized = []
        for item in pool[:max_size]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            args = item.get("arguments")
            if isinstance(args, str):  # models often double-encode nested arguments
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = None
            # Never let legacy/direct tool-call compatibility bypass persona
            # permissions. Every executable call must be present in the
            # current persona's filtered schema.
            if name not in allowed or not isinstance(args, dict):
                continue
            normalized.append({
                "id": str(item.get("id") or f"pool-{len(normalized)+1}"),
                "name": name,
                "arguments": args,
            })
        return normalized


    def _record_tool_results(self, executed, tool_trace, space_id, reference_now, on_event=None):
        for tc, tool_result, latency, tool_error in executed:
            self.storage.log_action(
                "tool_call", tc["name"], request=tc["arguments"],
                response=tool_result, space_id=space_id,
                latency_seconds=latency, error=tool_error,
            )
            entry = {"name": tc["name"], "arguments": tc["arguments"], "result": tool_result}
            tool_trace.append(entry)
            self._persist_tool_result(
                tc["name"], tc["arguments"], tool_result,
                space_id=space_id, occurred_at=reference_now,
            )
            if on_event:
                on_event({"type": "tool_result", **entry})


