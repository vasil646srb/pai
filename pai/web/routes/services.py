"""Autonomous service endpoints and the background runner wiring."""
from __future__ import annotations

import time

from fastapi import Body, FastAPI, HTTPException

from ...services import ServiceRunner


def register(app: FastAPI) -> None:
    def _execute_service(svc: dict) -> dict:
        if app.state.personas.get(svc["persona_id"]) is None:
            raise RuntimeError(f"персона '{svc['persona_id']}' не найдена")
        origin = {"kind": "service", "id": svc["id"], "name": svc["name"]}
        result = app.state.orchestrator.handle_turn(svc["prompt"], svc["persona_id"], svc["space_id"], origin=origin)
        return {"reply": result.reply}

    app.state.service_runner = ServiceRunner(app.state.services, _execute_service)
    if app.state.settings.services_enabled:
        app.state.service_runner.start()

    def _service_view(svc: dict) -> dict:
        return {**svc, "running": app.state.service_runner.is_running(svc["id"])}

    @app.get("/services")
    def list_services():
        return [_service_view(s) for s in app.state.services.list()]

    @app.get("/services/{sid}")
    def get_service(sid: str):
        svc = app.state.services.get(sid)
        if svc is None:
            raise HTTPException(404, "service not found")
        return _service_view(svc)

    def _check_persona(fields: dict) -> None:
        pid = str(fields.get("persona_id", "")).strip()
        if pid and app.state.personas.get(pid) is None:
            raise HTTPException(400, f"персона '{pid}' не найдена")

    @app.post("/services", status_code=201)
    def create_service(payload: dict = Body(...)):
        _check_persona(payload)
        try:
            svc = app.state.services.create(payload, time.time())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return _service_view(svc)

    @app.put("/services/{sid}")
    def update_service(sid: str, payload: dict = Body(...)):
        _check_persona(payload)
        try:
            svc = app.state.services.update(sid, payload, time.time())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if svc is None:
            raise HTTPException(404, "service not found")
        return _service_view(svc)

    @app.delete("/services/{sid}")
    def delete_service(sid: str):
        if not app.state.services.delete(sid):
            raise HTTPException(404, "service not found")
        return {"status": "deleted"}

    @app.post("/services/{sid}/run", status_code=202)
    def run_service_now(sid: str):
        if app.state.services.get(sid) is None:
            raise HTTPException(404, "service not found")
        started = app.state.service_runner.start_run(sid) is not None
        return {"started": started, "already_running": not started}
