"""Answer check: a small model verifies the final answer; a failed check sends the report back."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from pai.app import create_app
from pai.llm import MockLLM


import pytest


@pytest.fixture(autouse=True)
def _verify_on(monkeypatch):
    monkeypatch.setenv("PAI_VERIFY", "1")          # conftest turns the check off by default
    monkeypatch.delenv("PAI_VERIFY_REVISIONS", raising=False)


def _app(main, verifier):
    app = create_app(data_dir=tempfile.mkdtemp(), llm=main)
    app.state.orchestrator.verifier_llm = verifier        # small model used only for checks
    return app


def _sse(client, **body):
    with client.stream("POST", "/chat/stream", json=body) as r:
        return [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]


def test_passing_answer_is_returned_once():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="3 дня")
    ver.queue_response(content='{"ok": true}')
    c = TestClient(_app(main, ver))
    done = _sse(c, text="сколько дней между 1 и 4 января?", persona_id="brain")[-1]
    assert done["reply"] == "3 дня"
    assert done["verification"] == [{"ok": True, "report": "", "attempt": 1}]
    assert len(main.calls) == 1


def test_failed_check_sends_report_back_and_uses_revision():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Привет!")                                   # off-topic answer
    ver.queue_response(content='{"ok": false, "report": "ответ не на вопрос про даты"}')
    main.queue_response(content="Между 1 и 4 января 3 дня.")                  # revision
    ver.queue_response(content='{"ok": true}')
    c = TestClient(_app(main, ver))
    done = _sse(c, text="сколько дней между 1 и 4 января?", persona_id="brain")[-1]
    assert done["reply"] == "Между 1 и 4 января 3 дня."
    assert [v["ok"] for v in done["verification"]] == [False, True]
    # the main model got the report as feedback
    feedback = main.calls[1]["messages"][-1]["content"]
    assert "ответ не на вопрос про даты" in feedback


def test_revisions_are_capped():
    main, ver = MockLLM(), MockLLM()
    os.environ["PAI_VERIFY_REVISIONS"] = "1"
    try:
        for _ in range(2):
            main.queue_response(content="не то")
            ver.queue_response(content='{"ok": false, "report": "плохо"}')
        c = TestClient(_app(main, ver))
        done = _sse(c, text="вопрос", persona_id="brain")[-1]
    finally:
        del os.environ["PAI_VERIFY_REVISIONS"]
    assert done["reply"] == "не то"                  # last attempt is returned, no endless loop
    assert len(done["verification"]) == 2
    assert len(main.calls) == 2


def test_broken_checker_does_not_block_the_answer():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="ответ")
    ver.queue_response(content="это не json вообще")
    c = TestClient(_app(main, ver))
    done = _sse(c, text="вопрос", persona_id="brain")[-1]
    assert done["reply"] == "ответ" and done["verification"] == []


def test_can_be_disabled():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="ответ")
    os.environ["PAI_VERIFY"] = "0"
    try:
        c = TestClient(_app(main, ver))
        done = _sse(c, text="вопрос", persona_id="brain")[-1]
    finally:
        os.environ["PAI_VERIFY"] = "1"
    assert done["reply"] == "ответ" and done["verification"] == [] and ver.calls == []


def test_check_is_logged_on_success_and_revision(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="pai")
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="не то")
    ver.queue_response(content='{"ok": false, "report": "мимо вопроса"}')
    main.queue_response(content="то")
    ver.queue_response(content='{"ok": true}')
    c = TestClient(_app(main, ver))
    _sse(c, text="вопрос", persona_id="brain")
    msgs = [r.getMessage() for r in caplog.records if r.name.startswith("pai")]
    assert any("answer check" in m and "ok=False" in m and "мимо вопроса" in m for m in msgs), msgs
    assert any("answer revision 1/2" in m for m in msgs), msgs
    assert any("answer check" in m and "ok=True" in m for m in msgs), msgs


def test_verify_model_setting_falls_back_and_persists(tmp_path):
    from pai.llm_config import LLMConfig, LLMConfigStore
    cfg = LLMConfig(model="big", summarizer_model="")
    assert cfg.effective_verify_model() == "big"
    cfg = LLMConfig(model="big", summarizer_model="mid", verify_model="")
    assert cfg.effective_verify_model() == "mid"
    cfg = LLMConfig(model="big", summarizer_model="mid", verify_model="tiny")
    store = LLMConfigStore(str(tmp_path))
    store.save(cfg)
    assert store.load().effective_verify_model() == "tiny"


def test_checker_calls_are_logged_and_use_checker_model():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="3 дня")
    ver.queue_response(content='{"ok": true}')
    app = _app(main, ver)
    c = TestClient(app)
    _sse(c, text="сколько дней?", persona_id="brain")
    assert len(ver.calls) == 1 and len(main.calls) == 1
    rows = app.state.storage.get_action_log(kind="llm_request", limit=50)
    checks = [r for r in rows if "answer_check" in json.dumps(r, ensure_ascii=False)]
    assert checks, "answer check call must appear in the request log"
