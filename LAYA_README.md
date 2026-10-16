# Laya in PAI — BUILD 9 — install over the CURRENT project

From the project root (where `pai/` lives):
    unzip -o laya_integration_v2.zip        # adds ONLY new files: pai/laya.py, pai/laya_runtime.py, tests_laya/, apply_laya.py
    python apply_laya.py .                  # edits your current app.py, dialog.py, guest.py, system.py in place

`apply_laya.py` patches against precise anchors and overwrites nothing; before each
edit it creates a `<file>.pre-laya` backup. If an anchor is not found, the file is
left untouched and the missing anchor is reported. Re-running is safe.

Optional environment variables: `PAI_LAYA_MODE=shadow|on|off` (default `shadow`),
`PAI_LAYA_URL=http://127.0.0.1:8090`, `PAI_LAYA_AUTOSTART=1`, `PAI_LAYA_AUTOINSTALL=1`,
`PAI_LAYA_VERSION=1.3.3`, `PAI_LAYA_DIR`, `PAI_LAYA_CLI`, `PAI_LAYA_TIMEOUT=2`.
No API key needed; the server listens only on 127.0.0.1. Log: `<data_dir>/laya.log`,
status: `GET /laya/status`. Tests: `python -m pytest -q tests_laya`.
Shadow log: `SELECT datetime(ts,'unixepoch'), name, response FROM action_log WHERE kind='laya' ORDER BY ts DESC LIMIT 50;`

## v3: busy / backend / timeouts
- A slow server (wasm) is no longer treated as "foreign": it enters the `busy`
  state, without toggling between ready/failed.
- `PAI_LAYA_BACKEND=wasm` — backend for the server started by the project (needed
  on Termux if native does not work).
- `PAI_LAYA_PROBE_TIMEOUT=60` — how long to wait for a reply when checking (s).
- `PAI_LAYA_POLL=30` — check period (s).
- Set the client-side `PAI_LAYA_TIMEOUT` to 10 for wasm (default 2 is too small).

## v4: Laya rows in the "Action log"
After `apply_laya.py`: `python apply_laya_ui.py .`.
- "laya" filter, badge, short row (label and probability), and an expandable
  detail: request, probabilities per question, indentation, confidence, latency,
  and whether the legacy path was used.
- `laya.py` writes `latency_seconds` into the action log.

## v5: diagnostics for "why are there no laya rows"
`GET /laya/status` now contains:
- `breaker_open`, `timeout`.
- `stats`: `calls` (sent to Laya), `ok`, `fail`, `skipped_not_ready`, `last_ok`,
  `last_error`, `last_error_at`.
Rows are written to the action log only on a successful answer; errors are visible
in `stats.last_error` and the application log. A write error to the action log is
now a WARNING (previously muted to DEBUG).

When there are no requests (`stats.calls=0`), Laya is invoked only from:
- `verify_gate`: if answer verification is enabled (`PAI_VERIFY != 0`) and a
  verification model is configured;
- `announce`: a short promise-answer without any tool call;
- `guest_guard`: only in `/guest`, not in `/chat/stream`.

## v6: the Jev provider (cloud, OpenRouter)
Switch with `PAI_DECISION_PROVIDER=jev` (default is `laya` — the local server).
Requires `OPENROUTER_API_KEY` (or `PAI_JEV_API_KEY`). Without a key the feature is
disabled. Options: `PAI_JEV_MODEL=jev-1.13`, `PAI_JEV_URL=https://openrouter.ai/api`,
`PAI_LAYA_TIMEOUT` (for Jev defaults to 10). The local server is not started; message
texts are sent to OpenRouter. All questions are sent as `choice`, with
`criteria -> {label: label}`. If no `probabilities` come back, the chosen label's
probability is treated as 1.0. The Jev response format for `choice` is validated by
the first live call: see `GET /laya/status` -> `stats.last_error`.

## BUILD 7: Jev by default
- `PAI_DECISION_PROVIDER` defaults to `jev`. For local Laya set `PAI_DECISION_PROVIDER=laya`.
- Jev key: `PAI_JEV_API_KEY`, otherwise the OpenRouter key from the LLM settings in
the web UI (`<data_dir>/llm_config.json`). The key is read on every call, so a UI
change takes effect immediately. Works when the LLM provider is OpenRouter.
- Without a key: `stats.skipped_not_ready` and `last_error` "no API key".
- Tests: 22 (`python -m pytest -q tests_laya`).

## BUILD 8: Jev as the shadow voice over tool pools
Order: `unzip -> python apply_laya.py . -> python apply_laya_ui.py . -> python apply_laya_pool.py .`
- On each message (planner iteration 0) Jev answers yes/no for every tool (up to 40).
- The call runs in a background thread, so reply latency is unchanged.
- Written to the action log: `kind='laya'`, `name='pool_plan'`. In
  `response.actual`: `llm_pool`, `jev_yes`, `match`, `truncated`.
- Jev never changes the planner's decisions; the `on` mode is not implemented for
  this step.
- Calls go to OpenRouter; each request includes the tool descriptions and the
  message text.
- Tests: 25.

## BUILD 9: Jev for answer verification (variant B) + a settings toggle
Order: `unzip -> apply_laya.py -> apply_laya_ui.py -> apply_laya_pool.py -> apply_laya_verify.py`
- Settings -> reasoning block -> "Use Jev for answer verification". It saves
  immediately (no Save button) into `<data_dir>/laya_settings.json` and applies to
the running process.
- When the toggle is on, Jev decides "pass / fail" (by substance and language). A
  confident pass skips the LLM verification. Otherwise the LLM verification runs
  as before (it also writes `fix_plan` and `suggested_replies`).
- `PAI_LAYA_MODE=on` also enables this behavior; the toggle exists to enable only
  answer verification.
- Log pair: `kind='laya'`, `name='verify_pair'`: `actual.llm_status`, `jev_pass`,
  `agree`. Written on every LLM verification that received a Jev verdict.
- `GET`/`PUT /laya/settings`. Tests: 29.