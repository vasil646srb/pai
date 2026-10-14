import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_telegram import API, CALLS, UPDATES, srv, _msg  # noqa: F401  (starts the fake Bot API)

def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("PAI_DATA_DIR", str(tmp_path)); monkeypatch.setenv("PAI_AUTH_DISABLED", "1")
    monkeypatch.setenv("PAI_DECISION_PROVIDER", "laya"); monkeypatch.setenv("PAI_LAYA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("PAI_LAYA_AUTOSTART", "0"); monkeypatch.setenv("PAI_TELEGRAM_API", API)
    monkeypatch.setenv("PAI_TELEGRAM_POLL_SEC", "0")
    from pai.app import create_app
    return create_app(data_dir=str(tmp_path))

def test_group_settings_inbox_and_poll(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app = _app(tmp_path, monkeypatch); c = TestClient(app)
    UPDATES[:] = [_msg(50, -1003, 60, "вопрос из группы")]
    r = c.put("/telegram/settings", json={"token": "T123456789", "channels": [
        {"key": "public", "chat_id": "@public_ch", "mode": "approve"},
        {"key": "private", "chat_id": "-1002", "mode": "auto"},
        {"key": "group", "chat_id": "-1003", "mode": "approve"}]})
    assert r.status_code == 200, r.text
    g = {x["key"]: x for x in c.get("/telegram/settings").json()["channels"]}
    assert g["group"]["chat_id"] == "-1003" and g["group"]["read"] is True
    assert c.post("/telegram/poll").json()["new"] == 1
    inbox = c.get("/telegram/inbox").json()
    assert inbox["unread"] == 1 and inbox["recent"][0]["text"] == "вопрос из группы"
