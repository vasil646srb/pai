"""Uploaded files. Original bytes live on disk (compressed when the type is compressible),
metadata and extracted text live in SQLite. The WAL only ever refers to file ids."""

from __future__ import annotations

import gzip
import hashlib
import os
import time
import uuid
from pathlib import Path

from ..files_extract import ALLOWED_EXTS, MAX_UPLOAD_BYTES, clean_name, extract_text, guess_mime

try:  # optional: better ratio and speed than gzip
    import zstandard as _zstd
except ImportError:  # pragma: no cover - depends on the install
    _zstd = None

_COMPRESSIBLE = ("text/", "application/json", "application/xml", "application/javascript", "application/x-yaml")
_EXT = {"zstd": ".zst", "gzip": ".gz", "raw": ".bin"}


def _compress(data: bytes, mime: str) -> tuple[bytes, str]:
    if not mime.startswith(_COMPRESSIBLE) or len(data) < 512:
        return data, "raw"
    if _zstd is not None:
        return _zstd.ZstdCompressor(level=3).compress(data), "zstd"
    return gzip.compress(data, compresslevel=6), "gzip"


def _decompress(blob: bytes, codec: str) -> bytes:
    if codec == "zstd":
        return _zstd.ZstdDecompressor().decompress(blob)
    if codec == "gzip":
        return gzip.decompress(blob)
    return blob


class FilesMixin:
    """Upload storage. Expects `data_dir`, `_conn` and `_lock` from StorageCore."""

    def _file_path(self, sha: str, codec: str) -> Path:
        return self.data_dir / "files" / sha[:2] / f"{sha}{_EXT.get(codec, '.bin')}"

    def store_upload(self, name: str, data: bytes, space_id: str = "personal") -> dict:
        name = clean_name(name)
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_EXTS:
            raise ValueError(f"Тип файла не поддерживается: {ext or 'без расширения'}")
        if not data:
            raise ValueError("Пустой файл")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("Файл больше 20 МБ")

        sha = hashlib.sha256(data).hexdigest()
        with self._lock:
            row = self._conn.execute("SELECT id FROM uploaded_files WHERE sha256=? LIMIT 1", (sha,)).fetchone()
        if row:  # same bytes already stored: reuse, no second copy
            return self.file_meta(row[0])

        mime = guess_mime(name)
        text, note = extract_text(name, data)  # CPU work, outside the lock
        blob, codec = _compress(data, mime)
        path = self._file_path(sha, codec)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, path)  # atomic: no half-written files

        fid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO uploaded_files (id, sha256, space_id, name, mime, size_orig, size_stored, codec, note, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (fid, sha, space_id, name, mime, len(data), len(blob), codec, note, time.time()),
            )
            if text:
                tblob, tcodec = _compress(text.encode("utf-8"), "text/plain")
                self._conn.execute(
                    "INSERT INTO uploaded_file_text (file_id, chars, codec, body) VALUES (?,?,?,?)",
                    (fid, len(text), tcodec, tblob),
                )
            self._conn.commit()
        return self.file_meta(fid)

    def file_meta(self, fid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM uploaded_files WHERE id=?", (fid,)).fetchone()
            t = self._conn.execute("SELECT chars FROM uploaded_file_text WHERE file_id=?", (fid,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"], "name": row["name"], "mime": row["mime"], "size": row["size_orig"],
            "stored_size": row["size_stored"], "codec": row["codec"], "space_id": row["space_id"],
            "has_text": t is not None, "text_chars": t["chars"] if t else 0,
            "note": row["note"], "created_at": row["created_at"],
        }

    def read_file_bytes(self, fid: str) -> tuple[str, str, bytes] | None:
        """(name, mime, original bytes) or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT sha256, codec, name, mime FROM uploaded_files WHERE id=?", (fid,)).fetchone()
        if row is None:
            return None
        blob = self._file_path(row["sha256"], row["codec"]).read_bytes()
        return row["name"], row["mime"], _decompress(blob, row["codec"])

    def read_file_text(self, fid: str) -> str | None:
        """Fast path for the model: extracted text only, original is not touched."""
        with self._lock:
            row = self._conn.execute(
                "SELECT codec, body FROM uploaded_file_text WHERE file_id=?", (fid,)).fetchone()
        if row is None:
            return None
        return _decompress(row["body"], row["codec"]).decode("utf-8")
