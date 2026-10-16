# Personal AI Assistant (pai)

A memory-driven personal AI assistant. A core orchestrator routes every turn through
a planning → pool execution → analysis loop, backed by a persistent WAL (write-ahead
log) storage, per-persona memory spaces, and a growing library of tools.

## Quick start

```bash
    pip install -r requirements.txt
    uvicorn pai.app:app --host 127.0.0.1 --port 8000     # UI: http://127.0.0.1:8000
    python -m pytest tests                               # smoke tests
```

## Environment variables

- `PAI_DATA_DIR` — data directory (default `./pai_data`).
- `PAI_TIMEZONE` — IANA timezone, e.g. `Europe/Belgrade` (default: system zone).
- `PAI_TAIL_LENGTH` — number of chat messages included in the prompt tail (default 10).
- `PAI_LLM_*` — LLM provider settings (see `app.py`).

## Memory spaces

Each persona with `allow_spaces` chats in its own dedicated memory space
(work → `work`, tutor → `study`, ...). This keeps prompts and retrieval scoped to
the persona's own domain.