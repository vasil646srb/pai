"""Семантический поиск: API, настройки, статус, пинг сервера эмбеддингов. UI — разделы боковой панели."""
from __future__ import annotations

import time

from fastapi import Body, FastAPI, HTTPException

from ...semantic.config import PRECISION_LEVELS, effective_min_score
from ...semantic.embedder import EmbeddingError
from ...semantic.index import _client


def _settings_view(store) -> dict:
    return {**store.get(), "precision_levels": [{"level": k, **v} for k, v in PRECISION_LEVELS.items()]}


def register(app: FastAPI) -> None:
    @app.get("/semantic/status")
    def sem_status():
        return app.state.semantic.status()

    @app.get("/semantic/settings")
    def sem_settings_get():
        return _settings_view(app.state.semantic_store)

    @app.put("/semantic/settings")
    def sem_settings_put(body: dict = Body(...)):
        try:
            app.state.semantic_store.update(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if {"model", "dim", "chunk_size", "chunk_overlap"} & set(body):
            app.state.semantic.reindex()
        return _settings_view(app.state.semantic_store)

    @app.post("/semantic/reindex")
    def sem_reindex():
        app.state.semantic.reindex()
        return {"ok": True}

    @app.post("/semantic/ping")
    def sem_ping():
        cfg = app.state.semantic_store.get()
        t0 = time.time()
        try:
            v = _client(cfg).embed(["ping"])
        except EmbeddingError as e:
            raise HTTPException(502, str(e)) from e
        return {"ok": True, "dim": int(v.shape[1]), "latency_ms": round((time.time() - t0) * 1000)}

    @app.post("/semantic/search")
    def sem_search(body: dict = Body(...)):
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(400, "пустой запрос")
        cfg = app.state.semantic_store.get()
        if not cfg["enabled"]:
            raise HTTPException(409, "семантический индекс выключен в разделе «Эмбеддинги»")
        try:
            precision = int(body.get("precision") or cfg["precision"])
            top_k = max(1, min(int(body.get("top_k") or cfg["top_k"]), 200))
            if precision not in PRECISION_LEVELS:
                raise ValueError
        except (TypeError, ValueError) as e:
            raise HTTPException(400, "precision: 1..5, top_k: число") from e
        min_score = effective_min_score(precision, cfg["min_score"])
        try:
            results = app.state.semantic.search(
                query, space_id=body.get("space_id") or None, top_k=top_k, min_score=min_score)
        except EmbeddingError as e:
            raise HTTPException(502, f"сервер эмбеддингов недоступен: {e}") from e
        return {"query": query, "count": len(results), "min_score": min_score, "results": results}
