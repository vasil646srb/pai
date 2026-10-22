"""Speech recognition: settings, connection check, history of processed files. UI: section «Распознавание речи»."""
from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException

from ...files_extract import TranscribeError
from ...transcribe import ping_server
from ...transcribe_log import SOURCES, STATUSES


def _settings_view(store) -> dict:
    cfg = store.get()
    return {**cfg, "ready": bool(cfg["enabled"] and cfg["base_url"])}


def register(app: FastAPI) -> None:
    @app.get("/transcribe/settings")
    def tr_settings_get():
        return _settings_view(app.state.transcribe_store)

    @app.put("/transcribe/settings")
    def tr_settings_put(body: dict = Body(...)):
        try:
            app.state.transcribe_store.update(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return _settings_view(app.state.transcribe_store)

    @app.post("/transcribe/ping")
    def tr_ping():
        cfg = app.state.transcribe_store.get()
        if not cfg["base_url"]:
            raise HTTPException(409, "адрес сервера не задан")
        try:
            return ping_server(cfg)
        except TranscribeError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/transcribe/history")
    def tr_history(limit: int = 50, source: str = "", status: str = ""):
        log = app.state.transcribe_log
        return {
            "summary": log.summary(),
            "items": log.list(limit=limit, source=source if source in SOURCES else "",
                              status=status if status in STATUSES else ""),
            "sources": SOURCES,
        }

    @app.delete("/transcribe/history")
    def tr_history_clear():
        app.state.transcribe_log.clear()
        return {"ok": True}
