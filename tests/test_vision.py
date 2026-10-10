import json
import tempfile

from fastapi.testclient import TestClient

from pai.app import create_app
from pai.llm import MockLLM

IMG = "data:image/png;base64,AAAA"


def _app(main, vision):
    app = create_app(data_dir=tempfile.mkdtemp(), llm=main)
    app.state.orchestrator.vision_llm = vision
    return app


def _sse(client, **body):
    with client.stream("POST", "/chat/stream", json=body) as r:
        return [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]


def _user_content(main, idx=0):
    return main.calls[idx]["messages"][-1]["content"]


def test_images_go_through_vision_model_and_main_gets_description():
    main, vis = MockLLM(), MockLLM()
    vis.queue_response(content="На фото счёт на 450 рублей")
    main.queue_response(content="Счёт на 450 рублей.")
    c = TestClient(_app(main, vis))
    done = _sse(c, text="сколько по счёту?", persona_id="brain", images=[IMG])[-1]
    assert done["reply"] == "Счёт на 450 рублей."
    assert len(vis.calls) == 1
    content = _user_content(main)
    assert isinstance(content, str), "main model must get text, not image parts"
    assert "На фото счёт на 450 рублей" in content and "сколько по счёту?" in content


def test_no_vision_model_keeps_images_for_main_model():
    main = MockLLM()
    main.queue_response(content="ok")
    c = TestClient(_app(main, None))
    _sse(c, text="что на фото?", persona_id="brain", images=[IMG])
    content = _user_content(main)
    assert isinstance(content, list)
    assert any(p.get("type") == "image_url" for p in content)


def test_failed_vision_falls_back_to_main_with_images():
    class Broken:
        model = "vision-x"
        def chat(self, **kw):
            raise RuntimeError("no vision")
    main = MockLLM()
    main.queue_response(content="ok")
    c = TestClient(_app(main, Broken()))
    _sse(c, text="что на фото?", persona_id="brain", images=[IMG])
    content = _user_content(main)
    assert isinstance(content, list)
    assert any(p.get("type") == "image_url" for p in content)


def test_vision_setting_persists_and_defaults_empty():

    from pai.llm_config import LLMConfig, LLMConfigStore
    d = tempfile.mkdtemp()
    assert LLMConfig(model="m").effective_vision_model() == ""
    store = LLMConfigStore(d)
    store.save(LLMConfig(model="m", vision_model="qwen-vl"))
    assert store.load().effective_vision_model() == "qwen-vl"
