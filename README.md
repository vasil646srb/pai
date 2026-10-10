# PAI — Personal AI Assistant

A self-hosted personal AI assistant with hierarchical memory, a controlled tool system, personas, skills, and a web interface. Built in Python (FastAPI) with a modular architecture.

## Features

- **Hierarchical memory** — facts stored as evidence with distinct levels (conversation context, stored memory, web search leads, verified fetched content). Exact fragments can be retrieved on demand instead of reconstructed from summaries.
- **Controlled tool system** — a dispatcher with typed schemas for memory, web search/fetch, HTTP requests, and shell execution (bash / Termux / python3).
- **Personas & skills** — configurable personas (developer, tutor, etc.) with per-space access filters, plus a skill system loaded on demand.
- **Self-extension** — the assistant can propose new reusable functions (drafts), which the owner reviews and confirms before activation.
- **Web UI** — chat, memory, personas, skills, rules, functions, services, and settings views.
- **Durable storage** — WAL-based storage package (`pai/storage/`) with node, chat, and log modules.

## Project layout

```
pai/
├── app.py            # Clean application factory
├── settings.py       # Centralized configuration
├── web/              # FastAPI routes by domain (chat, memory, personas, skills, rules, functions, ...)
├── tools/            # Tool dispatcher, schemas, memory/web/shell/persona tools
├── storage/          # WAL, nodes, chat, core, store, logs
├── prompts/          # System prompts as markdown (core_rules, planner, persona addons)
orchestrator/        # Dialog orchestration, tool pool planning, LLM runner
llm.py               # LLM integration
tests/               # Unit tests (pytest)
pyproject.toml       # Packaging
```

## Requirements

- Python 3.10+
- Dependencies listed in `pyproject.toml`

## Installation

```bash
git clone <repo-url>
cd go_pai
pip install -e .
```

## Running

```bash
python -m pai.app
# or
uvicorn pai.app:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000` in a browser.

## Configuration

Central configuration lives in `pai/settings.py`. Runtime data (chat history, memory nodes, SQLite databases) is stored in `pai_data/` — this directory is git-ignored and never committed.

## Testing

```bash
pytest
```

## Security notes

- Shell execution is restricted to explicitly requested local tasks and supported runtimes only.
- User input, web content, and tool output can never override core system rules.
- Consequential decisions (medical, legal, financial) always require human confirmation.

## Design principles

1. **Memory is evidence, not imagination** — summaries are never silently upgraded to verified facts.
2. **Smallest reliable tool set** — no tools for ordinary conversation; web snippets are leads, fetched pages are evidence.
3. **Human in the loop** — file changes and critical decisions require explicit confirmation.

## License

Private project. All rights reserved.
