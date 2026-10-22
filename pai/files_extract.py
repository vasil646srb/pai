"""Uploaded file -> plain text for the model and for memory.

Text formats are decoded as UTF-8. DOCX is read from its XML (no extra dependency).
PDF needs `pypdf`; without it the file is stored with a note and no text.
Audio and video are transcribed by the speech-to-text hook (see transcribe.py); without it they are stored without text.
Other types are stored without text."""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from typing import Callable
from xml.etree import ElementTree as ET

MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # per file, as uploaded
MAX_EXTRACT_CHARS = 500_000              # stored text cap per file
MAX_UNPACKED_BYTES = 200 * 1024 * 1024   # DOCX zip-bomb guard (sum of declared sizes)
MAX_PDF_PAGES = 300

TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".log", ".ini", ".toml",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh", ".sql", ".rs", ".go", ".java",
    ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".kt",
}
MEDIA_EXTS = {
    ".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".flac",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi",
}
ALLOWED_EXTS = TEXT_EXTS | {".pdf", ".docx"} | MEDIA_EXTS


_log = logging.getLogger(__name__)
NOT_CONFIGURED = "распознавание речи не настроено (задайте PAI_WHISPER_URL или transcribe_settings.json в папке данных)"


class TranscribeError(RuntimeError):
    """Speech recognition is not set up, the server is unreachable, or it refused the file."""


_transcriber: Callable[[str, bytes, int | None], str] | None = None
_transcriber_ready: Callable[[], bool] = lambda: False
_recorder: Callable[[dict], None] | None = None  # receives one dict per attempt (history)


def set_transcriber(fn: Callable[[str, bytes, int | None], str], ready: Callable[[], bool]) -> None:
    """Called once at startup. fn(name, data, duration_seconds) -> text; ready() says whether it can run now."""
    global _transcriber, _transcriber_ready
    _transcriber, _transcriber_ready = fn, ready


def set_recorder(fn: Callable[[dict], None]) -> None:
    global _recorder
    _recorder = fn


def transcription_ready() -> bool:
    return _transcriber is not None and bool(_transcriber_ready())


def transcribe(name: str, data: bytes, duration: int | None = None, source: str = "upload") -> str:
    """Text of an audio or video file. Every attempt is reported to the recorder (history).
    source: upload | telegram-group | telegram-owner. Raises TranscribeError when it cannot be done."""
    info = {"name": name, "kind": os.path.splitext(name)[1].lower().lstrip("."), "size": len(data),
            "duration": duration or 0, "source": source}
    try:
        if not transcription_ready():
            raise TranscribeError(NOT_CONFIGURED)
        text = (_transcriber(name, data, duration) or "").strip()
    except Exception as e:
        _report(info, "error", error=str(e) or type(e).__name__)
        raise
    _report(info, "ok" if text else "empty", text=text)
    return text


def _report(info: dict, status: str, text: str = "", error: str = "") -> None:
    if _recorder is None:
        return
    try:
        _recorder({**info, "status": status, "text": text, "error": error})
    except Exception:  # history is best-effort: it never breaks recognition
        _log.warning("transcription history write failed", exc_info=True)


_MIME = {
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mpeg",
    ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".json": "application/json",
    ".md": "text/markdown",
    ".csv": "text/csv",
}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def guess_mime(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in _MIME:
        return _MIME[ext]
    return "text/plain" if ext in TEXT_EXTS else "application/octet-stream"


def clean_name(name: str) -> str:
    base = os.path.basename((name or "file").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f\"]", "", base).strip()[:200]
    return base or "file"


def extract_text(name: str, data: bytes) -> tuple[str | None, str]:
    """Return (text or None, note). The note explains why there is no text, or says it was truncated."""
    ext = os.path.splitext(name)[1].lower()
    try:
        if ext in TEXT_EXTS:
            if b"\x00" in data[:8000]:
                return None, "binary content"
            text = data.decode("utf-8", errors="replace")
        elif ext == ".docx":
            text = _docx_text(data)
        elif ext == ".pdf":
            text = _pdf_text(data)
            if text is None:
                return None, "PDF text needs the pypdf package"
        elif ext in MEDIA_EXTS:
            try:
                text = transcribe(name, data)
            except TranscribeError as e:
                return None, str(e)
        else:
            return None, "stored without text (type not readable)"
    except Exception as e:  # corrupt or unsupported file: keep the upload, drop the text
        return None, f"could not read: {type(e).__name__}"
    text = text.strip()
    if not text:
        return None, "no text found"
    if len(text) > MAX_EXTRACT_CHARS:
        return text[:MAX_EXTRACT_CHARS], "truncated"
    return text, ""


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED_BYTES:
            raise ValueError("DOCX too large when unpacked")
        root = ET.fromstring(z.read("word/document.xml"))
    paras = ["".join(t.text or "" for t in p.iter(_W + "t")) for p in root.iter(_W + "p")]
    return "\n".join(paras)


def _pdf_text(data: bytes) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])
