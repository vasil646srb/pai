# Personal AI Assistant (pai)

    pip install -r requirements.txt
    uvicorn pai.app:app --host 127.0.0.1 --port 8000     # UI: http://127.0.0.1:8000
    python -m pytest tests                               # smoke tests

Env: `PAI_DATA_DIR` (default ./pai_data), `PAI_TIMEZONE` (IANA, e.g. Europe/Belgrade; default = system zone),
`PAI_TAIL_LENGTH` (chat messages in the prompt tail, default 10), `PAI_LLM_*` (see app.py).

Each persona with `allow_spaces` chats in its own memory space (work -> `work`, tutor -> `study`, ...).
