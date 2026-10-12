"""Sign-in endpoints: status (public), login (public, rate-limited), logout and check (need a token)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import TOKEN_TTL_SECONDS, bearer_token


class LoginRequest(BaseModel):
    password: str = Field(default="", max_length=500)


def register(app: FastAPI) -> None:
    @app.get("/auth/status")
    def auth_status():
        store = app.state.auth
        return {"required": not store.disabled, "configured": store.enabled}

    @app.post("/auth/login")
    def login(req: LoginRequest, request: Request):
        store = app.state.auth
        if not store.enabled:
            raise HTTPException(503, "Вход не настроен: задайте PAI_PASSWORD на сервере")
        ip = request.client.host if request.client else "unknown"
        try:
            token = store.login(ip, req.password)
        except PermissionError as e:
            raise HTTPException(429, f"Слишком много попыток. Подождите {e.args[0]} с.") from e
        if token is None:
            raise HTTPException(401, "Неверный пароль")
        return {"token": token, "ttl_seconds": TOKEN_TTL_SECONDS}

    @app.post("/auth/logout")
    def logout(request: Request):
        app.state.auth.revoke(bearer_token(request))
        return {"ok": True}

    @app.get("/auth/check")
    def check():
        return {"ok": True}
