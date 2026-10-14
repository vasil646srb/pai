import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_runtime import FAKE_CLI, free_port

def test_lifespan_starts_and_stops_laya(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"; cli.write_text(FAKE_CLI)
    port = free_port()
    monkeypatch.setenv("PAI_DATA_DIR", str(tmp_path / "d")); monkeypatch.setenv("PAI_AUTH_DISABLED", "1")
    monkeypatch.setenv("PAI_DECISION_PROVIDER", "laya"); monkeypatch.setenv("PAI_LAYA_URL", f"http://127.0.0.1:{port}"); monkeypatch.setenv("PAI_LAYA_CLI", str(cli))
    import pai.laya as L; L._instance = None
    from fastapi.testclient import TestClient
    from pai.app import create_app
    app = create_app(data_dir=str(tmp_path / "d"))
    c = TestClient(app)                                   # без `with`: стартап-хуки не бегут, Laya не трогаем
    assert app.state.laya_runtime.status()["state"] == "idle"
    with TestClient(app) as c:
        end = time.time() + 20
        while time.time() < end and c.get("/laya/status").json()["state"] != "ready": time.sleep(0.2)
        assert c.get("/laya/status").json()["ready"] is True
        pid = app.state.laya_runtime._proc.pid
    time.sleep(0.5)
    assert not app.state.laya_runtime._tcp_up()           # shutdown убил то, что сам запустил
