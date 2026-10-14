import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("PAI_DATA_DIR", str(tmp_path)); monkeypatch.setenv("PAI_AUTH_DISABLED", "1")
    monkeypatch.setenv("PAI_DECISION_PROVIDER", "laya"); monkeypatch.setenv("PAI_LAYA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("PAI_LAYA_AUTOSTART", "0")
    from pai.app import create_app
    return create_app(data_dir=str(tmp_path))

BASE = {"base_url": "https://openrouter.ai/api/v1", "api_key": "k", "model": "openai/gpt-oss-120b"}

def test_config_effective_and_roundtrip(tmp_path):
    from pai.llm_config import LLMConfig, LLMConfigStore
    c = LLMConfig(model="main/x")
    assert c.effective_planner_model() == "main/x"          # пусто = основная
    c2 = LLMConfig(model="main/x", planner_model="cheap/y")
    assert c2.effective_planner_model() == "cheap/y"
    store = LLMConfigStore(str(tmp_path)); store.save(c2)
    assert store.load().planner_model == "cheap/y"

def test_builder_none_when_not_separate():
    from pai.llm_config import LLMConfig
    from pai.web.builders import build_planner_llm_client
    assert build_planner_llm_client(LLMConfig(model="a/b")) is None
    assert build_planner_llm_client(LLMConfig(model="a/b", planner_model="a/b")) is None
    cl = build_planner_llm_client(LLMConfig(base_url="https://openrouter.ai/api/v1", api_key="k", model="a/b", planner_model="c/d"))
    assert cl is not None and cl.model == "c/d"

def test_route_sets_planner_and_persists(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app = _app(tmp_path, monkeypatch); c = TestClient(app)
    r = c.put("/settings/llm", json={**BASE, "planner_model": "cheap/planner"})
    assert r.status_code == 200, r.text
    assert app.state.orchestrator.planner_llm.model == "cheap/planner"
    g = c.get("/settings/llm").json()
    assert g["planner_model"] == "cheap/planner"
    assert r.json()["effective_planner_model"] == "cheap/planner"
    c.put("/settings/llm", json={"planner_model": ""})
    assert app.state.orchestrator.planner_llm is None       # очистка -> основная модель
    c.put("/settings/llm", json={"planner_model": "cheap/planner"})
    app2 = _app(tmp_path, monkeypatch)                         # перезапуск: значение из файла
    assert app2.state.orchestrator.planner_llm is not None and app2.state.orchestrator.planner_llm.model == "cheap/planner"

def test_summarizer_and_verify_unaffected(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app = _app(tmp_path, monkeypatch); c = TestClient(app)
    c.put("/settings/llm", json={**BASE, "planner_model": "cheap/planner", "summarizer_model": "s/one", "verify_model": "v/two"})
    g = c.get("/settings/llm").json()
    assert g["summarizer_model"] == "s/one" and g["verify_model"] == "v/two"
    assert g["summarizer_model"] == "s/one" and g["verify_model"] == "v/two"
