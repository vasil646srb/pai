"""Uploaded file -> plain text for the model and for memory.

Text formats are decoded as UTF-8. DOCX is read from its XML (no extra dependency).
PDF needs `pypdf`; without it the file is stored with a note and no text.
Other types are stored without text."""

from __future__ import annotations

import io
import os
import re
import zipfile
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
ALLOWED_EXTS = TEXT_EXTS | {".pdf", ".docx"}

_MIME = {
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
