"""Пример запуска core-системы с MockLLM (без реального инференса) и с
реальным OpenAI-совместимым эндпоинтом (закомментировано ниже).

Запуск: python3 example_run.py
"""
from pai.llm import MockLLM  # , OpenAICompatibleClient
from pai.orchestrator import Orchestrator
from pai.personas import default_personas
from pai.skills import default_skill_catalog
from pai.storage import Storage


def main():
    storage = Storage("./pai_data")
    llm = MockLLM()  # замените на:
    # llm = OpenAICompatibleClient(base_url="http://localhost:8000/v1", model="Qwen3-30B-A3B")

    personas = default_personas()
    skills = default_skill_catalog()
    orch = Orchestrator(storage, llm, personas, skills, timezone_name="MSK")

    llm.queue_response(content="Привет! Я готов помочь.")
    result = orch.handle_turn("Привет, расскажи, что ты умеешь", persona_id="brain")
    print("ASSISTANT:", result.reply)
    print("USAGE:", result.usage)

    # Фоновое сжатие истории в дерево (обычно запускается по расписанию/после N сообщений)
    for i in range(5):
        llm.queue_response(content=f"ответ {i}")
        orch.handle_turn(f"сообщение номер {i}", persona_id="brain")

    created = orch.run_background_compression(space_id="personal")
    print(f"Создано {len(created)} узлов саммари")
    print("Stats:", storage.stats())

    storage.close()


if __name__ == "__main__":
    main()
