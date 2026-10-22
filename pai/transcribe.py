"""Speech-to-text: audio and video (uploads and Telegram media) -> text through a Whisper server on the LAN.

The server is any OpenAI-compatible `POST {base}/audio/transcriptions` (multipart: file, model, language,
response_format). Settings live in <data_dir>/transcribe_settings.json; env gives the defaults:
PAI_WHISPER_URL (e.g. http://192.168.1.50:8000/v1), PAI_WHISPER_MODEL, PAI_WHISPER_LANGUAGE, PAI_WHISPER_TIMEOUT.
Traffic goes straight to the LAN, without system proxies (same as the embedding client).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import requests

from .files_extract import NOT_CONFIGURED, TranscribeError, guess_mime, set_recorder, set_transcriber

DEFAULTS = {
    "enabled": False,
    "base_url": "",          # http://host:port/v1 ; empty = not configured
    "model": "whisper-1",    # many local servers ignore the name, some need the exact model id
    "language": "",          # "" = auto-detect; "ru", "en", ...
    "timeout": 600.0,        # seconds; a long file on a CPU can take minutes
    "max_duration": 600,     # Telegram media longer than this (seconds) is not transcribed
}
LIMITS = {"timeout": (5.0, 3600.0), "max_duration": (10, 7200)}


def _from_env(env=os.environ) -> dict:
    base = dict(DEFAULTS)
    url = (env.get("PAI_WHISPER_URL") or "").strip()
    if url:
        base.update(enabled=True, base_url=url.rstrip("/"))
    if env.get("PAI_WHISPER_MODEL"):
        base["model"] = env["PAI_WHISPER_MODEL"].strip()
    if env.get("PAI_WHISPER_LANGUAGE"):
        base["language"] = env["PAI_WHISPER_LANGUAGE"].strip()
    try:
        base["timeout"] = float(env.get("PAI_WHISPER_TIMEOUT", base["timeout"]))
    except ValueError:
        pass
    return base


class TranscribeSettingsStore:
    def __init__(self, path: str | os.PathLike, env=os.environ):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = _from_env(env)
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self._data.update({k: saved[k] for k in DEFAULTS if k in saved})
        except (OSError, ValueError):
            pass

    def get(self) -> dict:
        with self._lock:
            return dict(self._data)

    def update(self, body: dict) -> dict:
        with self._lock:
            new = dict(self._data)
            if "enabled" in body:
                new["enabled"] = bool(body["enabled"])
            if "base_url" in body:
                url = str(body["base_url"] or "").strip().rstrip("/")
                if url and not url.startswith(("http://", "https://")):
                    raise ValueError("адрес сервера должен начинаться с http:// или https://")
                new["base_url"] = url
            if "model" in body:
                new["model"] = str(body["model"] or "").strip() or DEFAULTS["model"]
            if "language" in body:
                new["language"] = str(body["language"] or "").strip()
            for key, (lo, hi) in LIMITS.items():
                if key in body:
                    try:
                        val = float(body[key])
                    except (TypeError, ValueError):
                        raise ValueError(f"{key}: число")
                    new[key] = type(DEFAULTS[key])(max(lo, min(hi, val)))
            if new["enabled"] and not new["base_url"]:
                raise ValueError("чтобы включить распознавание, укажите адрес сервера")
            self._save(new)
            self._data = new
            return dict(new)

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


class WhisperClient:
    def __init__(self, base_url: str, model: str, language: str = "", timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.language = language
        self.timeout = timeout

    def transcribe(self, name: str, data: bytes, mime: str = "") -> str:
        """Returns the recognized text ('' when there is no speech). Raises TranscribeError on failure."""
        form = {"model": self.model, "response_format": "json"}
        if self.language:
            form["language"] = self.language
        session = requests.Session()
        session.trust_env = False  # LAN address: ignore system proxies
        try:
            r = session.post(
                f"{self.base_url}/audio/transcriptions",
                data=form,
                files={"file": (name, data, mime or guess_mime(name) or "application/octet-stream")},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise TranscribeError(f"сервер распознавания недоступен ({type(e).__name__})") from e
        if r.status_code >= 400:
            raise TranscribeError(f"сервер распознавания: HTTP {r.status_code} {r.text[:200]}")
        if "json" in r.headers.get("content-type", ""):
            try:
                text = r.json().get("text", "")
            except (ValueError, AttributeError) as e:
                raise TranscribeError("сервер распознавания вернул не JSON с полем text") from e
        else:
            text = r.text
        return (text or "").strip()


def silent_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """A short WAV of silence, for the connection check."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def ping_server(cfg: dict) -> dict:
    """Sends a second of silence to the server. Returns latency and what came back (usually empty text)."""
    client = WhisperClient(cfg["base_url"], cfg["model"], cfg["language"], min(float(cfg["timeout"]), 120.0))
    t0 = time.time()
    text = client.transcribe("ping.wav", silent_wav(), "audio/wav")
    return {"ok": True, "latency_ms": round((time.time() - t0) * 1000), "model": cfg["model"], "text": text[:200]}


def install(store: TranscribeSettingsStore, log=None) -> None:
    """Connects speech-to-text to uploads and Telegram. Settings are read on every call, so page changes apply at once.
    log: TranscribeLog; every attempt is written there (see transcribe_log.py)."""

    def ready() -> bool:
        cfg = store.get()
        return bool(cfg["enabled"] and cfg["base_url"])

    def run(name: str, data: bytes, duration: int | None = None) -> str:
        cfg = store.get()
        if not ready():
            raise TranscribeError(NOT_CONFIGURED)
        if duration and duration > cfg["max_duration"]:
            raise TranscribeError(f"запись длиннее {cfg['max_duration']} с, не расшифровываю")
        client = WhisperClient(cfg["base_url"], cfg["model"], cfg["language"], cfg["timeout"])
        return client.transcribe(name, data, guess_mime(name))

    set_transcriber(run, ready)
    if log is not None:
        set_recorder(lambda info: log.add(**info))
