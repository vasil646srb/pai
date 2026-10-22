"""Настройки семантического поиска (JSON в data_dir) и шкала точности."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# Точность -> минимальный cosine. Ориентировочные значения для нормированных векторов;
# под конкретную модель откалибруйте по своим данным (ручной порог на странице /search).
PRECISION_LEVELS = {
    1: {"label": "широкий", "min_score": 0.50},
    2: {"label": "свободный", "min_score": 0.60},
    3: {"label": "обычный", "min_score": 0.68},
    4: {"label": "строгий", "min_score": 0.75},
    5: {"label": "точный", "min_score": 0.82},
}

ALLOWED_DIMS = (0, 128, 256, 512, 768)  # 0 = полная размерность модели

DEFAULTS = {
    "enabled": False,
    "base_url": "http://127.0.0.1:8080/v1",
    "api_format": "openai",  # "openai" (/v1/embeddings) или "texts" (/embed)
    "model": "embeddinggemma-300m",
    "dim": 256,
    "precision": 3,
    "min_score": None,      # ручной порог; если задан, перекрывает шкалу точности
    "top_k": 8,
    "chunk_size": 800,
    "chunk_overlap": 100,
}


def effective_min_score(precision: int, override: float | None) -> float:
    if override is not None:
        return float(override)
    return float(PRECISION_LEVELS[int(precision)]["min_score"])


class SemanticSettingsStore:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
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
            try:
                if "enabled" in body:
                    new["enabled"] = bool(body["enabled"])
                if "base_url" in body:
                    url = str(body["base_url"]).strip().rstrip("/")
                    if not url.startswith(("http://", "https://")):
                        raise ValueError("base_url должен начинаться с http:// или https://")
                    new["base_url"] = url
                if "model" in body:
                    new["model"] = str(body["model"]).strip()[:200] or DEFAULTS["model"]
                if "api_format" in body:
                    f = str(body["api_format"]).strip()
                    if f not in ("openai", "texts"):
                        raise ValueError("api_format: openai или texts")
                    new["api_format"] = f
                if "dim" in body:
                    d = int(body["dim"])
                    if d not in ALLOWED_DIMS:
                        raise ValueError("dim: 0 (полная), 128, 256, 512 или 768")
                    new["dim"] = d
                if "precision" in body:
                    p = int(body["precision"])
                    if p not in PRECISION_LEVELS:
                        raise ValueError("precision: 1..5")
                    new["precision"] = p
                if "min_score" in body:
                    v = body["min_score"]
                    if v in (None, ""):
                        new["min_score"] = None
                    else:
                        f = float(v)
                        if not 0.0 <= f <= 1.0:
                            raise ValueError("min_score: 0..1")
                        new["min_score"] = f
                if "top_k" in body:
                    k = int(body["top_k"])
                    if not 1 <= k <= 200:
                        raise ValueError("top_k: 1..200")
                    new["top_k"] = k
                if "chunk_size" in body:
                    s = int(body["chunk_size"])
                    if not 200 <= s <= 4000:
                        raise ValueError("chunk_size: 200..4000")
                    new["chunk_size"] = s
                if "chunk_overlap" in body:
                    o = int(body["chunk_overlap"])
                    if not 0 <= o <= new["chunk_size"] // 2:
                        raise ValueError("chunk_overlap: 0..половина chunk_size")
                    new["chunk_overlap"] = o
            except (TypeError, ValueError) as e:
                raise ValueError(str(e)) from e
            self._data = new
            self._save()
            return dict(new)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
