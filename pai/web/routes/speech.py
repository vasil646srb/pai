"""Voice output endpoints: the reply is prepared for listening by a text model, then synthesized."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response

from ...api_schemas import SpeechPrepRequest, SpeechSynthesisRequest
from ...llm import LLMError, OpenAICompatibleClient
from ...speech import prepare_speech_text


def register(app: FastAPI) -> None:
    @app.post("/speech/prepare")
    def speech_prepare(req: SpeechPrepRequest):
        llm = app.state.speech_llm
        if not isinstance(llm, OpenAICompatibleClient):
            raise HTTPException(503, "Модель подготовки текста для озвучки не настроена")
        try:
            speech_text = prepare_speech_text(llm, req.text)
        except LLMError as e:
            raise HTTPException(502, f"Подготовка текста для озвучки: {e}") from e
        if not speech_text:
            raise HTTPException(502, "Модель вернула пустой текст для озвучки")
        return {"speech_text": speech_text}

    @app.post("/speech/synthesize")
    def speech_synthesize(req: SpeechSynthesisRequest):
        tts = app.state.tts_client
        if tts is None:
            raise HTTPException(503, "Модель синтеза речи не настроена (Настройки → Озвучка)")
        try:
            audio, mime = tts.synthesize(req.text)
        except LLMError as e:
            raise HTTPException(502, f"Синтез речи: {e}") from e
        return Response(content=audio, media_type=mime, headers={"Cache-Control": "no-store"})
