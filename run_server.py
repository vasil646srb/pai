"""Запуск веб-сервера.

Использование:
    python3 run_server.py

Переменные окружения (для реального LLM вместо MockLLM):
    PAI_LLM_BASE_URL   — напр. http://localhost:8000/v1 (vLLM) или
                          https://openrouter.ai/api/v1 (OpenRouter)
    PAI_LLM_API_KEY    — опционально для vLLM, обязательно для OpenRouter
    PAI_LLM_MODEL      — напр. Qwen3-30B-A3B или qwen/qwen3-30b-a3b (OpenRouter)
    PAI_LLM_SITE_URL   — опционально, для OpenRouter (атрибуция в рейтинге).
                          Только ASCII — нелатинские символы игнорируются.
    PAI_LLM_SITE_NAME  — опционально, аналогично. Только ASCII.

Переменные окружения сервера (необязательные, есть значения по умолчанию):
    PAI_HOST      — по умолчанию 0.0.0.0
    PAI_PORT      — по умолчанию 8080
    PAI_DATA_DIR  — по умолчанию ./pai_data

Без PAI_LLM_BASE_URL сервер использует MockLLM (для теста API без инференса).

Эндпоинты и их описание: см. докстринг в pai/app.py, либо после запуска
открыть http://localhost:8080/docs (автогенерируемая Swagger-документация FastAPI).
"""
import os

import uvicorn

from pai.app import create_app

if __name__ == "__main__":
    host = os.environ.get("PAI_HOST", "0.0.0.0")
    port = int(os.environ.get("PAI_PORT", "8080"))
    data_dir = os.environ.get("PAI_DATA_DIR", "./pai_data")
    app = create_app(data_dir=data_dir)
    uvicorn.run(app, host=host, port=port)
