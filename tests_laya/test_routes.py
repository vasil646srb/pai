import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("PAI_DATA_DIR", str(tmp_path)); monkeypatch.setenv("PAI_AUTH_DISABLED", "1")
    monkeypatch.setenv("PAI_DECISION_PROVIDER", "laya"); monkeypatch.setenv("PAI_LAYA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("PAI_LAYA_AUTOSTART", "0")
    import pai.laya as L; L._instance = None
    from pai.app import create_app
    return create_app(data_dir=str(tmp_path))

def test_toggle_put_get_and_restart(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import pai.laya as L
    app = _app(tmp_path, monkeypatch)
    c = TestClient(app)
    L.get_laya().ready = False  # без lifespan сервер не запущен
    d = c.get("/laya/settings").json()
    assert d["use_jev_verify"] is False and d["available"] is False and "не готова" in d["reason"]
    d = c.put("/laya/settings", json={"use_jev_verify": True}).json()
    assert d["use_jev_verify"] is True and L.get_laya().use_for_verify is True
    assert json.loads((tmp_path / "laya_settings.json").read_text()) == {"use_jev_verify": True}
    # "перезапуск": новый singleton читает значение из файла
    L._instance = None
    app2 = _app(tmp_path, monkeypatch)
    assert L.get_laya().use_for_verify is True
    assert TestClient(app2).get("/laya/settings").json()["use_jev_verify"] is True
    L._instance = None
