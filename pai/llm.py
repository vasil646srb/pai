"""LLM client abstraction. Real implementation talks to any OpenAI-compatible
/v1/chat/completions endpoint (vLLM, OpenAI, OpenRouter, Ollama, ...).
MockLLM is used in tests and when no provider is configured.

Event protocol of ``chat_stream`` (shared by every client):
    {"type": "reasoning", "text": str}            chain-of-thought delta
    {"type": "content",   "text": str}            final-answer delta
    {"type": "tool_calls", "tool_calls": [...]}   once, after the stream ends
    {"type": "usage", "usage": {...}, "model": str, "context_length": int|None}
    {"type": "finish", "finish_reason": str|None}
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from .usage import estimate_tokens


class LLMError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# helpers: reasoning / content / tool-call extraction
# --------------------------------------------------------------------------

def extract_reasoning(obj: dict | None) -> str:
    """Pull chain-of-thought text out of a message / delta, whatever the
    provider calls it:
      * ``reasoning``          — OpenRouter unified field, gpt-oss on vLLM
      * ``reasoning_content``  — DeepSeek, Qwen/vLLM (--reasoning-parser), llama.cpp
      * ``reasoning_details``  — OpenRouter structured form (text / summary
        items; encrypted items carry no readable text and are skipped)
    Only one of them is used per object, so a provider that duplicates the
    same text in two fields does not produce doubled output."""
    if not isinstance(obj, dict):
        return ""
    for key in ("reasoning", "reasoning_content"):
        v = obj.get(key)
        if isinstance(v, str) and v:
            return v
    details = obj.get("reasoning_details")
    if isinstance(details, list):
        parts = []
        for d in details:
            if not isinstance(d, dict) or d.get("type") == "reasoning.encrypted":
                continue
            t = d.get("text") or d.get("summary")
            if isinstance(t, str) and t:
                parts.append(t)
        return "".join(parts)
    return ""


def content_to_text(content: Any) -> str:
    """``message.content`` is normally a string, but some servers return a
    list of typed parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                t = p.get("text")
                if isinstance(t, str):
                    out.append(t)
        return "".join(out)
    return str(content)


def parse_tool_arguments(raw: Any) -> dict:
    """Tool arguments arrive as a JSON string (OpenAI), sometimes as an
    already-parsed dict, sometimes empty/garbled."""
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return val if isinstance(val, dict) else {}


_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"


class ThinkSplitter:
    """Splits inline ``<think>…</think>`` blocks (DeepSeek-R1 style models
    served without a reasoning parser) out of the content stream. Works on
    arbitrary chunk boundaries: a possible partial tag at the end of a chunk
    is held back until the next one."""

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buf += text
        out: list[tuple[str, str]] = []
        while True:
            tag = _THINK_CLOSE if self._in_think else _THINK_OPEN
            kind = "reasoning" if self._in_think else "content"
            i = self._buf.find(tag)
            if i >= 0:
                if i:
                    out.append((kind, self._buf[:i]))
                self._buf = self._buf[i + len(tag):]
                self._in_think = not self._in_think
                continue
            # hold back a suffix that may be the beginning of the tag
            keep = 0
            for n in range(min(len(tag) - 1, len(self._buf)), 0, -1):
                if tag.startswith(self._buf[-n:]):
                    keep = n
                    break
            emit = self._buf[:len(self._buf) - keep]
            if emit:
                out.append((kind, emit))
            self._buf = self._buf[len(self._buf) - keep:]
            return out

    def flush(self) -> list[tuple[str, str]]:
        out = []
        if self._buf:
            out.append(("reasoning" if self._in_think else "content", self._buf))
        self._buf = ""
        return out


def split_think(text: str) -> tuple[str, str]:
    """Non-streaming variant: returns (content, reasoning)."""
    if not text or _THINK_OPEN not in text:
        return text or "", ""
    sp = ThinkSplitter()
    parts = sp.feed(text) + sp.flush()
    content = "".join(t for k, t in parts if k == "content")
    reasoning = "".join(t for k, t in parts if k == "reasoning")
    return content.lstrip("\n"), reasoning.strip()


# --------------------------------------------------------------------------
# base client
# --------------------------------------------------------------------------

class LLMClient(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        tool_choice=None,
    ) -> dict:
        """Return a dict: {"content": str|None, "tool_calls": list[dict],
        "reasoning": str|None, "finish_reason": str|None, "usage": dict, ...}.
        Each tool_call: {"id": str, "name": str, "arguments": dict}.
        "reasoning" carries the model's chain-of-thought / thinking output
        when the provider returns one — None otherwise.
        temperature/max_tokens default to the client's configured values
        when left as None; pass explicit values to override for one call."""
        raise NotImplementedError

    def request_params(self) -> dict:
        """Generation parameters this client will send on the *next* call
        (excluding messages/tools). Used for logging/audit."""
        return {
            "model": getattr(self, "model", None),
            "temperature": getattr(self, "temperature", None),
            "max_tokens": getattr(self, "max_tokens", None),
            "top_p": getattr(self, "top_p", None),
            "reasoning_enabled": getattr(self, "reasoning_enabled", None),
            "reasoning_effort": getattr(self, "reasoning_effort", None) if getattr(self, "reasoning_enabled", None) else None,
        }

    def summarize_ex(self, text: str, instruction: str = "") -> dict:
        """Like summarize() but returns the full picture for logging."""
        prompt = instruction or (
            "Compress the following text into a brief but informative summary. "
            "Preserve key facts, decisions, and time frames, if any."
        )
        summary_max_tokens = _summary_budget(text)
        params = dict(self.request_params())
        params.update({"temperature": 0.2, "max_tokens": summary_max_tokens})
        t0 = time.time()
        result = self.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
            max_tokens=summary_max_tokens,
        )
        return {
            "summary": (result.get("content") or "").strip(),
            "reasoning": result.get("reasoning"),
            "usage": result.get("usage") or {},
            "model": result.get("model") or getattr(self, "model", ""),
            "context_length": result.get("context_length") or getattr(self, "context_length", None),
            "request_params": params,
            "latency_seconds": time.time() - t0,
        }

    def summarize(self, text: str, instruction: str = "") -> str:
        """Convenience wrapper for background summarization calls."""
        return self.summarize_ex(text, instruction)["summary"]

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        tool_choice=None,
    ) -> Iterator[dict]:
        """Default fallback for clients without real streaming: call chat()
        and replay the result through the same event protocol."""
        result = self.chat(
            messages=messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens, top_p=top_p, tool_choice=tool_choice,
        )
        yield from _replay_result(result)


def _summary_budget(text: str) -> int:
    # max_tokens has to cover the *whole* budget for the call: with reasoning
    # enabled, thinking tokens come out of the same budget and a small
    # constant can leave zero tokens for the actual summary.
    return max(768, min(estimate_tokens(text), 4000))


def _replay_result(result: dict) -> Iterator[dict]:
    reasoning = result.get("reasoning") or ""
    if reasoning:
        yield {"type": "reasoning", "text": reasoning}
    content = result.get("content") or ""
    if content:
        yield {"type": "content", "text": content}
    if result.get("tool_calls"):
        yield {"type": "tool_calls", "tool_calls": result["tool_calls"]}
    if result.get("usage"):
        yield {"type": "usage", "usage": result["usage"], "model": result.get("model"),
               "context_length": result.get("context_length")}
    yield {"type": "finish", "finish_reason": result.get("finish_reason")}


# --------------------------------------------------------------------------
# OpenAI-compatible client
# --------------------------------------------------------------------------

_RETRY_STATUSES = {429, 500, 502, 503, 504}


class OpenAICompatibleClient(LLMClient):
    """Talks to any OpenAI-compatible /v1/chat/completions endpoint —
    a self-hosted vLLM server, OpenAI's own API, OpenRouter, or any other
    provider that speaks the same wire format."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: float = 180.0,
        extra_headers: dict[str, str] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        top_p: float | None = None,
        context_window: int = 8192,
        reasoning_enabled: bool = False,
        reasoning_effort: str = "medium",
        top_k: int | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ):
        self.base_url = self._normalize_base_url(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.top_k = top_k
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty
        self.context_window = max(1024, int(context_window or 8192))
        self.reasoning_enabled = reasoning_enabled
        self.reasoning_effort = reasoning_effort

    # ---- config / introspection ----

    @property
    def is_openrouter(self) -> bool:
        return "openrouter.ai" in self.base_url.lower()

    def request_params(self) -> dict:
        params = super().request_params()
        params["top_p"] = self.top_p
        params["top_k"] = self.top_k
        params["frequency_penalty"] = self.frequency_penalty
        params["presence_penalty"] = self.presence_penalty
        params["context_window"] = self.context_window
        params["tool_choice"] = "auto"  # only actually sent when tools is non-empty
        return params

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        """Strip a trailing endpoint path if one was pasted in by mistake
        (…/v1/chat/completions instead of …/v1)."""
        url = base_url.rstrip("/")
        for suffix in ("/chat/completions", "/completions", "/responses"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
                break
        return url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for k, v in self.extra_headers.items():
            # HTTP header values must be latin-1 encodable; a Cyrillic site
            # name would otherwise crash urllib.
            try:
                v.encode("latin-1")
                headers[k] = v
            except UnicodeEncodeError:
                headers[k] = v.encode("ascii", errors="ignore").decode("ascii") or "pai"
        return headers

    @staticmethod
    def _estimate_message_tokens(messages: list[dict]) -> int:
        total = 0
        for message in messages:
            if message.get("tool_calls"):
                total += estimate_tokens(json.dumps(message["tool_calls"], ensure_ascii=False))
            content = message.get("content")
            if isinstance(content, str):
                total += estimate_tokens(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "image_url":
                            total += 768
                        else:
                            total += estimate_tokens(str(block.get("text") or ""))
        return max(1, total)

    def _effective_max_tokens(self, messages: list[dict], requested: int | None) -> int:
        configured = self.max_tokens if requested is None else requested
        input_tokens = self._estimate_message_tokens(messages)
        available = self.context_window - input_tokens
        if available <= 0:
            raise LLMError(
                f"Context window of {self.context_window} tokens exceeded: "
                f"the input context takes up approximately {input_tokens} tokens. "
                f"Increase the context window in Settings or compress memory."
            )
        return max(1, min(int(configured), available))

    # ---- request building ----

    def _build_payload(
        self, messages, tools, temperature, max_tokens, top_p, tool_choice, stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self._effective_max_tokens(messages, max_tokens),
        }
        effective_top_p = self.top_p if top_p is None else top_p
        if effective_top_p is not None:
            payload["top_p"] = effective_top_p
        if self.top_k is not None:
            payload["top_k"] = self.top_k
        if self.frequency_penalty is not None:
            payload["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty is not None:
            payload["presence_penalty"] = self.presence_penalty
        if self.reasoning_enabled:
            if self.is_openrouter:
                payload["reasoning"] = {"effort": self.reasoning_effort}
            else:
                # OpenAI / vLLM / Ollama style
                payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _open(self, url: str, payload: dict | None, method: str = "POST"):
        """urlopen with retry on transient statuses; every failure mode is
        mapped to LLMError so callers (and the SSE endpoint) always get a
        readable message."""
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        attempts = 3
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                if e.code in _RETRY_STATUSES and attempt < attempts - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise LLMError(f"HTTP {e.code} from {url}: {err_body}") from e
            except urllib.error.URLError as e:
                raise LLMError(f"Failed to reach {url}: {e}") from e
            except TimeoutError as e:
                raise LLMError(f"Timeout after {self.timeout:.0f}s waiting for {url}") from e
            except OSError as e:
                raise LLMError(f"Network error talking to {url}: {e}") from e
        raise LLMError(f"Failed to reach {url}")  # pragma: no cover

    # ---- non-streaming ----

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        tool_choice=None,
    ) -> dict:
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(messages, tools, temperature, max_tokens, top_p, tool_choice, stream=False)
        try:
            with self._open(url, payload) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as e:
            raise LLMError(f"Timeout after {self.timeout:.0f}s waiting for {url}") from e
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise LLMError(f"Non-JSON response from {url}: {e}") from e

        if isinstance(body, dict) and body.get("error") and not body.get("choices"):
            raise LLMError(f"Provider error from {url}: {body['error']}")
        try:
            choice0 = body["choices"][0]
            msg = choice0["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Malformed response from LLM endpoint: {body}") from e

        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tool_calls.append({
                "id": tc.get("id", ""), "name": fn.get("name", ""),
                "arguments": parse_tool_arguments(fn.get("arguments")),
            })

        content = content_to_text(msg.get("content"))
        reasoning = extract_reasoning(msg)
        if not reasoning and _THINK_OPEN in content:
            content, reasoning = split_think(content)

        return {
            "content": content, "tool_calls": tool_calls,
            "reasoning": reasoning or None, "usage": body.get("usage") or {},
            "model": body.get("model") or self.model,
            "context_length": body.get("context_length"),
            "finish_reason": choice0.get("finish_reason"),
        }

    # ---- streaming ----

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        tool_choice=None,
    ) -> Iterator[dict]:
        """Real token-by-token SSE streaming, with tool-call support.
        Reasoning deltas are read from ``delta.reasoning`` /
        ``reasoning_content`` / ``reasoning_details``; inline ``<think>``
        blocks in the content are split out as reasoning."""
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(messages, tools, temperature, max_tokens, top_p, tool_choice, stream=True)

        splitter = ThinkSplitter()
        saw_native_reasoning = False
        tool_acc: dict[int, dict] = {}
        finish_reason: str | None = None
        last_usage: dict | None = None

        def emit_content(piece: str):
            if saw_native_reasoning:
                yield {"type": "content", "text": piece}
                return
            for kind, text in splitter.feed(piece):
                yield {"type": kind, "text": text}

        try:
            resp = self._open(url, payload)
            with resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("error"):
                        raise LLMError(f"Provider error mid-stream: {chunk['error']}")
                    if chunk.get("usage"):
                        last_usage = chunk["usage"]
                        yield {
                            "type": "usage", "usage": last_usage,
                            "model": chunk.get("model") or self.model,
                            "context_length": chunk.get("context_length"),
                        }
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    ch = choices[0]
                    delta = ch.get("delta") or {}
                    if ch.get("finish_reason"):
                        finish_reason = ch["finish_reason"]

                    rpiece = extract_reasoning(delta)
                    if rpiece:
                        saw_native_reasoning = True
                        yield {"type": "reasoning", "text": rpiece}

                    piece = content_to_text(delta.get("content"))
                    if piece:
                        yield from emit_content(piece)

                    for pos, tc in enumerate(delta.get("tool_calls") or []):
                        idx = tc.get("index")
                        if idx is None:
                            idx = pos
                        slot = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"] if slot["name"] != fn["name"] else ""
                        a = fn.get("arguments")
                        if isinstance(a, str):
                            slot["args"] += a
                        elif isinstance(a, dict):
                            slot["args"] = json.dumps(a)
        except TimeoutError as e:
            raise LLMError(f"Timeout after {self.timeout:.0f}s waiting for {url}") from e
        except (ConnectionError, OSError) as e:
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"Stream interrupted: {e}") from e

        for kind, text in splitter.flush():
            yield {"type": kind, "text": text}

        if tool_acc:
            calls = []
            for idx in sorted(tool_acc):
                s = tool_acc[idx]
                if not s["name"]:
                    continue
                calls.append({"id": s["id"] or f"call_{idx}", "name": s["name"],
                              "arguments": parse_tool_arguments(s["args"])})
            if calls:
                yield {"type": "tool_calls", "tool_calls": calls}
        yield {"type": "finish", "finish_reason": finish_reason}

    # ---- catalog ----

    def list_models(self) -> list[dict]:
        """Fetch the provider's model catalog via GET /models. Returns
        {id, name, context_length, price_prompt_per_1m, price_completion_per_1m}
        dicts sorted by id. Raises LLMError on failure."""
        url = f"{self.base_url}/models"
        try:
            with self._open(url, None, method="GET") as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as e:
            raise LLMError(f"Timeout waiting for {url}") from e
        except json.JSONDecodeError as e:
            raise LLMError(f"Malformed response from {url}: {e}") from e

        entries = body.get("data") or body.get("models") or [] if isinstance(body, dict) else []
        out = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id") or entry.get("name")
            if not entry_id:
                continue
            ctx = entry.get("context_length") or entry.get("context_window")
            price_prompt, price_completion = self._parse_pricing(entry.get("pricing"))
            out.append({
                "id": entry_id,
                "name": entry.get("name") or entry_id,
                "context_length": ctx if isinstance(ctx, int) and ctx > 0 else None,
                "price_prompt_per_1m": price_prompt,
                "price_completion_per_1m": price_completion,
            })
        out.sort(key=lambda m: m["id"])
        return out

    @staticmethod
    def _parse_pricing(pricing: Any) -> tuple[float | None, float | None]:
        """OpenRouter pricing is USD-per-token strings; convert to USD per 1M.
        Zero/missing/malformed → None ("unknown", not "free")."""
        if not isinstance(pricing, dict):
            return None, None

        def to_per_million(raw: Any) -> float | None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            return value * 1_000_000

        return to_per_million(pricing.get("prompt")), to_per_million(pricing.get("completion"))

    def fetch_model_context_length(self) -> int | None:
        """Best-effort lookup of the configured model's real context window."""
        try:
            models = self.list_models()
        except LLMError:
            return None
        for m in models:
            if m["id"] == self.model and m["context_length"]:
                return m["context_length"]
        return None


# --------------------------------------------------------------------------
# mock
# --------------------------------------------------------------------------

class MockLLM(LLMClient):
    """Deterministic mock for tests and offline development.
    `responses` is an optional queue of canned responses; otherwise falls back
    to trivial deterministic behavior (echo / truncate-based summary)."""

    def __init__(self, responses: list[dict] | None = None):
        self._queue = list(responses) if responses else []
        self.calls: list[dict] = []

    def queue_response(
        self,
        content: str | None = None,
        tool_calls: list[dict] | None = None,
        reasoning: str | None = None,
    ) -> None:
        self._queue.append({"content": content, "tool_calls": tool_calls or [], "reasoning": reasoning})

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.3,
        max_tokens: int | None = 1024,
        top_p: float | None = None,
        tool_choice=None,
    ) -> dict:
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if self._queue:
            return self._queue.pop(0)
        last_user = next((_msg_text(m) for m in reversed(messages) if m.get("role") == "user"), "")
        return {"content": f"[mock reply to: {last_user[:60]}]", "tool_calls": [], "reasoning": None}

    def summarize(self, text: str, instruction: str = "") -> str:
        self.calls.append({"summarize": text})
        if self._queue:
            r = self._queue.pop(0)
            return (r.get("content") or "").strip()
        snippet = " ".join(text.split())[:160]
        return f"Summary: {snippet}{'...' if len(text) > 160 else ''}"

    def summarize_ex(self, text: str, instruction: str = "") -> dict:
        return {"summary": self.summarize(text, instruction), "reasoning": None, "usage": {},
                "model": "mock", "context_length": None, "request_params": {}, "latency_seconds": 0.0}

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.3,
        max_tokens: int | None = 1024,
        top_p: float | None = None,
        tool_choice=None,
    ) -> Iterator[dict]:
        result = self.chat(messages=messages, tools=tools, tool_choice=tool_choice)
        reasoning = result.get("reasoning") or ""
        if reasoning:
            for w in reasoning.split(" "):
                yield {"type": "reasoning", "text": w + " "}
        content = result.get("content") or ""
        words = content.split(" ")
        for i, w in enumerate(words):
            if content:
                yield {"type": "content", "text": (w + " ") if i < len(words) - 1 else w}
        if result.get("tool_calls"):
            yield {"type": "tool_calls", "tool_calls": result["tool_calls"]}
        yield {"type": "finish", "finish_reason": "tool_calls" if result.get("tool_calls") else "stop"}


def _msg_text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return str(c or "")
