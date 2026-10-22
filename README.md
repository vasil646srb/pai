# Personal AI Assistant (pai)

A self-hosted, **memory-driven personal AI assistant**. The core is an
orchestrator that routes every conversation turn through a
**planning → tool-pool execution → analysis** loop, backed by persistent
hierarchical memory (a RAPTOR-style tree), per-persona memory spaces, a
growing library of tools, and optional autonomous background services
(Telegram, Laya/Jev decision scoring, MCP).

`pai` is designed to run locally (e.g. inside Termux / proot-distro on a
phone) or on a small server, with the option to plug into a local
vLLM instance or a cloud provider (OpenRouter).

> **Language note.** The main `README.md` and the companion docs
> (`LAYA_README.md`, `ROLES_README.md`, `TELEGRAM_*_README.md`,
> `PYTHON_FUNCTIONS.md`, `ITERATIONS.md`) are in English. The web UI and
> some source comments are in Russian; the project itself is
> language-agnostic.

---

## Table of contents

- [Features](#features)
- [Architecture overview](#architecture-overview)
- [How a turn is processed](#how-a-turn-is-processed)
- [Memory model](#memory-model)
- [Personas and self-generated rules](#personas-and-self-generated-rules)
- [Tools and skills](#tools-and-skills)
- [Laya / Jev decision scoring](#laya--jev-decision-scoring)
- [Telegram integration](#telegram-integration)
- [MCP support](#mcp-support)
- [Web UI](#web-ui)
- [HTTP API](#http-api)
- [Quick start](#quick-start)
- [Environment variables](#environment-variables)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Companion documentation](#companion-documentation)

---

## Features

- **Hierarchical memory tree** — dialogue is stored as raw level-0 nodes and
  periodically compressed up to level 1 (primary summaries), level 2
  (thematic), and beyond. Live context grows **logarithmically** with respect
  to linear history growth. (See [Memory model](#memory-model).)
- **Planning-driven turns** — every turn goes through a planner that selects
  a tool pool, runs the pool, then analyzes the results, up to
  `max_tool_iterations` (default 6) before composing a final answer.
- **Per-persona memory spaces** — each persona with `allow_spaces` chats in
  its own space (work → `work`, tutor → `study`, …), keeping prompts and
  retrieval scoped to that persona's domain.
- **Personas** — an overlay of system-prompt addon text, active skills, a
  memory filter, tool permissions and a tone/budget profile. Switching a
  persona never forks the archive.
- **Self-generated rules** — a `draft → validated → active` lifecycle for
  rules the assistant discovers, resolved per persona/space context.
- **Custom functions** — user-defined functions (LLM-prompt, HTTP-JSON,
  Python) managed through the "Functions" page, with a validate + activate
  flow.
- **Auto-compression** — when the model's KV cache free space drops below a
  threshold (default 50%), compression runs automatically (and defers while
  a turn is actively in progress).
- **WAL + SQLite dual storage** — every node is written to a write-ahead log
  first, then upserted into the SQLite index with an FTS5 full-text view.
- **Laya / Jev decision scoring** — an optional "yes/no" decision layer that
  scores tool-pool choices and answer verification (see below).
- **Telegram** — post to public/private channels and groups, read new
  messages, and optionally auto-reply in the background with the `brain`
  persona (Jev decides whether a reply is needed).
- **MCP client** — connect to external Model Context Protocol servers from a
  catalog.
- **Guest mode** — a public page for site visitors (guest space, inbox,
  private guest rooms) with per-IP rate limits.

---

## Architecture overview

The application is assembled by the factory `pai/app.create_app()`:

```
pai.app (FastAPI application factory)
 ├─ Storage (SQLite + WAL, memory nodes, files, inbox, guest rooms)
 ├─ Orchestrator (turn loop, cache monitor, context budget)
 │   ├─ DialogLoop       — per-turn conversation flow
 │   ├─ ContextBuilder   — assembles the live context + budget
 │   ├─ LLMRunner        — calls the LLM, iterates on tool calls
 │   ├─ ToolPoolPlanner  — builds and runs the tool pool
 │   ├─ TreeBuilder      — hierarchical compression
 │   └─ CacheManager     — KV-cache monitoring + auto-compression trigger
 ├─ LLM clients          — main / summarizer / verifier / vision models
 ├─ Personas, Skills, Rules, CustomFunctions registries (JSON files)
 ├─ SecretStore + LLMConfigStore   — keys & provider config (JSON, 0600)
 ├─ LayaRuntime          — optional Laya/Jev decision server
 ├─ ServiceStore         — autonomous services registry
 └─ web layer            — routes (chat, memory, personas, …) + static UI
```

Everything composes through dependency injection so tests can pass explicit
objects instead of mutating the environment.

---

## How a turn is processed

1. **User message** arrives (via web chat, Telegram, or the HTTP API) with a
   persona id and a memory space id.
2. The **planner** (planning phase) selects a **tool pool** — a set of tools
   to run for this turn. When Laya/Jev `pool_plan` scoring is enabled, each
   tool is scored yes/no in a background thread (latency unchanged).
3. The **pool runs**: `search_archive`, `get_fragment`, `web_search`,
   `web_fetch`, `http_request`, `shell_exec`, etc. Each tool call is logged
   to the action log.
4. The **analysis** phase parses the results on the planning/analysis model.
5. Steps 2–4 repeat up to `max_tool_iterations` (default 6), then the **final
   answer** is composed on the main model.
6. The answer is optionally **verified** by a verifier model (or by Jev when
   enabled). The turn's raw content is saved as a level-0 memory node
   immediately.
7. When KV-cache usage gets tight, **auto-compression** demotes raw history
   into summaries.

The reasoning/tool-call trace and token usage are stored and shown in the
web UI (Action log, Usage page).

---

## Memory model

Memory is a **forest of trees**, one per space. Persisted in SQLite
(`index.sqlite3`) with a **write-ahead log** (`wal.jsonl`) appended before
every write for crash safety.

- **Level 0** — raw nodes (a message, an event, a fact). Stored verbatim.
- **Level 1** — summaries of primary clusters.
- **Level 2+** — thematic summaries rolling up lower levels.

Each node carries: `id`, `space_id`, `level`, `parent_id` / `child_ids`,
`type`, `summary_text`, `raw_content`, timestamps, `position_in_history`,
`timeframe_start/end`, `tags`, `status`, `annotations`, `retrieval_count`,
and `reasoning` (chain-of-thought, display-only — never fed back to the
model).

Full-text search runs over an **FTS5** virtual table (`nodes_fts`). Search is
scoped by the persona's memory filter (`allow_spaces` / `deny_spaces`).

Storage also keeps: the `retrieval_log`, the `action_log` (kept to the latest
3000 rows), uploaded file metadata + extracted text, inbox messages from
guests, and private guest rooms.

---

## Personas and self-generated rules

Personas are **not** part of the memory tree — they are an overlay of:

- `system_core_addon` — extra prompt text added to the system core for this
  persona,
- `active_skill_ids` — the subset of active skills,
- `memory_filter` — allowed/denied spaces and tags,
- `tool_permissions` — allowed tools,
- `tone` and budget profile.

Built-in personas (seeded on first run into `personas.json`):

| id | Purpose | Memory spaces | Notable tools |
|----|---------|---------------|---------------|
| `brain` | Precise full-archive search, web/HTTP/shell | all | web_search, web_fetch, http_request, shell_exec |
| `work` | Business assistant, plans/meetings/drafts | work, shared (no diary) | daily_plan, meeting_notes skills |
| `pm` | Proactive project manager (agreements, deadlines) | work, shared | daily_plan, meeting_notes |
| `friend` | Warm conversationalist | personal, health, shared | — |
| `tutor` | Socratic tutor | study, shared | — |
| `assistant` | Standard assistant, no shell/HTTP | all | get_fragment, search_archive, web_* |
| `guest` | Public site visitors | guest, shared (diary hidden) | get_fragment, search_archive, web_* |

Internal (process-only, not in the UI): `telegram_group` — the persona used
by the Telegram background auto-reply.

**Self-generated rules** follow a lifecycle:
`draft → validated → active`, evaluated per persona and space context. They
let the assistant encode recurring, useful behaviors without hard-coding
prompt text.

---

## Tools and skills

Tools are dispatched by `pai/tools/dispatcher.py` through a declarative
schema (`tools/schemas.py`). The persona's `tool_permissions` gate which
tools are available.

- **Memory tools** (`tools/memory.py`) — `get_fragment`, `search_archive`,
  `calculate_time_delta`, `list_fragments_by_timeframe`.
- **Web tools** (`tools/web.py`) — `web_search`, `web_fetch`,
  `http_request` (supports `{{secret:NAME}}` placeholders resolved via the
  SecretStore).
- **Local tools** (`tools/shell.py`) — `shell_exec` (bash / termux / python3
  runtimes). Only where explicitly intended.
- **Persona tools** (`tools/persona.py`) — list/get/create/edit personas.
- **Proposal tools** (`tools/proposals.py`) — propose custom functions and
  rules.
- **Telegram tools** — `telegram_post`, `telegram_read`.

**Skills** (`pai/skills.py`) are a separate modular scheme for system
instructions (as opposed to dialogue history, which uses the tree). A default
catalog ships and is persisted to `skills.json`.

---

## Laya / Jev decision scoring

`pai` has an optional decision layer that answers **yes/no** questions that
help steer the planner and verifier. It comes in two flavors:

- **Laya** — a local API server (`PAI_DECISION_PROVIDER=laya`) that listens
  only on `127.0.0.1`, no API key needed.
- **Jev** — a cloud provider on OpenRouter (`PAI_DECISION_PROVIDER=jev`, the
  default). Requires an OpenRouter key.

What it does:

- **`pool_plan`** — on each message (planner iteration 0) scores every tool
  in the pool yes/no, in a background thread (reply latency unchanged). It
  never overrides the planner's decisions.
- **Answer verification** — decides `pass / fail` by substance and language;
  a confident pass can skip the LLM verifier (the settings toggle "Use Jev
  for answer verification", or `PAI_LAYA_MODE=on`).

Status and diagnostics are available at `GET /laya/status`; the Laya log
lives in `<data_dir>/laya.log`. Full details: **`LAYA_README.md`**.

**Models per task (BUILD 10)** — a separate "Pool planning model" runs the
planning and analysis phases; the final answer stays on the main model.
Summarization and answer verification keep their own models. See
**`ROLES_README.md`**.

---

## Telegram integration

Three Telegram surfaces are supported (see the `TELEGRAM_*_README.md` files):

- **Public channel** — posts via a confirmation queue (default) or immediate.
- **Private channel** — immediate (default) or confirmation.
- **Group** — a third channel; the bot can read new messages via
  `getUpdates` (background poll, default every 60 s) and post to the queue.

Tools: `telegram_post(channel, text)` and `telegram_read(channel)`.

**Background auto-reply (BUILD 13)**: every `PAI_TELEGRAM_POLL_SEC` seconds
the agent fetches new messages, Jev classifies each (reply needed? type:
question / request / chatter / spam), and if a reply is needed the `brain`
persona writes it into the group queue (confirmation) or posts it
(immediate). Spam and bot messages are skipped; each message is processed
once. Requires the Laya/Jev integration and a key.

Bot token is stored in the SecretStore (`TELEGRAM_BOT_TOKEN`) and only its
tail is shown in the UI. API base can be overridden with `PAI_TELEGRAM_API`.

> **Group privacy note.** By default a bot in a group only sees commands,
> mentions, and replies to its own messages. To see everything:
> `@BotFather → /setprivacy → Disable`, then remove and re-add the bot.
> Webhooks must be disabled (`deleteWebhook`) or `getUpdates` returns a
> conflict error.

---

## MCP support

`pai` includes an MCP client (`mcp_client.py`, `mcp_catalog.py`) that can
connect to external **Model Context Protocol** servers configured via a
catalog (`PAI_MCP_CATALOG_URLS`). Secret values in the exposed config are
masked; keys remain visible.

---

## Web UI

The single-page UI is served from `/` (`pai/static/`). Views available in the
sidebar:

- **Chat** — talk to the active persona; attach images and files; streamed
  responses; a toolbar with token/latency stats and "hide tool calls".
- **Overview** — storage stats (nodes by level/space, disk size), KV-cache
  status, manual compression trigger, retrieval log.
- **Tree / nodes** — browse the memory forest top-down, full-text search,
  node inspector (`get_fragment`).
- **Spaces** — list/rename/delete memory spaces.
- **Personas** — edit personas, tool permissions, memory filters; create new.
- **Skills** — manage the skill catalog.
- **Rules** — manage self-generated rules (validate / activate).
- **Functions** — manage custom functions (LLM-prompt / HTTP-JSON / Python).
- **Usage** — token usage and cost.
- **Action log** — `llm_request` and `tool_call`/`laya` rows.
- **Services** — autonomous services registry.
- **Telegram** — bot config, channels, group auto-reply, publish/reject queue.
- **Settings** — LLM provider, models per role, reasoning, compression.

A password gate protects the UI unless `PAI_AUTH_DISABLED=1`.
A separate **guest page** (`/guest`) is available for site visitors.

---

## HTTP API

The FastAPI app exposes JSON endpoints under `/api` plus a Swagger UI at
`/docs`. Main areas: chat (`/chat`, streaming), memory (search / nodes /
spaces / structure), personas, skills, rules, functions, settings, actions,
services, uploads, inbox, guest rooms, Telegram, Laya status/settings, and
speech. Use the Swagger UI at `/docs` for the full, up-to-date route list.

---

## Quick start

Requirements: **Python ≥ 3.10**.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the server (LLM from env, or MockLLM if not configured)
export PAI_LLM_BASE_URL=http://localhost:8000/v1   # e.g. a local vLLM
export PAI_LLM_API_KEY=...                          # required for OpenRouter
export PAI_LLM_MODEL=qwen/qwen3-30b-a3b             # model id
python run_server.py

#    default: UI + Swagger at http://127.0.0.1:8080
#    (see --help docs in run_server.py)

# 3. Run the test suite
python -m pytest tests
```

`run_server.py` honors `PAI_HOST` (default `0.0.0.0`), `PAI_PORT` (default
`8080`) and `PAI_DATA_DIR` (default `./pai_data`). Without
`PAI_LLM_BASE_URL` the server uses a `MockLLM` so you can exercise the API
without an inference backend.

There is also an `example_run.py`, plus helper scripts `deploy.sh`,
`f.sh`, `fix.py`, `restart_server.sh`.

---

## Environment variables

### Server / data

| Variable | Default | Meaning |
|----------|---------|---------|
| `PAI_DATA_DIR` | `./pai_data` | Data directory (SQLite, WAL, JSON registries, files). |
| `PAI_HOST` | `0.0.0.0` | Bind host. |
| `PAI_PORT` | `8080` | Bind port. |
| `PAI_TIMEZONE` | system | IANA timezone, e.g. `Europe/Belgrade`. |
| `PAI_TAIL_LENGTH` | `10` | Number of chat messages in the prompt tail. |
| `PAI_VERIFY` | `1` (`0`=off) | Enable answer verification. |
| `PAI_SERVICES` | `1` (`0`=off) | Enable autonomous services. |
| `PAI_PASSWORD` | empty | Web UI password (enables auth gate). |
| `PAI_AUTH_DISABLED` | `0` (`1`=off) | Disable the password gate. |
| `PAI_CORS_ORIGINS` | empty | Comma-separated extra CORS origins (never `*`). |

### LLM provider (`PAI_LLM_*`)

| Variable | Meaning |
|----------|---------|
| `PAI_LLM_BASE_URL` | Provider base URL (vLLM `…/v1`, OpenRouter `…/api/v1`). |
| `PAI_LLM_API_KEY` | API key (mandatory for OpenRouter). |
| `PAI_LLM_MODEL` | Main model id. |
| `PAI_LLM_SUMMARIZER_MODEL` | Summarization/compression model (blank = main). |
| `PAI_LLM_SITE_URL` / `PAI_LLM_SITE_NAME` | OpenRouter attribution (ASCII only). |
| `PAI_LLM_TEMPERATURE` | Default `0.3`. |
| `PAI_LLM_MAX_TOKENS` | Default `1024`. |
| `PAI_LLM_TOP_P` | Optional. |
| `PAI_LLM_CONTEXT_WINDOW` | Default `8192`. |
| `PAI_LLM_REASONING_ENABLED` | `1`/`true`/`yes` to enable reasoning. |
| `PAI_LLM_REASONING_EFFORT` | `low`/`medium`/`high` (default `medium`). |
| `PAI_COMPRESSION_TRIGGER_PCT` | KV free-space threshold for auto-compression (1–95, default `50`). |

### Laya / Jev

| Variable | Default | Meaning |
|----------|---------|---------|
| `PAI_DECISION_PROVIDER` | `jev` | `laya` (local server) or `jev` (OpenRouter). |
| `PAI_JEV_API_KEY` | — | Jev key (or falls back to the OpenRouter key). |
| `PAI_JEV_MODEL` | `jev-1.13` | Jev model. |
| `PAI_JEV_URL` | `https://openrouter.ai/api` | Jev endpoint. |
| `PAI_LAYA_MODE` | `shadow` | `shadow` / `on` / `off`. |
| `PAI_LAYA_URL` | `http://127.0.0.1:8090` | Local Laya server URL. |
| `PAI_LAYA_TIMEOUT` | `2` | Client timeout (use 10 for wasm). |
| `PAI_LAYA_POLL` | `30` | Check period (s). |
| `PAI_LAYA_PROBE_TIMEOUT` | `60` | Probe reply wait (s). |
| `PAI_LAYA_AUTOSTART` / `AUTOINSTALL` | `1` | Start / install the server automatically. |
| `PAI_LAYA_BACKEND` | — | `wasm` for Termux when native backend fails. |
| `PAI_LAYA_VERSION`, `PAI_LAYA_DIR`, `PAI_LAYA_CLI` | — | Server install options. |

### Telegram

| Variable | Default | Meaning |
|----------|---------|---------|
| `PAI_TELEGRAM_POLL_SEC` | `60` | Background poll interval (`0` = off). |
| `PAI_TELEGRAM_MAX_AGE_SEC` | — | Max age of messages considered. |
| `PAI_TELEGRAM_API` | `https://api.telegram.org` | Bot API base URL. |

### Guest mode

`PAI_GUEST_PER_DAY`, `PAI_GUEST_PER_HOUR`, `PAI_GUEST_GLOBAL_PER_DAY`,
`PAI_GUEST_INBOX_PER_HOUR`, `PAI_GUEST_INBOX_PER_DAY`,
`PAI_GUEST_ROOMS_PER_HOUR` — per-IP rate limits for the guest page.

### MCP

`PAI_MCP_CATALOG_URLS` — comma-separated MCP catalog URLs.

---

## Project structure

```
up/
├── README.md, LAYA_README.md, ROLES_README.md, TELEGRAM_*_README.md,
│   PYTHON_FUNCTIONS.md, ITERATIONS.md     — documentation
├── run_server.py, example_run.py          — launchers
├── deploy.sh, restart_server.sh, f.sh, fix.py   — ops helpers
├── pyproject.toml, requirements.txt       — packaging & deps
├── pai/                                   — application package
│   ├── app.py          — FastAPI application factory
│   ├── api_schemas.py, models.py          — Pydantic models
│   ├── settings.py     — env → Settings (single place)
│   ├── llm.py, llm_config.py, model_kinds.py   — LLM clients & config
│   ├── personas.py, skills.py, rules     — registries
│   ├── custom_functions.py, secret_store.py   — functions & secrets
│   ├── storage.py, storage/               — SQLite + WAL storage
│   │   ├── core.py  (connection/schema/WAL)
│   │   ├── nodes.py, chat.py, logs.py, files.py, inbox.py,
│   │   └── guest_rooms.py, store.py, wal.py
│   ├── tree.py         — hierarchical compression (RAPTOR-like)
│   ├── orchestrator/   — base, dialog, context, llm_runner,
│   │                     tool_pool, cache
│   ├── tools/          — dispatcher, schemas, memory, web, shell,
│   │                     persona, proposals, errors
│   ├── prompts/        — core_rules, planner_*, summary, verify,
│   │                     vision, iteration_limit_* templates
│   ├── web/            — routes (chat, memory, personas, rules,
│   │                     functions, settings, tools, services,
│   │                     files, speech, vault, auth, guest, system)
│   │                     + build_* helpers, deps, ui
│   ├── static/         — single-page UI (index.html, app/*.js (loaded in
│   │                     filename order), md.js, style.css, theme-init.js,
│   │                     guest.*, inbox.*, services.*, mcp.html + *-page.js)
│   ├── laya.py, laya_runtime.py        — decision scoring server
│   ├── telegram.py, telegram_agent.py  — Telegram integration
│   │                     (telegram_format.py: Markdown→HTML, splitting;
│   │                      telegram_messages.py: inbound message helpers)
│   ├── mcp_client.py, mcp_catalog.py   — MCP client
│   ├── usage.py, time_utils.py, turns.py, cache_monitor.py
│   └── speech.py, files_extract.py, services.py
├── tests/              — pytest suite (+ mcp_stub_server.py)
└── pai_data/           — runtime data (git-ignored)
```

---

## Testing

```bash
python -m pytest tests          # main suite (pytest is configured in pyproject)
python -m pytest -q tests_laya  # Laya/Jev tests (present after applying Laya builds)
node --test tests/js/*.test.mjs # front-end tests: markdown escaping, app script load order
```

The UI sends a Content-Security-Policy with `script-src 'self'`, so new
front-end code must go in a file under `pai/static/`, never in an inline
`<script>` block or an `on*=` attribute. `tests/test_security_headers.py`
checks this.

`pyproject.toml` sets `testpaths = ["tests"]`, disables the cache provider,
and configures `ruff` (`-p no:cacheprovider`, line length 120). The main
`tests/` suite covers the app, orchestrator, storage, tools, personas,
skills, rules, custom functions, MCP, Telegram, settings, prompts, tool
budget, proposal rules, spaces, and smoke tests.

---

## Security notes

- **API keys / secrets** are kept in a dedicated `SecretStore` and in
  `llm_config.json`, with file permissions tightened to owner-only (0600),
  deliberately kept outside the SQLite memory store so they don't ride along
  with DB backups or exports. Keys are masked in API responses; `http_request`
  supports `{{secret:NAME}}` placeholders without ever showing the value.
- **Auth** — the UI is password-gated unless `PAI_AUTH_DISABLED=1`. CORS
  lists origins explicitly and never uses `*`.
- **Shell access** is a gated tool (`shell_exec`) — only enabled for personas
  that explicitly include it (e.g. `brain`), never by default.
- **Local Laya** binds only to `127.0.0.1` and needs no API key.
- **Before irreversible or externally visible actions** (publishing a
  Telegram post, deleting spaces/nodes, sending messages), the assistant asks
  for explicit confirmation (e.g. `ask_confirmation`) — a public/group post
  always goes into a confirmation queue by default.

---

## Companion documentation

- **`LAYA_README.md`** — Laya/Jev integration, builds v3–v9, provider switch,
  verification, diagnostics (`/laya/status`).
- **`ROLES_README.md`** — models per task (planning / analysis / final).
- **`TELEGRAM_README.md`** — Telegram public/private channels (build 11).
- **`TELEGRAM_GROUPS_README.md`** — Telegram group + reading messages (build 12).
- **`TELEGRAM_AGENT_README.md`** — background auto-reply in groups (build 13).
- **`PYTHON_FUNCTIONS.md`** — Python custom functions reference.
- **`ITERATIONS.md`** — development iteration journal.