"""Owner request backlog (owner password): /requests — create, list, mark done, delete."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ...storage import Storage
from ..deps import get_storage

REQUEST_TEXT = 2000


class RequestIn(BaseModel):
    text: str = Field(default="", max_length=REQUEST_TEXT)


class RequestDone(BaseModel):
    done: bool = True


def register(app):
    @app.post("/requests")
    def create_request(req: RequestIn, storage: Storage = Depends(get_storage)):
        text = req.text.strip()
        if not text:
            raise HTTPException(400, "Напишите текст запроса")
        return storage.add_request(text)

    @app.get("/requests")
    def list_requests(include_done: bool = True, storage: Storage = Depends(get_storage)):
        return storage.list_requests(include_done=include_done)

    @app.patch("/requests/{rid}")
    def update_request(rid: str, req: RequestDone, storage: Storage = Depends(get_storage)):
        if not storage.set_request_done(rid, req.done):
            raise HTTPException(404, "Запрос не найден")
        return {"ok": True}

    @app.delete("/requests/{rid}")
    def delete_request(rid: str, storage: Storage = Depends(get_storage)):
        if not storage.delete_request(rid):
            raise HTTPException(404, "Запрос не найден")
        return {"ok": True}
