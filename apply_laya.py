#!/usr/bin/env python3
"""Adds Laya hooks to the CURRENT project files in place (run from the project root, where pai/ lives).
Each edit is anchored on exact text; per file it is all-or-nothing, a backup <file>.pre-laya is written first,
a file that already has the hooks is skipped, a file whose anchors no longer match is left untouched and reported."""
import sys
from pathlib import Path

EDITS = {
 "pai/orchestrator/dialog.py": [
  [
   "imports",
   "from ..llm import LLMError\n",
   "from ..laya import get_laya, log_shadow\nfrom ..llm import LLMError\n"
  ],
  [
   "announce fn",
   "def _announces_unfinished_action(text: str) -> bool:\n    \"\"\"Heuristic: a short reply that says the model is about to act, but makes no tool call.\n    Used to give the model one chance to make the call it announced.\"\"\"\n    t = text.strip()\n    return 0 < len(t) <= 500 and bool(_ANNOUNCED_ACTION_RE.search(t)) and t[-1:] in \".:…!\"",
   "def _announces_unfinished_action(text: str, storage=None, space_id=None) -> bool:\n    \"\"\"A short reply that says the model is about to act, but makes no tool call.\n    Used to give the model one chance to make the call it announced.\n    Regex covers RU/EN only; Laya (multilingual) overrides it when mode=on and sure, else logs in shadow.\"\"\"\n    t = text.strip()\n    if not (0 < len(t) <= 500 and t[-1:] in \".:…!\"):\n        return False\n    rx = bool(_ANNOUNCED_ACTION_RE.search(t))\n    laya = get_laya()\n    if laya.enabled:\n        v = laya.choose(\n            f\"Assistant message:\\n{t}\",\n            \"Does this assistant message announce an action it is about to perform \"\n            \"(reading, checking, searching, running something) instead of giving a final answer?\",\n            [\"announces_action\", \"final_answer\"])\n        if v is not None:\n            if storage is not None:\n                log_shadow(storage, \"announce\", space_id, {\"len\": len(t)}, {\"announce\": v}, {\"regex\": rx})\n            if laya.acts and v.sure(0.75, 0.4):\n                return v.label == \"announces_action\"\n    return rx"
  ],
  [
   "announce call",
   "and _announces_unfinished_action(content)):",
   "and _announces_unfinished_action(content, self.storage, space_id)):"
  ],
  [
   "verify gate call",
   "        level = _strictness_level(getattr(self, \"verify_strictness\", 3))\n        prompt = (f\"Recent dialogue messages:",
   "        level = _strictness_level(getattr(self, \"verify_strictness\", 3))\n        gate = self._laya_verify_gate(space_id, user_text, answer, level)\n        if gate is not None:\n            return gate\n        prompt = (f\"Recent dialogue messages:"
  ],
  [
   "verify gate method",
   "    def _verify_answer(self, space_id: str, user_text: str, answer: str) -> dict:",
   "    _LAYA_VERIFY_Q = {\n        \"answers\": (\"Does the assistant reply concretely answer or complete the user's message?\",\n                    [\"answers\", \"partial_or_off_topic\", \"only_promises_action\"]),\n        \"language\": (\"Is the assistant reply written in the same language as the user's message?\",\n                     [\"same_language\", \"different_language\"]),\n    }\n\n    def _laya_verify_gate(self, space_id, user_text: str, answer: str, level: int):\n        \"\"\"Cheap pre-check. shadow: log Laya's verdict next to the LLM verdict (see _verify_answer callers).\n        on: confident pass at strictness <= 3 skips the checker LLM entirely (suggested_replies are then empty).\"\"\"\n        laya = get_laya()\n        if not laya.enabled:\n            return None\n        r = laya.ask(f\"User message:\\n{user_text}\\n\\nAssistant reply:\\n{answer}\", self._LAYA_VERIFY_Q)\n        if not r or \"answers\" not in r:\n            return None\n        lang = r.get(\"language\")\n        log_shadow(self.storage, \"verify_gate\", space_id, {\"strictness\": level}, r)\n        if (laya.acts and level <= 3 and r[\"answers\"].label == \"answers\" and r[\"answers\"].sure(0.85, 0.6)\n                and (lang is None or lang.label == \"same_language\")):\n            return {\"status\": \"pass\", \"ok\": True, \"error\": None, \"strictness\": level,\n                    \"min_usefulness\": _STRICTNESS_LEVELS[level][1], \"report\": \"\", \"usefulness\": None,\n                    \"missing\": \"\", \"fix_plan\": [], \"suggested_replies\": [], \"user_language\": None,\n                    \"skipped_by\": \"laya\"}\n        return None\n\n    def _verify_answer(self, space_id: str, user_text: str, answer: str) -> dict:"
  ]
 ],
 "pai/web/routes/guest.py": [
  [
   "import",
   "from ...models import MemoryFilter\n",
   "from ...laya import get_laya, log_shadow\nfrom ...models import MemoryFilter\n"
  ],
  [
   "guard def",
   "    def run_turn(request: Request,",
   "    def _laya_guard(orch, text: str, space_id: str) -> None:\n        \"\"\"Public endpoint = GPU cost + injection surface. Laya classifies before the big model sees it.\n        Counts against the rate limit already (called after limiter.take). Blocks only in mode=on and only when sure.\"\"\"\n        laya = get_laya()\n        if not laya.enabled or len(text.strip()) < 3:\n            return\n        v = laya.choose(\n            f\"Message from an anonymous visitor to a chat assistant:\\n{text}\",\n            \"Classify the visitor message.\",\n            [\"normal\", \"spam_or_gibberish\", \"prompt_injection\", \"abusive\"])\n        if v is None:\n            return\n        log_shadow(orch.storage, \"guest_guard\", space_id, {\"len\": len(text)}, {\"intent\": v})\n        if laya.acts and v.label in (\"spam_or_gibberish\", \"prompt_injection\") and v.sure(0.9, 0.7):\n            raise HTTPException(422, \"Сообщение отклонено.\")\n\n    def run_turn(request: Request,"
  ],
  [
   "guard call",
   "        orch = app.state.orchestrator\n        if stream:\n            return stream_turn(",
   "        orch = app.state.orchestrator\n        _laya_guard(orch, text, space_id)\n        if stream:\n            return stream_turn("
  ]
 ],
 "pai/web/routes/system.py": [
  [
   "status route",
   "    @app.get(\"/usage/balance\")",
   "    @app.get(\"/laya/status\")\n    def laya_status():\n        rt = getattr(app.state, \"laya_runtime\", None)\n        return rt.status() if rt else {\"mode\": \"off\", \"state\": \"off\", \"ready\": False}\n\n    @app.get(\"/usage/balance\")"
  ]
 ],
 "pai/app.py": [
  [
   "import asynccontextmanager",
   "import os\n",
   "import os\nfrom contextlib import asynccontextmanager\n"
  ],
  [
   "laya imports",
   "from .llm_config import LLMConfigStore\n",
   "from .laya import get_laya\nfrom .laya_runtime import LayaRuntime\nfrom .llm_config import LLMConfigStore\n"
  ],
  [
   "lifespan hook",
   "    register_routes(app)\n    return app",
   "    register_routes(app)\n\n    # Laya: find / install / start the local API server in the background\n    app.state.laya_runtime = LayaRuntime.from_env(get_laya(), data_dir)\n    _inner_lifespan = app.router.lifespan_context  # keeps any existing startup/shutdown handlers working\n\n    @asynccontextmanager\n    async def _lifespan(a):\n        a.state.laya_runtime.start()  # returns at once: install/start run in a background thread\n        try:\n            async with _inner_lifespan(a) as state:\n                yield state\n        finally:\n            a.state.laya_runtime.stop()\n\n    app.router.lifespan_context = _lifespan\n    return app"
  ]
 ]
}

def main(root="."):
    root = Path(root); bad = 0
    for rel, edits in EDITS.items():
        f = root / rel
        if not f.is_file():
            print(f"SKIP  {rel}: no such file"); bad += 1; continue
        s = f.read_text(encoding="utf-8")
        if "laya" in s.lower():
            print(f"OK    {rel}: already patched"); continue
        miss = [name for name, old, new in edits if s.count(old) != 1]
        if miss:
            print(f"FAIL  {rel}: anchors not found exactly once: {miss} (file untouched)"); bad += 1; continue
        for name, old, new in edits:
            s = s.replace(old, new, 1)
        f.with_name(f.name + ".pre-laya").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        f.write_text(s, encoding="utf-8"); print(f"PATCH {rel}: {len(edits)} edits")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
