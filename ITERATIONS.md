# Iteration log

One new feature per iteration. The list below records what has already been added.

## Iteration 1 — Memory backup and restore

- `Storage.export_wal()` / `Storage.import_wal(data, mode)` in `pai/storage.py`.
- `GET /memory/export` — download the whole WAL as a `.jsonl` backup.
- `POST /memory/import?mode=merge|replace` — restore/merge a backup; the file is
  fully validated before anything is written (a corrupt backup does not touch
  the storage).
- Tests: `tests/test_storage.py` (export/import, merge/replace, invalid files),
  `tests/test_app.py` (endpoints, roundtrip through the API).
- `requirements.txt`: added `python-multipart` (FastAPI needs it for file uploads).
- README: updated the endpoints table, the test count, and added a curl example.

**Status:** implemented, 137/137 tests pass.

## Iteration 2 — Graceful termination when the tool-call limit is exhausted

**Bug:** in a long dialogue (for example "write a book" → "give the full text of
all chapters one by one" → "more text") the model fell into a loop of repeated
`search_archive`/`get_fragment` calls with no result and, once it exhausted
`MAX_TOOL_ITERATIONS` (6), returned the raw technical string
`[tool iteration limit exceeded]` to the user instead of an actual answer.

**Fix** (`pai/orchestrator.py`):
- Added `Orchestrator._force_final_answer(messages)` — when the limit is reached,
  one more LLM call is made **without tools** (`tools=None`), with an explicit
  nudge to formulate a final answer based on the context already gathered (or to
  honestly say the info was not found and suggest refining the request).
- The bare placeholder text (`_ITERATION_LIMIT_FALLBACK`) is used only if even
  that final call produced no text (LLM unavailable / returned empty) — the user
  is never left without a reply.
- The fix is applied to both paths: `_resolve_tool_calls` (regular `/chat`) and
  `_resolve_tool_calls_stream` (`/chat/stream`).
- Tests: `tests/test_orchestrator.py` — forced no-tools call after the limit,
  fallback on empty forced answer, fallback on an LLM error in the forced call,
  and the same scenario for the streaming path.

**Status:** implemented, 140/140 tests pass.

## Iteration 6 — web_search tool (internet search)

- New tool `web_search(query, limit=5)` in `pai/tools.py`: queries
  `html.duckduckgo.com/html/` via stdlib `urllib` (no external dependencies, no
  API key), parses results with regex (`title`, `url`, `snippet`), and unwraps
  DuckDuckGo redirects (`/l/?uddg=...`).
  - `ToolError` on network failure (handled in the dispatcher — returns
    `{"results": [], "error": ...}` instead of crashing the dialogue).
- Schema added to `TOOL_SCHEMAS`; handler `ToolDispatcher._tool_web_search` logs
  the call through `storage.log_retrieval`, like the other tools.
- Explicitly **not** added to the default `Persona.tool_permissions` — the tool
  touches the external internet rather than the user's memory, so it must be
  enabled per persona deliberately (`tool_permissions=[..., "web_search"]`).
- The tool's description for the model states explicitly that it does not replace
  `get_fragment`/`search_archive`, should be used only for external facts, and
  returns snippets rather than full page text.

**Status:** implemented (no tests, per user request).

## Iteration 7 — Changing a persona's tool_permissions

- `PATCH /personas/{persona_id}/tools` in `pai/app.py`: updates the
  `tool_permissions` of an existing persona (e.g. enable/disable `web_search`
  without recreating the persona).
- `PersonaToolsUpdate` (`pai/api_schemas.py`): `{"tool_permissions": [...]}`.
- Validation: names are checked against the real tool list
  (`TOOL_SCHEMAS` from `pai/tools.py`) — unknown name → `422` with the list of
  allowed names. Non-existent persona → `404`.
- `persona.updated_at` is refreshed on change.

**Status:** implemented (no tests, per user request).

## Iteration 8 — Pool of independent tool calls

**Bug:** when a single LLM answer contained several `tool_calls`, the orchestrator
executed them sequentially. This increased latency and, more importantly, did not
architecturally fix the boundary between planning and result analysis: the next
LLM request could be made after each individual tool was processed.

**Fix** (`pai/orchestrator.py`):
- All `tool_calls` from a single LLM answer now form one execution pool.
- Calls run in parallel through a bounded `ThreadPoolExecutor` (up to 8 workers).
- The orchestrator waits for **all** pool calls to finish.
- Only after all results arrive is the next single LLM request made.
- Results are kept in the original `tool_calls` order, so the order of `tool`
  messages is deterministic.
- The change applies simultaneously to the regular and the streaming path.
- An error on one tool is isolated and does not cancel the other pool calls.

Added test `test_handle_turn_executes_multiple_tools_as_one_pool_before_next_llm_call`.

**Status:** implemented.

## 2026-09-09 — Planning tool pools

The previous implementation already ran several `tool_calls` from one LLM answer
as a single execution pool, but if the model returned only one `tool_call`, a new
LLM request was issued right after it. This did not guarantee pre-planning.

The protocol is now changed to:
1. `planning`: the model must produce a `plan_tool_pool`;
2. `pool execution`: all calls in the chosen pool run in parallel and their
   results are saved to memory / `action_log`;
3. `analysis`: the model receives the results of the whole pool and decides
   whether to run another pool or answer the user now.

`max_tool_iterations` now limits the number of such cycles, not the number of
individual functions.