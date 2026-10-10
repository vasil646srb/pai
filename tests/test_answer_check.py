"""Answer check: a small model verifies the final answer; a failed check sends the report back."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from pai.app import create_app
from pai.llm import MockLLM


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
    assert done["verification"][0]["ok"] is True and done["verification"][0]["attempt"] == 1
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
    # no endless loop; a failed final answer is not shown in chat, only a notice is
    assert done["reply"] == "⚠ Ответ не прошёл проверку. Текст ответа — в разделе размышлений."
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
    assert any("answer revision 1/1" in m for m in msgs), msgs
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


def test_checker_and_vision_prompts_are_english_and_judge_usefulness():
    from pai.orchestrator import dialog
    assert "useful to the user" in dialog._VERIFY_SYSTEM
    assert "usefulness" in dialog._VERIFY_SYSTEM and "user_language" in dialog._VERIFY_SYSTEM
    for prompt in (dialog._VERIFY_SYSTEM, dialog._VISION_SYSTEM):
        assert not any("\u0400" <= ch <= "\u04FF" for ch in prompt)


def test_usefulness_score_is_parsed_and_clamped():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Ответ")
    ver.queue_response(content='{"ok": true, "usefulness": 14, "user_language": "Russian"}')
    c = TestClient(_app(main, ver))
    v = _sse(c, text="вопрос", persona_id="brain")[-1]["verification"][0]
    assert v["usefulness"] == 10


def test_checker_returns_confidence_missing_and_two_replies():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Ответ")
    ver.queue_response(content=(
        '{"ok": true, "report": "", "confidence": 0.8, "missing": "нет цены", '
        '"suggested_replies": ["Да, давай", "Нет, покажи другой вариант", "Третий"]}'))
    c = TestClient(_app(main, ver))
    v = _sse(c, text="сколько стоит?", persona_id="brain")[-1]["verification"][0]
    assert v["confidence"] == 0.8 and v["missing"] == "нет цены"
    assert v["suggested_replies"] == ["Да, давай", "Нет, покажи другой вариант"]


def test_checker_without_new_fields_still_works():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Ответ")
    ver.queue_response(content='{"ok": true}')
    c = TestClient(_app(main, ver))
    v = _sse(c, text="вопрос", persona_id="brain")[-1]["verification"][0]
    assert v["suggested_replies"] == [] and v["confidence"] is None


def test_checker_gets_user_language_and_answers_in_it():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Ответ")
    ver.queue_response(content=(
        '{"ok": true, "user_language": "Russian", "report": "", "confidence": 0.7, "missing": "", '
        '"suggested_replies": ["Да", "Нет"]}'))
    c = TestClient(_app(main, ver))
    done = _sse(c, text="сколько стоит подписка?", persona_id="brain")[-1]
    checks = [x for x in ver.calls if "You check an assistant's reply" in x["messages"][0]["content"]]
    prompt = checks[0]["messages"][1]["content"]
    assert "User's language: Russian" in prompt
    assert done["verification"][-1]["user_language"] == "Russian"


def test_language_hint_detects_cyrillic_and_defers_otherwise():
    from pai.orchestrator import dialog
    assert dialog._user_language_hint("Сколько стоит подписка?") == "Russian"
    assert "detect it" in dialog._user_language_hint("How much is it?")
    assert "detect it" in dialog._user_language_hint("")


def test_failed_answer_is_sent_only_as_check_failed_event():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Привет!")
    ver.queue_response(content='{"ok": false, "report": "не на вопрос"}')
    main.queue_response(content="Ответ по делу.")
    ver.queue_response(content='{"ok": true}')
    c = TestClient(_app(main, ver))
    events = _sse(c, text="сколько дней?", persona_id="brain")
    failed = [e for e in events if e["type"] == "check_failed"]
    assert len(failed) == 1
    assert failed[0]["text"] == "Привет!" and failed[0]["report"] == "не на вопрос" and failed[0]["attempt"] == 1
    assert events[-1]["reply"] == "Ответ по делу."


def test_exhausted_checks_show_notice_not_the_failed_text(monkeypatch):
    monkeypatch.setenv("PAI_VERIFY_REVISIONS", "0")
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Плохой ответ")
    ver.queue_response(content='{"ok": false, "report": "мимо"}')
    c = TestClient(_app(main, ver))
    events = _sse(c, text="вопрос", persona_id="brain")
    assert any(e["type"] == "check_failed" and e["text"] == "Плохой ответ" for e in events)
    assert events[-1]["reply"] == "⚠ Ответ не прошёл проверку. Текст ответа — в разделе размышлений."


def test_strictness_raises_the_bar_for_passing():
    def run(level, useful):
        main, ver = MockLLM(), MockLLM()
        main.queue_response(content="Ответ")
        ver.queue_response(content=f'{{"ok": true, "usefulness": {useful}, "user_language": "Russian"}}')
        app = _app(main, ver)
        app.state.orchestrator.verify_strictness = level
        c = TestClient(app)
        return _sse(c, text="вопрос", persona_id="brain")[-1]["verification"][0]
    assert run(1, 3)["ok"] is True          # lenient: 3/10 passes
    assert run(5, 6)["ok"] is False         # very strict: 6/10 fails
    v = run(5, 6)
    assert v["strictness"] == 5 and v["min_usefulness"] == 7
    assert "below" in v["report"] or v["report"]


def test_strictness_setting_persists_and_clamps(tmp_path):
    from pai.llm_config import LLMConfig, LLMConfigStore
    store = LLMConfigStore(str(tmp_path))
    store.save(LLMConfig(model="m", verify_strictness=4))
    assert store.load().verify_strictness == 4
    (tmp_path / "llm_config.json").write_text('{"model": "m", "verify_strictness": 99}', encoding="utf-8")
    assert store.load().verify_strictness == 5


def test_answer_check_runs_at_most_twice_per_turn(monkeypatch):
    monkeypatch.setenv("PAI_VERIFY_REVISIONS", "5")
    main, ver = MockLLM(), MockLLM()
    for _ in range(6):
        main.queue_response(content="не то")
        ver.queue_response(content='{"ok": false, "report": "плохо"}')
    c = TestClient(_app(main, ver))
    _sse(c, text="вопрос", persona_id="brain")
    checks = [x for x in ver.calls if "You check an assistant's reply" in x["messages"][0]["content"]]
    assert len(checks) == 2
    assert len(main.calls) == 2


def test_english_report_for_russian_user_is_replaced_with_russian():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Привет!")
    ver.queue_response(content='{"ok": false, "report": "Specify the exact action to save"}')
    main.queue_response(content="Сохранено.")
    ver.queue_response(content='{"ok": true}')
    c = TestClient(_app(main, ver))
    done = _sse(c, text="Сохрани", persona_id="brain")[-1]
    report = done["verification"][0]["report"]
    assert report and any("Ѐ" <= ch <= "ӿ" for ch in report)


def test_revision_feedback_forbids_planning_prose():
    main, ver = MockLLM(), MockLLM()
    main.queue_response(content="Привет!")
    ver.queue_response(content='{"ok": false, "report": "мимо вопроса"}')
    main.queue_response(content="Ответ.")
    ver.queue_response(content='{"ok": true}')
    c = TestClient(_app(main, ver))
    _sse(c, text="сколько дней между 1 и 4 января?", persona_id="brain")
    feedback = main.calls[1]["messages"][-1]["content"]
    assert "no plans, notes or commentary" in feedback
