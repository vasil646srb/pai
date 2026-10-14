"""Voice output: text preparation for speech and speech synthesis (OpenAI-compatible /audio/speech)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .llm import LLMClient, LLMError

SPEECH_PREP_INSTRUCTION = (
    "Преобразуй текст ответа ассистента в текст для озвучки вслух. "
    "Правила: убери markdown (заголовки, жирный/курсив, символы списков, ссылки оставь как название без адреса); "
    "таблицы и код не читай построчно — опиши их одной-двумя фразами по смыслу; "
    "числа, сокращения и формулы запиши так, как их произнёс бы человек; "
    "сохрани язык, смысл и порядок мыслей исходного текста; ничего не добавляй от себя. "
    "Верни только готовый текст для озвучки, без пояснений и кавычек."
)

TTS_CHUNK_CHARS = 3500  # provider limit for one /audio/speech request is 4096 characters


def prepare_speech_text(llm: LLMClient, text: str) -> str:
    result = llm.chat(
        messages=[
            {"role": "system", "content": SPEECH_PREP_INSTRUCTION},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
        max_tokens=max(1024, min(8000, 1024 + len(text) // 2)),
    )
    return (result.get("content") or "").strip()


def split_for_tts(text: str, limit: int = TTS_CHUNK_CHARS) -> list[str]:
    """Cut at whitespace where possible so words are not split between requests."""
    chunks = re.findall(rf".{{1,{limit}}}(?=\s|$)|.{{1,{limit}}}", text, flags=re.S)
    return [c.strip() for c in chunks if c.strip()]


class TTSClient:
    """Speech synthesis through an OpenAI-compatible `POST {base}/audio/speech`."""

    def __init__(self, base_url: str, api_key: str, model: str, voice: str,
                 extra_headers: dict[str, str] | None = None, timeout: float = 120.0,
                 response_format: str = "mp3"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.voice = (voice or "").strip()  # empty = do not send; the provider's default voice applies
        self.extra_headers = extra_headers or {}
        self.timeout = timeout
        self.response_format = response_format

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for k, v in self.extra_headers.items():
            try:
                v.encode("latin-1")
                headers[k] = v
            except UnicodeEncodeError:
                headers[k] = v.encode("ascii", errors="ignore").decode("ascii") or "pai"
        return headers

    def _synthesize_one(self, text: str) -> tuple[bytes, str]:
        url = f"{self.base_url}/audio/speech"
        payload = {"model": self.model, "input": text, "response_format": self.response_format}
        if self.voice:
            payload["voice"] = self.voice
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", "audio/mpeg")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise LLMError(f"HTTP {e.code} from {url}: {body[:500]}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"Failed to reach {url}: {e}") from e
        except TimeoutError as e:
            raise LLMError(f"Timeout after {self.timeout:.0f}s waiting for {url}") from e

    def synthesize(self, text: str) -> tuple[bytes, str]:
        """Long texts are split into several requests; mp3 frames concatenate into one playable stream."""
        parts = split_for_tts(text)
        if not parts:
            raise LLMError("Пустой текст для синтеза речи")
        audio = b""
        mime = "audio/mpeg"
        for part in parts:
            chunk, mime = self._synthesize_one(part)
            audio += chunk
        return audio, mime
