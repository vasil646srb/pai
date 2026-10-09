import json
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from pai.app import create_app
from pai.llm import MockLLM
from pai.services import ServiceRunner, ServiceStore


def _store(tmp_path=None):
    d = tmp_path or tempfile.mkdtemp()
    return ServiceStore(f"{d}/services.json")


def _fields(**kw):
    base = {"name": "Сводка", "persona_id": "brain", "prompt": "Сделай сводку", "interval_minutes": 30}
    base.update(kw)
    return base


def test_store_create_update_delete_and_persist(tmp_path):
    st = _store(tmp_path)
    svc = st.create(_fields(), now=1000.0)
    assert svc["next_run_at"] == 1000.0 and svc["enabled"] is True and svc["space_id"] == "personal"
    upd = st.update(svc["id"], {"interval_minutes": 5, "enabled": False}, now=1000.0)
    assert upd["interval_minutes"] == 5 and upd["enabled"] is False
    st2 = ServiceStore(f"{tmp_path}/services.json")
    assert [s["id"] for s in st2.list()] == [svc["id"]]
    assert st2.delete(svc["id"]) is True and st2.list() == []


@pytest.mark.parametrize("bad", [
    {"name": ""}, {"prompt": ""}, {"interval_minutes": 0}, {"interval_minutes": 99999},
    {"space_id": "../etc"}, {"persona_id": ""},
])
def test_store_rejects_bad_fields(bad):
    with pytest.raises(ValueError):
        _store().create(_fields(**bad), now=0.0)


def test_corrupt_file_does_not_break_startup(tmp_path):
    (tmp_path / "services.json").write_text("{broken", encoding="utf-8")
    assert ServiceStore(f"{tmp_path}/services.json").list() == []


def test_runner_runs_due_services_and_records_results(tmp_path):
    st = _store(tmp_path)
    due = st.create(_fields(name="due"), now=0.0)
    off = st.create(_fields(name="off", enabled=False), now=0.0)
    later = st.create(_fields(name="later"), now=0.0)
    st.update(later["id"], {"enabled": True}, now=0.0)
    st.add_run(later["id"], {"ok": True, "reply": "x", "started_at": 0, "finished_at": 10_000.0,
                             "duration_seconds": 0})
    calls = []

    def execute(svc):
        calls.append(svc["name"])
        if svc["name"] == "boom":
            raise RuntimeError("упал")
        return {"reply": f"готово: {svc['name']}"}

    runner = ServiceRunner(st, execute)
    threads = runner.tick_once(now=100.0)
    for t in threads:
        t.join(5)
    assert calls == ["due"]                      # disabled and not-yet-due services are skipped
    got = st.get(due["id"])
    assert got["runs"][-1]["ok"] is True and got["runs"][-1]["reply"] == "готово: due"
    assert got["next_run_at"] == got["last_run_at"] + 30 * 60
    assert st.get(off["id"])["runs"] == []


def test_failed_run_is_recorded_not_raised(tmp_path):
    st = _store(tmp_path)
    svc = st.create(_fields(name="boom"), now=0.0)

    def execute(_):
        raise RuntimeError("упал")

    runner = ServiceRunner(st, execute)
    t = runner.start_run(svc["id"])
    t.join(5)
    run = st.get(svc["id"])["runs"][-1]
    assert run["ok"] is False and "упал" in run["error"]


def test_same_service_never_runs_twice_at_once(tmp_path):
    import threading
    st = _store(tmp_path)
    svc = st.create(_fields(), now=0.0)
    gate = threading.Event()

    def execute(_):
        gate.wait(5)
        return {"reply": "ok"}

    runner = ServiceRunner(st, execute)
    first = runner.start_run(svc["id"])
    assert runner.start_run(svc["id"]) is None
    gate.set()
    first.join(5)


def _client():
    main = MockLLM()
    app = create_app(data_dir=tempfile.mkdtemp(), llm=main)
    return TestClient(app), main, app


def test_api_crud_and_manual_run():
    c, main, app = _client()
    r = c.post("/services", json=_fields(name="Утро"))
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert c.get("/services").json()[0]["name"] == "Утро"
    assert c.put(f"/services/{sid}", json={"enabled": False}).json()["enabled"] is False
    assert c.post("/services", json=_fields(persona_id="nobody")).status_code == 400
    assert c.post("/services", json=_fields(interval_minutes=0)).status_code == 400
    assert c.put("/services/missing", json={"enabled": True}).status_code == 404

    main.queue_response(content="Сводка готова")
    assert c.post(f"/services/{sid}/run").status_code == 202
    for _ in range(100):
        runs = c.get(f"/services/{sid}").json()["runs"]
        if runs:
            break
        time.sleep(0.05)
    assert runs[-1]["ok"] is True and runs[-1]["reply"] == "Сводка готова"
    assert len(main.calls) == 1

    assert c.delete(f"/services/{sid}").status_code == 200
    assert c.get("/services").json() == []


def test_service_run_is_stored_as_separate_event_not_user_message():
    c, main, app = _client()
    sid = c.post("/services", json=_fields(name="Сводка")).json()["id"]
    main.queue_response(content="Сводка: всё в порядке")
    c.post(f"/services/{sid}/run")
    for _ in range(100):
        if c.get(f"/services/{sid}").json()["runs"]:
            break
        time.sleep(0.05)
    history = c.get("/chat/history?space_id=personal&limit=50").json()["messages"]
    events = [m for m in history if m["origin"] == "service"]
    assert len(events) == 2
    assert {m["role"] for m in events} == {"user", "assistant"}
    assert all(m["origin_name"] == "Сводка" for m in events)
    assert not [m for m in history if m["origin"] is None and m["text"] == "Сделай сводку"]


def test_user_turns_are_not_marked_as_service():
    from pai.orchestrator.dialog import event_tags
    assert event_tags(None, "user") == ["user"]
    assert event_tags({"kind": "service", "id": "abc", "name": "X"}, "assistant")[:2] == ["assistant", "service"]
