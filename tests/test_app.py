import pytest
from fastapi.testclient import TestClient

from pai.app import create_app
from pai.llm import MockLLM
from pai.storage import Storage


@pytest.fixture
def client(tmp_path):
    storage = Storage(tmp_path / "data")
    llm = MockLLM()
    app = create_app(llm=llm, storage=storage)
    with TestClient(app) as c:
        c._llm = llm  # stash for tests that want to queue responses
        c._storage = storage
        yield c
    storage.close()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_list_personas_returns_defaults(client):
    r = client.get("/personas")
    assert r.status_code == 200
    ids = {p["persona_id"] for p in r.json()}
    assert {"brain", "work", "pm", "friend", "tutor"} <= ids


def test_create_persona(client):
    payload = {
        "persona_id": "custom",
        "name": "Custom",
        "allow_spaces": ["work"],
    }
    r = client.post("/personas", json=payload)
    assert r.status_code == 201
    assert r.json()["persona_id"] == "custom"

    r2 = client.get("/personas")
    ids = {p["persona_id"] for p in r2.json()}
    assert "custom" in ids


def test_create_persona_duplicate_conflicts(client):
    payload = {"persona_id": "brain", "name": "dup"}
    r = client.post("/personas", json=payload)
    assert r.status_code == 409


def test_chat_basic_flow(client):
    client._llm.queue_response(content="Привет! Чем помочь?")
    r = client.post("/chat", json={"text": "привет", "persona_id": "brain"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "Привет! Чем помочь?"
    assert "usage" in body
    assert "live_context_preview" in body


def test_chat_unknown_persona_404(client):
    r = client.post("/chat", json={"text": "hi", "persona_id": "nope"})
    assert r.status_code == 404


def test_chat_space_denied_for_persona_403(client):
    r = client.post("/chat", json={"text": "hi", "persona_id": "work", "space_id": "personal"})
    assert r.status_code == 403


def test_chat_with_reference_now(client):
    client._llm.queue_response(content="ok")
    r = client.post("/chat", json={
        "text": "вчера мы говорили", "persona_id": "brain",
        "reference_now": "2026-08-30T12:00:00+00:00",
    })
    assert r.status_code == 200


def test_chat_bad_reference_now_400(client):
    r = client.post("/chat", json={
        "text": "hi", "persona_id": "brain", "reference_now": "not-a-date",
    })
    assert r.status_code == 400


def test_memory_stats_after_chat(client):
    client._llm.queue_response(content="ok")
    client.post("/chat", json={"text": "test message", "persona_id": "brain"})
    r = client.get("/memory/stats")
    assert r.status_code == 200
    assert r.json()["total_nodes"] >= 2


def test_memory_node_inspector(client):
    client._llm.queue_response(content="reply text")
    client.post("/chat", json={"text": "hello there", "persona_id": "brain"})

    roots = client.get("/memory/tree/roots").json()
    assert len(roots) >= 1
    node_id = roots[0]["id"]

    r = client.get(f"/memory/node/{node_id}")
    assert r.status_code == 200
    assert r.json()["id"] == node_id


def test_memory_node_not_found_404(client):
    r = client.get("/memory/node/doesnotexist")
    assert r.status_code == 404


def test_memory_node_status_update(client):
    client._llm.queue_response(content="reply")
    client.post("/chat", json={"text": "pin me", "persona_id": "brain"})
    roots = client.get("/memory/tree/roots").json()
    node_id = roots[0]["id"]

    r = client.patch(f"/memory/node/{node_id}/status", json={"status": "pinned"})
    assert r.status_code == 200
    assert r.json()["status"] == "pinned"


def test_memory_node_status_invalid_value_400(client):
    client._llm.queue_response(content="reply")
    client.post("/chat", json={"text": "x", "persona_id": "brain"})
    roots = client.get("/memory/tree/roots").json()
    node_id = roots[0]["id"]
    r = client.patch(f"/memory/node/{node_id}/status", json={"status": "not-a-status"})
    assert r.status_code == 400


def test_memory_search(client):
    client._llm.queue_response(content="ok")
    client.post("/chat", json={"text": "обсуждение архитектуры хранилища", "persona_id": "brain"})

    r = client.post("/memory/search", json={"query": "архитектуры"})
    assert r.status_code == 200
    assert isinstance(r.json()["results"], list)


def test_memory_compress_endpoint(client):
    for i in range(6):
        client._llm.queue_response(content=f"reply {i}")
        client.post("/chat", json={"text": f"msg {i}", "persona_id": "brain"})

    r = client.post("/memory/compress", json={"space_id": "personal"})
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_memory_retrieval_log(client):
    node_resp = client.post("/chat", json={"text": "x", "persona_id": "brain"})
    r = client.get("/memory/retrieval-log")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_tool_calculate_time_delta(client):
    r = client.post("/tools/calculate_time_delta", json={"date1": "2026-07-15", "date2": "2026-08-30"})
    assert r.status_code == 200
    assert r.json()["days"] == 46


def test_tool_calculate_time_delta_bad_date_400(client):
    r = client.post("/tools/calculate_time_delta", json={"date1": "garbage", "date2": "now"})
    assert r.status_code == 400


def test_rules_create_and_lifecycle(client):
    r = client.post("/rules", json={"rule_text": "always check deadlines", "scope": "global"})
    assert r.status_code == 201
    rule_id = r.json()["id"]
    assert r.json()["status"] == "draft"

    r2 = client.post(f"/rules/{rule_id}/validate")
    assert r2.status_code == 200
    assert r2.json()["status"] == "validated"

    r3 = client.post(f"/rules/{rule_id}/activate")
    assert r3.status_code == 200
    assert r3.json()["status"] == "active"


def test_rules_cannot_skip_stage_400(client):
    r = client.post("/rules", json={"rule_text": "x", "scope": "global"})
    rule_id = r.json()["id"]
    r2 = client.post(f"/rules/{rule_id}/activate")  # skip validate
    assert r2.status_code == 400


def test_rules_activate_missing_rule_404(client):
    r = client.post("/rules/doesnotexist/validate")
    assert r.status_code == 404


def test_rules_reject(client):
    r = client.post("/rules", json={"rule_text": "bad rule", "scope": "global"})
    rule_id = r.json()["id"]
    r2 = client.post(f"/rules/{rule_id}/reject")
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"


def test_rules_invalid_scope_400(client):
    r = client.post("/rules", json={"rule_text": "x", "scope": "not-a-scope"})
    assert r.status_code == 400


def test_usage_endpoint(client):
    client._llm.queue_response(content="ok")
    client.post("/chat", json={"text": "hi", "persona_id": "brain"})
    r = client.get("/usage")
    assert r.status_code == 200
    assert r.json()["total_calls"] >= 1


def test_spaces_endpoint(client):
    client._llm.queue_response(content="ok")
    client.post("/chat", json={"text": "hi", "persona_id": "brain", "space_id": "work"})
    r = client.get("/spaces")
    assert r.status_code == 200
    assert "work" in r.json()


def test_tree_children_endpoint(client):
    for i in range(3):
        client._llm.queue_response(content=f"reply {i}")
        client.post("/chat", json={"text": f"m{i}", "persona_id": "brain"})
    client.post("/memory/compress", json={"space_id": "personal", "force_top": False})

    roots = client.get("/memory/tree/roots").json()
    assert len(roots) >= 1
    node_id = roots[0]["id"]
    r = client.get(f"/memory/tree/{node_id}/children")
    assert r.status_code == 200


def test_full_tool_call_chat_flow(client):
    client._llm.queue_response(content="фиксирую факт")
    r0 = client.post("/chat", json={"text": "важный факт про проект X", "persona_id": "brain"})
    node_id = None
    roots = client.get("/memory/tree/roots").json()
    for n in roots:
        if "важный факт" in n["raw_content"]:
            node_id = n["id"]
    assert node_id is not None

    client._llm.queue_response(tool_calls=[{"id": "plan", "name": "plan_tool_pool", "arguments": {"calls": [{"name": "get_fragment", "arguments": {"id": node_id}}]}}])
    client._llm.queue_response(content="Нашёл факт про проект X")
    r = client.post("/chat", json={"text": "что там про проект X?", "persona_id": "brain"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "Нашёл факт про проект X"
    assert body["tool_trace"][0]["name"] == "get_fragment"


# ---------- memory export / import (backup & restore) ----------

def test_export_memory_returns_downloadable_wal(client):
    client._llm.queue_response(content="ok")
    client.post("/chat", json={"text": "запомни: дедлайн в пятницу", "persona_id": "brain"})

    r = client.get("/memory/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in r.headers["content-disposition"]
    assert len(r.content) > 0


def test_export_import_roundtrip_into_fresh_store(client, tmp_path):
    client._llm.queue_response(content="ok")
    client.post("/chat", json={"text": "важная заметка", "persona_id": "brain"})
    backup = client.get("/memory/export").content

    fresh_storage = Storage(tmp_path / "fresh")
    fresh_app = create_app(llm=MockLLM(), storage=fresh_storage)
    with TestClient(fresh_app) as fresh_client:
        r = fresh_client.post(
            "/memory/import",
            files={"file": ("backup.jsonl", backup, "application/x-ndjson")},
            params={"mode": "replace"},
        )
        assert r.status_code == 200
        assert r.json()["nodes_in_index"] > 0
        stats = fresh_client.get("/memory/stats").json()
        assert stats["total_nodes"] == r.json()["nodes_in_index"]
    fresh_storage.close()


def test_import_merge_adds_to_existing_store(client):
    client._llm.queue_response(content="ok")
    client.post("/chat", json={"text": "заметка раз", "persona_id": "brain"})
    before = client.get("/memory/stats").json()["total_nodes"]
    backup = client.get("/memory/export").content

    r = client.post(
        "/memory/import",
        files={"file": ("backup.jsonl", backup, "application/x-ndjson")},
        params={"mode": "merge"},
    )
    assert r.status_code == 200
    after = client.get("/memory/stats").json()["total_nodes"]
    assert after == before  # same records replayed — merge is idempotent by id


def test_import_rejects_garbage_file(client):
    r = client.post(
        "/memory/import",
        files={"file": ("backup.jsonl", b"this is not a valid backup", "text/plain")},
    )
    assert r.status_code == 400


def test_import_rejects_bad_mode_query_param(client):
    r = client.post(
        "/memory/import",
        files={"file": ("backup.jsonl", b"", "application/x-ndjson")},
        params={"mode": "wipe-everything"},
    )
    assert r.status_code == 422  # fails FastAPI's pattern validation before reaching the handler


def test_custom_function_requires_validation_and_explicit_activation(client):
    payload = {
        "name": "echo_text",
        "description": "Echo via LLM",
        "kind": "llm_prompt",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        "implementation": {"prompt": "Return {{args}}"},
    }
    r = client.post("/functions", json=payload)
    assert r.status_code == 201
    fid = r.json()["id"]
    assert r.json()["status"] == "draft"
    assert client.post(f"/functions/{fid}/activate").status_code == 400
    assert client.post(f"/functions/{fid}/validate").json()["status"] == "validated"
    assert client.post(f"/functions/{fid}/activate").json()["status"] == "active"
    assert any(f["name"] == "echo_text" for f in client.get("/functions").json())
    assert client.post(f"/functions/{fid}/archive").json()["status"] == "archived"


def test_python_function_with_uppercase_json_schema_is_normalized(client):
    payload = {
        "name": "subtract_numbers",
        "description": "Subtract two numbers",
        "kind": "python",
        "parameters": {
            "type": "OBJECT",
            "required": ["num1", "num2"],
            "properties": {
                "num1": {"type": "NUMBER"},
                "num2": {"type": "NUMBER"},
            },
        },
        "implementation": {
            "inline_code": "def subtract_numbers(num1, num2):\n    return num1 - num2"
        },
    }
    r = client.post("/functions", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["parameters"]["type"] == "object"
    assert body["parameters"]["properties"]["num1"]["type"] == "number"
    fid = body["id"]
    assert client.post(f"/functions/{fid}/validate").status_code == 200
    assert client.post(f"/functions/{fid}/activate").status_code == 200
