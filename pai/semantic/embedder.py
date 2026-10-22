"""Клиент эмбеддингов. Два формата API:
- "openai": POST {base}/embeddings, тело {"model", "input"}, ответ {"data":[{"embedding","index"}]}
  (llama-server --embeddings, Ollama, LM Studio). base обычно заканчивается на /v1.
- "texts":  POST {base}/embed, тело {"texts"}, ответ {"embeddings": [[...], ...]}
  (свой сервер эмбеддингов). base без /v1, например http://192.168.1.198:8000.
Ходит в LAN напрямую, без системных прокси.
"""
from __future__ import annotations

import numpy as np
import requests

# Формат промптов EmbeddingGemma: документы и запросы кодируются по-разному.
DOC_PREFIX = "title: none | text: "
QUERY_PREFIX = "task: search result | query: "
API_FORMATS = ("openai", "texts")


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, timeout: float = 120.0,
                 dim: int | None = None, api: str = "openai"):
        if api not in API_FORMATS:
            raise EmbeddingError(f"неизвестный формат API: {api}")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.dim = dim or None  # None = полная размерность
        self.api = api
        self._http = requests.Session()
        self._http.trust_env = False  # без системных HTTP(S)_PROXY (важно в Termux)

    def _raw_vectors(self, texts: list[str]) -> list[list[float]]:
        if self.api == "texts":
            r = self._http.post(f"{self.base_url}/embed", json={"texts": texts}, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["embeddings"]
        r = self._http.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": texts},
            timeout=self.timeout,
        )
        r.raise_for_status()
        items = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in items]

    def embed(self, texts: list[str]) -> np.ndarray:
        """Матрица (n, dim) float32, строки нормированы до длины 1 (cosine = скалярное произведение)."""
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        try:
            raw = self._raw_vectors(texts)
        except (requests.RequestException, KeyError, ValueError, TypeError) as e:
            raise EmbeddingError(f"сервер эмбеддингов ({self.base_url}, {self.api}): {e}") from e
        vecs = np.asarray(raw, dtype=np.float32)
        if vecs.ndim != 2 or vecs.shape[0] != len(texts):
            raise EmbeddingError("сервер вернул не столько векторов, сколько текстов")
        if self.dim and self.dim < vecs.shape[1]:
            vecs = vecs[:, : self.dim]  # Matryoshka: первые N измерений
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms
