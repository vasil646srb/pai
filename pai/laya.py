"""Laya "System 1": fast local classifier in front of the big LLM.

Rules:
  * never raises into callers — any failure returns None, caller keeps the old behaviour;
  * circuit breaker: after N consecutive failures Laya is skipped for `cooldown` seconds;
  * modes (PAI_LAYA_MODE): off | shadow (default when URL set: compute + log, do not act) | on (act);
  * decisions use our own margin (p1 - p2) from `probabilities`, not Laya's `confidence`.

Env: PAI_LAYA_URL (default http://127.0.0.1:8090; the server itself is managed by laya_runtime.py), PAI_LAYA_MODE, PAI_LAYA_TIMEOUT (s, default 2).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass
class Verdict:
    label: str
    probs: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    latency: float = 0.0

    @property
    def p(self) -> float:
        return self.probs.get(self.label, 0.0)

    @property
    def margin(self) -> float:
        rest = sorted((v for k, v in self.probs.items() if k != self.label), reverse=True)
        return self.p - (rest[0] if rest else 0.0)

    def sure(self, min_p: float = 0.8, min_margin: float = 0.5) -> bool:
        return self.p >= min_p and self.margin >= min_margin

    def as_log(self) -> dict:
        return {"label": self.label, "p": round(self.p, 4), "margin": round(self.margin, 4),
                "confidence": round(self.confidence, 4), "probs": self.probs}


class Laya:
    remote = False  # True for providers that need no local server (laya_runtime then leaves them alone)

    def __init__(self, url: str = "", mode: str = "", timeout: float = 2.0,
                 fail_threshold: int = 3, cooldown: float = 30.0):
        self.url = url.rstrip("/")
        mode = (mode or ("shadow" if self.url else "off")).lower()
        self.mode = mode if mode in ("off", "shadow", "on") and self.url else "off"
        self.timeout = timeout
        self._fail_threshold, self._cooldown = fail_threshold, cooldown
        self._fails = 0
        self._open_until = 0.0
        self._lock = threading.Lock()
        self.ready = True  # laya_runtime flips it to False until its server answers
        self.use_for_verify = False  # the settings-page switch: Jev decides the answer check (see acts_verify)
        self.stats = {"calls": 0, "ok": 0, "fail": 0, "skipped_not_ready": 0,
                      "last_ok": None, "last_error": "", "last_error_at": None}

    @classmethod
    def from_env(cls, env=os.environ) -> "Laya":
        try:
            timeout = float(env.get("PAI_LAYA_TIMEOUT", "2"))
        except ValueError:
            timeout = 2.0
        mode = env.get("PAI_LAYA_MODE", "").lower()
        url = env.get("PAI_LAYA_URL") or ("" if mode == "off" else "http://127.0.0.1:8090")
        return cls(url, mode, timeout)

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def acts_verify(self) -> bool:
        """True when Jev's answer-check verdict may skip the LLM checker: the settings switch, or mode=on."""
        return self.enabled and (self.use_for_verify or self.mode == "on")

    @property
    def acts(self) -> bool:
        """True only in `on` mode: verdicts may change behaviour."""
        return self.mode == "on"

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def _build_payload(self, state: str, questions: dict) -> dict:
        return {"state": state[:6000], "questions": {
            n: {"type": "choice", "instructions": ins, "criteria": crit}
            for n, (ins, crit) in questions.items()}}

    def _parse_answers(self, data: dict, questions: dict, lat: float) -> dict:
        out: dict[str, Verdict] = {}
        for n, a in (data.get("answers") or {}).items():
            if isinstance(a, dict) and a.get("choice") in questions.get(n, ("", []))[1]:
                out[n] = Verdict(a["choice"], {k: float(v) for k, v in (a.get("probabilities") or {}).items()},
                                 float(a.get("confidence") or 0.0), lat)
        return out

    def _count(self, **kv) -> None:
        with self._lock:
            for k, v in kv.items():
                if k == "inc":
                    for name in v:
                        self.stats[name] += 1
                else:
                    self.stats[k] = v

    def _fail(self, why: str) -> None:
        self._count(inc=["fail"], last_error=why[:300], last_error_at=time.time())

    def _post(self, payload: dict) -> dict | None:
        now = time.monotonic()
        with self._lock:
            if now < self._open_until:
                self.stats["fail"] += 1
                self.stats["last_error"] = "circuit breaker open (laya не отвечала, пауза)"
                self.stats["last_error_at"] = time.time()
                return None
        try:
            req = urllib.request.Request(
                f"{self.url}/v1/systemone", data=json.dumps(payload, ensure_ascii=False).encode(),
                headers=self._headers(), method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read(1_000_000).decode("utf-8", "replace"))
            with self._lock:
                self._fails = 0
            self._count(last_ok=time.time())
            return data
        except Exception as e:  # network, timeout, bad JSON — all the same for the caller
            self._fail(f"{type(e).__name__}: {e}")
            with self._lock:
                self._fails += 1
                if self._fails >= self._fail_threshold:
                    self._open_until = time.monotonic() + self._cooldown
                    self._fails = 0
                    _log.warning("laya unavailable (%s): skipping for %ss", e, self._cooldown)
            return None

    def ask(self, state: str, questions: dict[str, tuple[str, list[str]]]) -> dict[str, Verdict] | None:
        """questions: name -> (instructions, criteria). Returns name -> Verdict, or None on any failure."""
        if not self.enabled or not state.strip():
            return None
        if not self.ready:
            self._count(inc=["skipped_not_ready"], last_error="laya не готова (runtime ещё не поднял сервер)",
                        last_error_at=time.time())
            return None
        self._count(inc=["calls"])
        payload = self._build_payload(state, questions)
        t0 = time.time()
        data = self._post(payload)
        lat = time.time() - t0
        if not data:
            return None
        out = self._parse_answers(data, questions, lat)
        if not out:
            self._fail("ответ без нужных полей answers/choice")
            return None
        self._count(inc=["ok"])
        return out

    def choose(self, state: str, instructions: str, criteria: list[str]) -> Verdict | None:
        r = self.ask(state, {"q": (instructions, criteria)})
        return r.get("q") if r else None


class JevLaya(Laya):
    """Jev (TypeSafe) through OpenRouter's System One API. Cloud: text leaves the machine.
    Key: PAI_JEV_API_KEY, else the OpenRouter key saved in the web settings (<data_dir>/llm_config.json),
    read on every call so a key change in the UI applies without restart.
    Same ask() contract as Laya: every question is sent as `choice`, criteria become {label: label}.
    Response: a choice answer with `choice` and/or `probabilities`; if only `choice` is present, its probability is 1.0.
    The real Jev response shape is verified only by the first live call (see stats.last_error)."""
    remote = True

    def __init__(self, url: str = "https://openrouter.ai/api", mode: str = "", timeout: float = 10.0,
                 api_key: str = "", model: str = "jev-1.13", data_dir: str = "./pai_data"):
        super().__init__(url, mode or "shadow", timeout)
        self._api_key_override, self.model, self.data_dir = api_key, model, data_dir

    @classmethod
    def from_env(cls, env=os.environ) -> "JevLaya":
        try:
            timeout = float(env.get("PAI_LAYA_TIMEOUT", "10"))
        except ValueError:
            timeout = 10.0
        return cls(env.get("PAI_JEV_URL", "https://openrouter.ai/api"),
                   env.get("PAI_LAYA_MODE", "").lower(), timeout,
                   api_key=env.get("PAI_JEV_API_KEY", ""),
                   model=env.get("PAI_JEV_MODEL", "jev-1.13"),
                   data_dir=env.get("PAI_DATA_DIR") or "./pai_data")

    def _key(self) -> str:
        if self._api_key_override:
            return self._api_key_override
        try:
            from .llm_config import LLMConfigStore
            return LLMConfigStore(self.data_dir).load().api_key or ""
        except Exception:
            return ""

    def ask(self, state: str, questions: dict) -> dict | None:
        if self.enabled and state.strip() and not self._key():
            self._count(inc=["skipped_not_ready"], last_error="нет API-ключа: сохраните OpenRouter-ключ в настройках LLM или PAI_JEV_API_KEY",
                        last_error_at=time.time())
            return None
        return super().ask(state, questions)

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self._key()}"}

    def _build_payload(self, state: str, questions: dict) -> dict:
        return {"model": self.model, "state": state[:6000], "questions": {
            n: {"type": "choice", "instructions": ins, "criteria": {c: c for c in crit}}
            for n, (ins, crit) in questions.items()}}

    def _parse_answers(self, data: dict, questions: dict, lat: float) -> dict:
        out: dict[str, Verdict] = {}
        for n, a in (data.get("answers") or {}).items():
            crit = questions.get(n, ("", []))[1]
            if not isinstance(a, dict):
                continue
            probs = a.get("probabilities") if isinstance(a.get("probabilities"), dict) else {}
            probs = {k: float(v) for k, v in probs.items() if k in crit}
            label = a.get("choice")
            if label not in crit:
                if not probs:
                    continue
                label = max(probs, key=probs.get)
            if not probs:
                probs = {label: 1.0}
            out[n] = Verdict(label, probs, float(a.get("confidence") or 0.0), lat)
        return out


_POOL_MAX_TOOLS = 40


def last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
            return ""
    return ""


def laya_pool_shadow(storage, space_id, user_text: str, tool_schemas: list[dict], llm_pool: list[dict]) -> None:
    """SHADOW ONLY. For each tool, Jev answers yes/no: 'should this tool be called for this message?'.
    Runs in a background thread, so it adds no latency to the planner. Result goes to action_log next to the LLM's pool.
    Never changes what the planner does."""
    laya = get_laya()
    if not laya.enabled or not user_text.strip() or not tool_schemas:
        return

    def work():
        try:
            qs = {}
            for sch in tool_schemas[:_POOL_MAX_TOOLS]:
                fn = sch.get("function", {})
                name = fn.get("name")
                if not name:
                    continue
                desc = (fn.get("description") or "")[:240]
                qs[name] = (f"Should the assistant call the tool '{name}' to answer the user's message? "
                            f"Tool description: {desc}", ["yes", "no"])
            if not qs:
                return
            verdicts = laya.ask(f"User message:\n{user_text[:2000]}", qs)
            if verdicts is None:
                return
            jev_yes = sorted(n for n, v in verdicts.items() if v.label == "yes" and v.p >= 0.5)
            llm = sorted({tc.get("name") for tc in llm_pool if tc.get("name")})
            actual = {"llm_pool": llm, "jev_yes": jev_yes, "match": set(jev_yes) == set(llm),
                      "truncated": len(tool_schemas) > _POOL_MAX_TOOLS}
            log_shadow(storage, "pool_plan", space_id,
                       {"message_len": len(user_text), "tools": len(qs)}, verdicts, actual)
        except Exception:
            _log.warning("laya pool shadow failed", exc_info=True)

    threading.Thread(target=work, name="pai-laya-pool", daemon=True).start()


_instance: Laya | None = None


def get_laya() -> Laya:
    """PAI_DECISION_PROVIDER=jev (default, OpenRouter, key from web settings) or laya (local server)."""
    global _instance
    if _instance is None:
        if os.environ.get("PAI_DECISION_PROVIDER", "jev").lower() == "jev":
            _instance = JevLaya.from_env()
        else:
            _instance = Laya.from_env()
    return _instance


def log_shadow(storage, name: str, space_id, request: dict, verdicts: dict, actual=None) -> None:
    """One action_log row per decision: what Laya said + what the old path decided. Query later for agreement."""
    try:
        lat = max((v.latency for v in verdicts.values()), default=None)
        storage.log_action("laya", name, request, space_id=space_id, latency_seconds=lat,
                           response={"laya": {k: v.as_log() for k, v in verdicts.items()}, "actual": actual})
    except Exception:
        _log.warning("laya shadow log failed", exc_info=True)


def laya_settings_path(data_dir) -> Path:
    return Path(data_dir) / "laya_settings.json"


def load_laya_settings(path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_laya_settings(path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def laya_verify_pair(storage, space_id, jev_r, llm_verdict, level) -> None:
    """SHADOW pair: Jev's verdict vs the LLM checker's verdict for the same answer, one row per LLM check.
    Skipped when Jev's verdict already replaced the LLM check (skipped_by == 'laya')."""
    try:
        if not jev_r or "answers" not in jev_r or not llm_verdict or llm_verdict.get("skipped_by") == "laya":
            return
        lang = jev_r.get("language")
        jev_pass = jev_r["answers"].label == "answers" and (lang is None or lang.label == "same_language")
        llm_status = llm_verdict.get("status")
        agree = ((llm_status == "pass") == jev_pass) if llm_status in ("pass", "fail") else None
        actual = {"llm_status": llm_status, "llm_usefulness": llm_verdict.get("usefulness"),
                  "jev_pass": jev_pass, "agree": agree}
        log_shadow(storage, "verify_pair", space_id, {"strictness": level}, {}, actual)
    except Exception:
        _log.warning("laya verify pair failed", exc_info=True)
