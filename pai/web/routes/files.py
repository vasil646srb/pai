"""Uploads: POST /files, GET /files/{id}/meta, GET /files/{id} (original download)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response

from ...files_extract import MAX_UPLOAD_BYTES
from ...storage import Storage
from ..deps import get_storage


def register(app: FastAPI) -> None:
    @app.post("/files")
    async def upload_file(
        file: UploadFile = File(...),
        space_id: str = "personal",
        storage: Storage = Depends(get_storage),
    ):
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Файл больше 20 МБ")
        try:
            return storage.store_upload(file.filename or "file", data, space_id=space_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/files/{file_id}/meta")
    def file_meta(file_id: str, storage: Storage = Depends(get_storage)):
        meta = storage.file_meta(file_id)
        if meta is None:
            raise HTTPException(404, "Файл не найден")
        return meta

    @app.get("/files/{file_id}")
    def download_file(file_id: str, storage: Storage = Depends(get_storage)):
        got = storage.read_file_bytes(file_id)
        if got is None:
            raise HTTPException(404, "Файл не найден")
        name, mime, data = got
        return Response(
            content=data,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
