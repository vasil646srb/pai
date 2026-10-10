"""Dialog loop.

There is exactly ONE code path for a turn: ``handle_turn_stream`` (a
generator of UI events). ``handle_turn`` (the non-streaming /chat endpoint)
simply drains it. Every LLM call — planning, analysis and the final answer —
is streamed, so the model's reasoning and text reach the UI live, and the
reasoning of *all* phases is collected and persisted with the answer.

Event types yielded by ``handle_turn_stream``:
    reasoning_delta  {text}            chain-of-thought chunk
    delta            {text}            answer chunk
    delta_reset      {}                discard answer text streamed so far
    check_failed     {text, report, attempt}  an answer that failed the check; shown only in reasoning
                                       (the model turned out to want a tool)
    tool_call        {name, arguments}
    tool_result      {name, arguments, result}
    tool_trace       {trace}
    cache_status     {status}
    done             {reply, reasoning, tool_trace, live_context_preview,
                      usage, turn_usage, cache_status}
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING

from ..llm import LLMError
from ..models import NodeType, Persona
from ..prompts import load_prompt
from ..time_utils import normalize_relative_dates
from ..tools import TOOL_PLAN_SCHEMA, ToolDispatcher, ToolError
from ..usage import estimate_tokens
from .base import _build_user_content
from .llm_runner import _ITERATION_LIMIT_FALLBACK, _ITERATION_LIMIT_NUDGE

if TYPE_CHECKING:
    from .base import Orchestrator, TurnResult


_IMAGE_TOKEN_ESTIMATE = 768  # rough per-image token cost for the preview estimate
_TOOL_RESULT_MODEL_CHARS = 12000  # per tool result fed back to the model

_EMPTY_LENGTH_HINT = (
    "⚠ Ответ пуст: модель упёрлась в лимит max_tokens (на reasoning ушёл весь бюджет). "
    "Увеличьте max_tokens в Настройках → LLM или снизьте reasoning effort."
)
_EMPTY_HINT = "⚠ Модель вернула пустой ответ. Повторите запрос или проверьте настройки модели."



_ANNOUNCED_ACTION_RE = re.compile(
    r"(прочита\w*|проверю|проверим|посмотр\w*|открою|запущу|выполню|сделаю|найду|"
    r"\bI(?:'ll| will)\s+(?:read|check|look|run|open|search|find|review))",
    re.IGNORECASE,
)


def _announces_unfinished_action(text: str) -> bool:
    """Heuristic: a short reply that says the model is about to act, but makes no tool call.
    Used to give the model one chance to make the call it announced."""
    t = text.strip()
    return 0 < len(t) <= 500 and bool(_ANNOUNCED_ACTION_RE.search(t)) and t[-1:] in ".:…!"



_log = logging.getLogger(__name__)

_VISION_SYSTEM = (
    load_prompt("vision_system")
)

_VERIFY_SYSTEM = (
    load_prompt("verify_system")
)

# Answer-check strictness: level -> (description for the checker, minimum usefulness to pass).
_STRICTNESS_LEVELS = {
    1: ("very lenient: accept any reasonable attempt that addresses the message", 3),
    2: ("lenient: accept a reply that addresses the message even if it is imperfect", 4),
    3: ("normal: the reply must address the message and be concretely useful", 5),
    4: ("strict: the reply must fully address the message, be specific and complete", 6),
    5: ("very strict: any gap, vagueness or unrequested omission fails the reply", 7),
}


def _strictness_level(value) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


def _user_language_hint(text: str) -> str:
    """Deterministic hint for the checker: Cyrillic-dominant text is Russian; otherwise the model
    must detect the language from the user's message."""
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return "the language of the user's last message (detect it)"
    cyr = sum(1 for ch in letters if "\u0400" <= ch <= "\u04FF")
    if cyr / len(letters) > 0.5:
        return "Russian"
    return "the language of the user's last message (detect it)"
CHECK_FAILED_NOTICE = "⚠ Ответ не прошёл проверку. Текст ответа — в разделе размышлений."
# Texts the app itself writes into the chat. Stored with the "notice" tag: visible in history,
# but never sent back to the model as its own earlier words.
_SYSTEM_NOTICES = frozenset({_ITERATION_LIMIT_FALLBACK, _EMPTY_HINT, _EMPTY_LENGTH_HINT, CHECK_FAILED_NOTICE})
def event_tags(origin: dict | None, role: str) -> list[str]:
    """Tags for a stored dialogue node. Service runs get their own tags so they are never
    mistaken for something the user typed."""
    if not origin or origin.get("kind") != "service":
        return [role]
    name = str(origin.get("name") or "")[:80]
    return [role, "service", f"service:{origin.get('id')}", f"service-name:{name}"]


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


_MAX_CHECKS_PER_TURN = 2  # the answer check runs at most twice per turn


class _TurnState:
    """Per-turn accumulator shared by all LLM phases of one turn."""

    def __init__(self) -> None:
        self.reasoning_parts: list[str] = []
        self.verdicts: list[dict] = []   # answer checks made during this turn

    @property
    def reasoning(self) -> str | None:
        text = "".join(self.reasoning_parts).strip()
        return text or None


class DialogLoop:
    """Orchestrates the full dialog cycle using Orchestrator components."""

    def __init__(self, orch: Orchestrator):
        self.orch = orch

    def __getattr__(self, name):
        """Delegate unknown attributes to Orchestrator."""
        return getattr(self.orch, name)

    # ------------------------------------------------------------------
    # one streamed LLM call
    # ------------------------------------------------------------------

    def _llm_phase(self, state: _TurnState, messages, tools, phase, space_id, segments, tool_choice=None):
        """Generator: streams one LLM call, translating runner events into UI
        events and recording reasoning into ``state``. Returns the runner's
        result dict plus ``streamed_content`` (bool)."""
        gen = self._tracked_chat_stream(
            messages, tools=tools, tool_choice=tool_choice, phase=phase,
            space_id=space_id, system_segments=segments,
        )
        started_reasoning = False
        streamed_content = False
        try:
            while True:
                ev = next(gen)
                if ev["type"] == "reasoning":
                    text = ev["text"]
                    if not started_reasoning:
                        started_reasoning = True
                        if state.reasoning_parts:  # visual separator between phases
                            text = "\n\n" + text
                    state.reasoning_parts.append(text)
                    yield {"type": "reasoning_delta", "text": text}
                elif ev["type"] == "content":
                    streamed_content = True
                    yield {"type": "delta", "text": ev["text"]}
        except StopIteration as stop:
            result = stop.value
        result["streamed_content"] = streamed_content
        return result

    def _forced_final(self, state: _TurnState, phase_messages, space_id, segments):
        """Last tools-free call when the loop can't continue. Returns
        (content, streamed, finish_reason). Never raises LLMError."""
        nudged = phase_messages + [{"role": "user", "content": _ITERATION_LIMIT_NUDGE}]
        try:
            # tools stay declared (history contains tool calls; some providers
            # reject that without a tools list) but tool_choice="none" forbids calling.
            r = yield from self._llm_phase(
                state, nudged, [TOOL_PLAN_SCHEMA], "final", space_id, segments, tool_choice="none",
            )
        except LLMError:
            return _ITERATION_LIMIT_FALLBACK, False, None
        content = (r.get("content") or "").strip()
        if content:
            return content, True, r.get("finish_reason")
        if r.get("finish_reason") == "length":
            return _EMPTY_LENGTH_HINT, False, "length"
        return _ITERATION_LIMIT_FALLBACK, False, r.get("finish_reason")

    # ------------------------------------------------------------------
    # plan -> execute complete pool -> analyze
    # ------------------------------------------------------------------

    @staticmethod
    def _clip_for_model(result):
        s = json.dumps(result, ensure_ascii=False, default=str)
        if len(s) <= _TOOL_RESULT_MODEL_CHARS:
            return result
        return {"truncated": True, "original_chars": len(s), "preview": s[:_TOOL_RESULT_MODEL_CHARS]}

    def _record_side_call(self, purpose: str, model: str, space_id: str, latency: float,
                          usage: dict, messages: list, text: str, resp_model: str = "") -> None:
        """Account for an auxiliary model call (checker, vision) in usage and the request log."""
        if self.usage is not None:
            if usage:
                self.usage.record_api(purpose, usage, latency, model=model)
            else:
                self.usage.record(purpose, "\n".join(str(m.get("content") or "") for m in messages),
                                  text, 0, latency)
        try:
            self.storage.log_action(
                "llm_request", model, request={
                    "purpose": purpose, "phase": purpose, "temperature": None,
                    "messages": messages,
                }, space_id=space_id, latency_seconds=latency,
                response={"content": text, "usage": usage, "model": resp_model or model},
            )
        except Exception as e:  # logging must never block the answer
            _log.warning("%s log failed: %s", purpose, e, exc_info=True)

    def _describe_images(self, space_id: str, user_text: str, images: list[str]) -> str | None:
        """Ask the vision model for a text description of the images. Returns None when
        the call fails, so the caller can fall back to sending the images to the main model."""
        msgs = [{"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": _build_user_content(
                    f"User's text: {user_text or '(no text)'}", images)}]
        model = getattr(self.vision_llm, "model", "?")
        t0 = time.time()
        try:
            raw = self.vision_llm.chat(messages=msgs, temperature=0.2, max_tokens=800)
        except Exception as e:  # the vision model is optional: any failure falls back to the main model
            _log.warning("image description failed, images go to the main model: %s", e, exc_info=True)
            return None
        latency = time.time() - t0
        text = (raw.get("content") or "").strip()
        self._record_side_call("image_describe", model, space_id, latency, raw.get("usage") or {},
                               msgs, text, raw.get("model") or "")
        if not text:
            _log.warning("image description came back empty, images go to the main model")
            return None
        _log.info("image description [%s] model=%s: %d chars", space_id, model, len(text))
        return text

    def _verify_answer(self, space_id: str, user_text: str, answer: str) -> dict | None:
        """Ask the small model whether `answer` addresses the user's last message.
        Returns {"ok": bool, "report": str}, or None when the check itself failed
        (then the answer is not blocked)."""
        if self.verifier_llm is None or not answer.strip():
            return None
        nodes = self.storage.get_chat_messages(space_id=space_id, limit=4)
        dialog = "\n\n".join(
            f"{'Assistant' if 'assistant' in (n.tags or []) else ('Service task' if 'service' in (n.tags or []) else 'User')}: {n.raw_content}"
            for n in nodes
        ) or "(empty)"
        level = _strictness_level(getattr(self, "verify_strictness", 3))
        prompt = (f"Recent dialogue messages:\n{dialog}\n\n"
                  f"User's last message:\n{user_text}\n\n"
                  f"Assistant's new reply:\n{answer}\n\n"
                  f"Check strictness: level {level} of 5, {_STRICTNESS_LEVELS[level][0]}. "
                  f"User's language: {_user_language_hint(user_text)}. "
                  f"Write suggested_replies, report and missing in that language.")
        try:
            model = getattr(self.verifier_llm, "model", "?")
            if self.verifier_llm is self.llm:
                _log.info("answer check uses the main model (%s): pick a smaller one in Settings → Модель для проверки ответа", model)
            verify_msgs = [{"role": "system", "content": _VERIFY_SYSTEM},
                           {"role": "user", "content": prompt}]
            t0 = time.time()
            raw = self.verifier_llm.chat(
                messages=verify_msgs, temperature=0.0, max_tokens=400,
            )
            latency = time.time() - t0
            text = (raw.get("content") or "").strip()
            m = _JSON_OBJ_RE.search(text)
            data = json.loads(m.group(0)) if m else None
        except Exception as e:  # check failed: do not block the user's answer
            _log.warning("answer check failed: %s", e, exc_info=True)
            return None
        self._record_side_call("answer_check", model, space_id, latency, raw.get("usage") or {},
                               verify_msgs, text, raw.get("model") or "")
        if not isinstance(data, dict) or "ok" not in data:
            _log.warning("answer check returned no verdict: %r", text[:200])
            return None
        try:
            usefulness = max(0, min(10, int(float(data.get("usefulness")))))
        except (TypeError, ValueError):
            usefulness = None
        replies = data.get("suggested_replies") or []
        if not isinstance(replies, list):
            replies = []
        replies = [str(r).strip()[:300] for r in replies if str(r).strip()][:2]
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        min_useful = _STRICTNESS_LEVELS[level][1]
        passed = bool(data["ok"]) and (usefulness is None or usefulness >= min_useful)
        verdict = {
            "ok": passed,
            "strictness": level,
            "min_usefulness": min_useful,
            "report": str(data.get("report") or "").strip()[:1000],
            "usefulness": usefulness,
            "confidence": confidence,
            "missing": str(data.get("missing") or "").strip()[:300],
            "suggested_replies": replies,
            "user_language": str(data.get("user_language") or "").strip()[:40] or None,
        }
        if not passed and not verdict["report"]:
            verdict["report"] = (f"usefulness {usefulness}/10 is below the {min_useful}/10 required "
                                 f"at strictness level {level}")
        _log.info("answer check [%s] model=%s: ok=%s useful=%s/10 lang=%s confidence=%s%s", space_id, model,
                  verdict["ok"], usefulness, verdict["user_language"], confidence,
                  "" if verdict["ok"] else f" report={verdict['report']!r}")
        return verdict

    def _verify_and_revise(self, state, persona, messages, tool_schemas, dispatcher, now, space_id,
                           segments, tool_trace, final_content, streamed, user_text):
        """Generator. Checks the final answer; on a failed check sends the report back to the
        main model for a rewrite, at most PAI_VERIFY_REVISIONS times. Returns the final
        (tool_trace, content, streamed)."""
        if not self.verify_enabled:
            _log.info("answer check disabled (PAI_VERIFY=0)")
            return tool_trace, final_content, streamed
        if self.verifier_llm is None:
            _log.info("answer check skipped: no checker model configured")
            return tool_trace, final_content, streamed
        revisions = self.verify_revisions
        msgs = messages
        checks = min(revisions + 1, _MAX_CHECKS_PER_TURN)
        for attempt in range(checks):
            verdict = self._verify_answer(space_id, user_text, final_content)
            if verdict is None:
                break
            state.verdicts.append({**verdict, "attempt": attempt + 1})
            if verdict["ok"]:
                break
            # A failed answer never stays in the chat: its text goes to the reasoning section.
            yield {"type": "check_failed", "text": final_content, "report": verdict["report"],
                   "attempt": attempt + 1}
            if attempt == checks - 1:
                final_content, streamed = CHECK_FAILED_NOTICE, False
                break
            _log.info("answer revision %d/%d requested", attempt + 1, revisions)
            feedback = (f"The answer check failed: {verdict['report']}\n"
                        "Rewrite the reply to the user's last message taking these remarks into account, "
                        "in the user's language. Use tools if needed.")
            msgs = msgs + [{"role": "assistant", "content": final_content},
                           {"role": "user", "content": feedback}]
            yield {"type": "delta_reset"}
            new_trace, new_content, new_streamed = yield from self._tool_loop(
                state, persona, msgs, tool_schemas, dispatcher, now, space_id, segments,
            )
            tool_trace = tool_trace + new_trace
            final_content, streamed = new_content, new_streamed
        return tool_trace, final_content, streamed

    def _tool_loop(
        self,
        state: _TurnState,
        persona: Persona,
        messages: list[dict],
        tool_schemas: list[dict],
        dispatcher: ToolDispatcher,
        reference_now: datetime,
        space_id: str,
        segments: list[dict],
    ):
        """Generator. The LLM is contacted once to create a pool, then not
        contacted again until every tool in that pool has completed. The
        analysis call may create the next pool.

        Returns (tool_trace, final_content, already_streamed)."""
        tool_trace: list[dict] = []
        phase_messages = messages
        seen_calls: set[str] = set()
        nudged = False

        for iteration in range(self.max_tool_iterations):
            phase = "planning" if iteration == 0 else "analysis"
            planner_messages = self._planner_messages(phase_messages, tool_schemas, phase)
            result = yield from self._llm_phase(
                state, planner_messages, [TOOL_PLAN_SCHEMA], phase, space_id, segments, tool_choice="auto",
            )
            content = (result.get("content") or "").strip()
            pool, plan_error = self._extract_tool_pool(result)

            if plan_error:
                if result["streamed_content"]:
                    yield {"type": "delta_reset"}
                text, streamed, _ = yield from self._forced_final(state, phase_messages, space_id, segments)
                return tool_trace, text, streamed

            if pool is None:
                # The model said it would act but made no call: give it one chance.
                if (content and not nudged and iteration + 1 < self.max_tool_iterations
                        and _announces_unfinished_action(content)):
                    nudged = True
                    if result["streamed_content"]:
                        yield {"type": "delta_reset"}
                    phase_messages = phase_messages + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": "You said you would act, but you did not call "
                                                    "plan_tool_pool. Call it now with the calls you need, "
                                                    "or give the final answer if no tool is needed."},
                    ]
                    continue
                # No tools requested: this is the final answer (already streamed).
                if content:
                    return tool_trace, content, True
                if result.get("finish_reason") == "length":
                    return tool_trace, _EMPTY_LENGTH_HINT, False
                text, streamed, _ = yield from self._forced_final(state, phase_messages, space_id, segments)
                return tool_trace, (text if streamed or text != _ITERATION_LIMIT_FALLBACK else _EMPTY_HINT), streamed

            pool = self._normalize_pool(pool, tool_schemas)
            fresh = []
            for tc in pool:
                key = json.dumps({"name": tc["name"], "arguments": tc["arguments"]},
                                 ensure_ascii=False, sort_keys=True, default=str)
                if key not in seen_calls:
                    fresh.append((tc, key))
            pool = [tc for tc, _ in fresh]
            seen_calls.update(key for _, key in fresh)

            if not pool:
                # Empty/duplicate pool: the model decided it has enough information.
                if content:
                    return tool_trace, content, True
                if result["streamed_content"]:
                    yield {"type": "delta_reset"}
                if iteration + 1 >= self.max_tool_iterations:
                    break
                phase_messages = phase_messages + [{
                    "role": "user",
                    "content": "Tool pool is empty. If the task is already solved, give the answer; "
                               "otherwise build the next pool.",
                }]
                continue

            # The model wants tools: whatever text it streamed was only a preamble.
            if result["streamed_content"]:
                yield {"type": "delta_reset"}

            for tc in pool:
                yield {"type": "tool_call", "name": tc["name"], "arguments": tc["arguments"]}
            executed = self._execute_tool_pool(dispatcher, pool, persona)
            self._record_tool_results(executed, tool_trace, space_id, reference_now)
            for tc, tool_result, _latency, _tool_error in executed:
                yield {"type": "tool_result", "name": tc["name"], "arguments": tc["arguments"], "result": tool_result}

            results_json = json.dumps(
                [{"name": tc["name"], "arguments": tc["arguments"],
                  "result": self._clip_for_model(tool_result), "error": tool_error}
                 for tc, tool_result, latency, tool_error in executed],
                ensure_ascii=False, default=str,
            )
            # Record the round with the REAL tool-calling protocol (assistant
            # tool_calls + tool result). The old text form ("Tool pool plan: [...]"
            # as an assistant message) taught the model to imitate it as plain
            # text, so the "plan" itself showed up as the final answer.
            call_id = f"plan_{iteration}"
            plan_args = json.dumps(
                {"calls": [{"name": tc["name"], "arguments": tc["arguments"]} for tc in pool]},
                ensure_ascii=False,
            )
            phase_messages = phase_messages + [
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "plan_tool_pool", "arguments": plan_args},
                }]},
                {"role": "tool", "tool_call_id": call_id, "content": results_json},
            ]

        text, streamed, _ = yield from self._forced_final(state, phase_messages, space_id, segments)
        return tool_trace, text, streamed

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def handle_turn(
        self,
        user_text: str,
        persona_id: str,
        space_id: str = "personal",
        reference_now: datetime | None = None,
        images: list[str] | None = None,
        origin: dict | None = None,
    ) -> TurnResult:
        """Non-streaming wrapper: drains handle_turn_stream."""
        from .base import TurnResult  # lazy: base imports this module
        done = None
        for ev in self.handle_turn_stream(user_text, persona_id, space_id, reference_now, images, origin):
            if ev["type"] == "done":
                done = ev
        if done is None:  # pragma: no cover - generator always ends with "done"
            raise RuntimeError("Turn finished without a result")
        return TurnResult(
            reply=done["reply"],
            reasoning=done.get("reasoning"),
            tool_trace=done["tool_trace"],
            live_context_preview=done["live_context_preview"],
            usage=done["usage"],
            cache_status=done.get("cache_status") or {},
        )

    def handle_turn_stream(
        self,
        user_text: str,
        persona_id: str,
        space_id: str = "personal",
        reference_now: datetime | None = None,
        images: list[str] | None = None,
        origin: dict | None = None,
    ):
        """origin: None for a user turn; {"kind": "service", "id", "name"} for an autonomous
        service run. Service turns are stored as separate events, not as user messages."""
        persona = self.personas.get(persona_id)
        if persona is None:
            raise KeyError(f"Unknown persona: {persona_id}")
        if not persona.memory_filter.permits(space_id, []):
            raise ToolError(f"Persona '{persona_id}' does not have access to space '{space_id}'")

        self._turn_started()
        try:
            tctx = self._build_time_context(reference_now)
            annotations = normalize_relative_dates(user_text, tctx.now)

            # Build the prompt BEFORE ingesting the new message: the chat tail
            # is rendered into the system prompt, and the user's message is
            # also sent as the user turn — ingesting first duplicated it.
            system_segments = self.build_system_segments(persona, space_id, reference_now=reference_now)
            system_prompt = "\n\n".join(sg["text"] for sg in system_segments)

            # Memory stores text only — images ride along in the live LLM
            # messages but aren't persisted as raw bytes.
            # Images go to the vision model first (if configured); its description replaces
            # them in the main model's input. On failure the images go to the main model.
            description = None
            if images and self.vision_llm is not None:
                description = self._describe_images(space_id, user_text, images)
            llm_user_text = user_text
            llm_images = images
            if description:
                llm_user_text = f"{user_text}\n\n[Image description: {description}]".strip()
                llm_images = None
            memory_text = user_text
            if description:
                memory_text = f"{user_text}\n\n[Image: {description}]".strip()
            elif images:
                memory_text = f"{user_text}\n\n[Attached images: {len(images)}]".strip()
            self.tree.ingest_raw(
                content=memory_text,
                space_id=space_id,
                node_type=NodeType.IMAGE if (images and not user_text) else NodeType.TEXT,
                occurred_at=tctx.now.timestamp(),
                tags=event_tags(origin, "user"),
                annotations=[
                    {"raw_phrase": a.raw_phrase, "resolved_date": a.resolved_date, "confidence": a.confidence}
                    for a in annotations
                ],
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _build_user_content(llm_user_text, llm_images)},
            ]
            dispatcher = ToolDispatcher(
                self.storage, reference_now=tctx.now, custom_functions=self.custom_functions,
                llm=self.llm, rules_manager=self.rules, skills=self.skills,
                persona_registry=self.personas,
            )
            tool_schemas = dispatcher.schemas_for_persona(persona)

            usage_events_before = len(self.usage.events)
            state = _TurnState()
            tool_trace, final_content, streamed = yield from self._tool_loop(
                state, persona, messages, tool_schemas, dispatcher, tctx.now, space_id, system_segments,
            )
            tool_trace, final_content, streamed = yield from self._verify_and_revise(
                state, persona, messages, tool_schemas, dispatcher, tctx.now, space_id, system_segments,
                tool_trace, final_content, streamed, user_text,
            )
            if not streamed:
                yield {"type": "delta_reset"}
                if final_content:
                    yield {"type": "delta", "text": final_content}
            final_reasoning = state.reasoning

            yield {"type": "tool_trace", "trace": tool_trace}

            self.tree.ingest_raw(
                content=final_content,
                space_id=space_id,
                node_type=NodeType.TEXT,
                occurred_at=tctx.now.timestamp(),
                tags=event_tags(origin, "assistant") + (["notice"] if final_content in _SYSTEM_NOTICES else []),
                reasoning=final_reasoning,
            )

            cache_status = self._check_cache_and_maybe_compress(persona, space_id, system_prompt, user_text)
            if cache_status.get("auto_compressed"):
                yield {"type": "cache_status", "status": cache_status}

            image_tokens_est = _IMAGE_TOKEN_ESTIMATE * len(llm_images or [])
            preview = {
                "system_prompt_tokens_est": estimate_tokens(system_prompt),
                "user_tokens_est": estimate_tokens(user_text) + image_tokens_est,
                "reply_tokens_est": estimate_tokens(final_content),
                "context_window": getattr(self.cache_monitor, "known_context_length", None),
                "budget": persona.budget_profile.to_dict(),
                "free_space_est": persona.budget_profile.free_space(),
            }

            usage_summary = self.usage.summary()
            turn_events = self.usage.events_since(usage_events_before)
            total_latency = sum(float(e.get("latency_seconds") or 0) for e in turn_events)
            total_output = sum(int(e.get("output_tokens") or 0) for e in turn_events)
            turn_usage = {
                "input_tokens": sum(int(e.get("input_tokens") or 0) for e in turn_events),
                "output_tokens": total_output,
                "total_tokens": sum(int(e.get("total_tokens") or 0) for e in turn_events),
                "reasoning_tokens": sum(int(e.get("reasoning_tokens") or 0) for e in turn_events),
                "cached_input_tokens": sum(int(e.get("cached_input_tokens") or 0) for e in turn_events),
                "cost_usd": round(sum(float(e.get("cost_usd") or 0) for e in turn_events), 8),
                "latency_seconds": round(total_latency, 4),
                "llm_calls": len(turn_events),
                "events": turn_events,
                "model": (turn_events[-1].get("model") if turn_events else getattr(self.llm, "model", "")),
                "context_length": (turn_events[-1].get("context_length") if turn_events else None),
                "context_utilization": (turn_events[-1].get("context_utilization") if turn_events else None),
                "generation_tokens_per_second": round(total_output / total_latency, 3) if total_latency > 0 else 0,
            }
            yield {
                "type": "done",
                "reply": final_content,
                "reasoning": final_reasoning,
                "tool_trace": tool_trace,
                "live_context_preview": preview,
                "usage": usage_summary,
                "turn_usage": turn_usage,
                "cache_status": cache_status,
                "verification": state.verdicts,
            }
        finally:
            self._turn_finished()
