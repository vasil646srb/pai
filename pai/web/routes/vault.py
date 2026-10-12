"""Owner-only management of secrets used by the brain persona. Values are write-only."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


def register(app: FastAPI) -> None:
    @app.get("/secrets")
    def list_secrets():
        return {"names": app.state.secret_store.names()}

    @app.put("/secrets/{name}")
    def put_secret(name: str, body: SecretIn):
        try:
            app.state.secret_store.set(name, body.value)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "names": app.state.secret_store.names()}

    @app.delete("/secrets/{name}")
    def delete_secret(name: str):
        if not app.state.secret_store.delete(name):
            raise HTTPException(404, "нет такого секрета")
        return {"ok": True, "names": app.state.secret_store.names()}
