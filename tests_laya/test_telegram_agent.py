import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from test_telegram import svc, CALLS, UPDATES, API, srv, _msg  # noqa: F401  (fixtures + fake Bot API)

def _ingest(svc, *msgs):
    UPDATES[:] = list(msgs)
    svc.poll()

@pytest.fixture
def hooks():
    from pai import telegram as T
    calls = {"responder": 0, "classify": []}
    yield T, calls
    T.set_responder(None); T.set_classifier(None)

def _responder(calls, text="Да, отвечаю коротко."):
    def fn(item):
        calls["responder"] += 1
        return text
    return fn

def test_question_gets_brain_reply_in_queue(svc, hooks):
    T, calls = hooks
    T.set_classifier(lambda t, s: {"needs_reply": True, "kind": "question"})
    T.set_responder(_responder(calls))
    _ingest(svc, _msg(1, -1003, 10, "Кто знает про GeoJEPA?"))
    r = svc.process_group()
    assert r["processed"] == 1 and calls["responder"] == 1
    pend = svc.queue_view()["pending"]
    assert len(pend) == 1 and pend[0]["channel"] == "group" and pend[0]["text"] == "Да, отвечаю коротко."
    assert not [c for c in CALLS if c[0] == "sendMessage"]        # approve-режим: без подтверждения не уходит
    assert svc.process_group()["checked"] == 0                    # второй раз не обрабатывается

def test_no_reply_needed_skips_responder(svc, hooks):
    T, calls = hooks
    T.set_classifier(lambda t, s: {"needs_reply": False, "kind": "chat"})
    T.set_responder(_responder(calls))
    _ingest(svc, _msg(2, -1003, 11, "ну привет всем"))
    svc.process_group()
    assert calls["responder"] == 0 and svc.queue_view()["pending"] == []

def test_spam_is_ignored_even_if_marked_needs_reply(svc, hooks):
    T, calls = hooks
    T.set_classifier(lambda t, s: {"needs_reply": True, "kind": "spam"})
    T.set_responder(_responder(calls))
    _ingest(svc, _msg(3, -1003, 12, "купи казино"))
    svc.process_group()
    assert calls["responder"] == 0

def test_jev_unavailable_retries_then_gives_up(svc, hooks):
    T, calls = hooks
    T.set_classifier(lambda t, s: None)
    T.set_responder(_responder(calls))
    _ingest(svc, _msg(4, -1003, 13, "вопрос?"))
    svc.process_group(); svc.process_group()
    assert svc.inbox_view()["recent"][0].get("analyzed") is not True    # ещё попытки
    svc.process_group()
    item = svc.inbox_view()["recent"][0]
    assert item["analyzed"] is True and item["verdict"] == "unavailable" and calls["responder"] == 0

def test_bot_messages_are_not_answered(svc, hooks):
    T, calls = hooks
    T.set_classifier(lambda t, s: {"needs_reply": True, "kind": "question"})
    T.set_responder(_responder(calls))
    m = _msg(5, -1003, 14, "я бот")
    m["message"]["from"]["is_bot"] = True
    _ingest(svc, m)
    svc.process_group()
    assert calls["responder"] == 0

def test_auto_reply_off_does_nothing(svc, hooks):
    T, calls = hooks
    T.set_classifier(lambda t, s: {"needs_reply": True, "kind": "question"})
    T.set_responder(_responder(calls))
    svc.set_auto_reply(False)
    _ingest(svc, _msg(6, -1003, 15, "вопрос?"))
    assert svc.process_group() == {"processed": 0, "reason": "автоответы выключены"}
    assert calls["responder"] == 0
    svc.set_auto_reply(True)

def test_group_auto_mode_sends_at_once(svc, hooks):
    T, calls = hooks
    T.set_classifier(lambda t, s: {"needs_reply": True, "kind": "question"})
    T.set_responder(_responder(calls, "Публикую сразу"))
    svc.save_channels([{"key": "group", "chat_id": "-1003", "mode": "auto", "read": True}])
    _ingest(svc, _msg(7, -1003, 16, "вопрос?"))
    svc.process_group()
    sent = [c[1] for c in CALLS if c[0] == "sendMessage"]
    assert sent and sent[-1]["text"] == "Публикую сразу"

def test_responder_error_is_recorded_not_raised(svc, hooks):
    T, calls = hooks
    T.set_classifier(lambda t, s: {"needs_reply": True, "kind": "question"})
    def boom(item):
        raise RuntimeError("model down")
    T.set_responder(boom)
    _ingest(svc, _msg(8, -1003, 17, "вопрос?"))
    r = svc.process_group()
    item = svc.inbox_view()["recent"][0]
    assert r["processed"] == 1 and item["error"] == "RuntimeError" and item["analyzed"] is True

def test_auto_reply_flag_preserves_channels(svc):
    svc.set_auto_reply(False)
    svc.save_channels([{"key": "group", "chat_id": "-1003", "mode": "approve", "read": True}])
    assert svc.auto_reply_enabled() is False
    assert {c["key"]: c for c in svc.channels()}["group"]["chat_id"] == "-1003"
    svc.set_auto_reply(True)

def _orch(done_events, seen):
    class Orch:
        def handle_turn_stream(self, text, persona_id, space_id="personal", **kw):
            seen.update(persona=persona_id, space=space_id, text=text)
            yield {"type": "delta", "text": "черновик"}
            yield {"type": "done", **done_events}
    return Orch()

def test_responder_sends_only_when_final_check_passed():
    from pai.telegram_agent import make_responder, GROUP_SPACE
    seen = {}
    ok = _orch({"reply": "  Ответ.  ", "verification": [{"status": "fail"}, {"status": "pass"}]}, seen)
    assert make_responder(ok)({"from": "alice", "text": "Кто знает X?"}) == "Ответ."
    assert seen["persona"] == "brain" and seen["space"] == GROUP_SPACE and "Кто знает X?" in seen["text"]

def test_responder_holds_reply_that_failed_last_check():
    from pai.telegram_agent import make_responder
    from pai.telegram import HeldReply
    o = _orch({"reply": "плохой", "verification": [{"status": "fail", "report": "не по теме"}]}, {})
    with pytest.raises(HeldReply) as e:
        make_responder(o)({"text": "вопрос"})
    assert "не по теме" in str(e.value) and e.value.draft == "плохой"

def test_responder_holds_when_check_did_not_run():
    from pai.telegram_agent import make_responder
    from pai.telegram import HeldReply
    with pytest.raises(HeldReply):
        make_responder(_orch({"reply": "x", "verification": []}, {}))({"text": "q"})
    with pytest.raises(HeldReply):
        make_responder(_orch({"reply": "x", "verification": [{"status": "error"}]}, {}))({"text": "q"})

def test_held_draft_is_kept_not_sent(svc, hooks):
    from pai.telegram import HeldReply
    T, calls = hooks
    T.set_classifier(lambda t, s: {"needs_reply": True, "kind": "question"})
    def held(item):
        raise HeldReply("ответ не прошёл проверку", draft="черновик")
    T.set_responder(held)
    _ingest(svc, _msg(9, -1003, 18, "вопрос?"))
    svc.process_group()
    item = svc.inbox_view()["recent"][0]
    assert item["held"] == "ответ не прошёл проверку" and item["draft"] == "черновик"
    assert svc.queue_view()["pending"] == [] and not [c for c in CALLS if c[0] == "sendMessage"]

def test_tool_post_to_group_never_auto_sends(svc):
    from pai import telegram as T
    svc.save_channels([{"key": "group", "chat_id": "-1003", "mode": "auto", "read": True},
                       {"key": "public", "chat_id": "@public_ch", "mode": "auto"}])
    r = T.post_from_tool("group", "непроверенный текст")
    assert r["status"] == "pending"
    r2 = T.post_from_tool("public", "непроверенный текст")
    assert r2["status"] == "pending"
    assert not [c for c in CALLS if c[0] == "sendMessage"]

def test_classify_none_when_jev_off_or_missing(monkeypatch):
    import pai.telegram_agent as A
    monkeypatch.setattr(A, "get_laya", lambda: None)            # интеграция Laya/Jev не подключена
    assert A.classify("вопрос?", "bob") is None
    laya = pytest.importorskip("pai.laya")  # часть про выключенную Laya нужна только при установленных сборках Laya
    monkeypatch.setattr(A, "get_laya", lambda: laya.Laya("", "off"))  # подключена, но выключена
    assert A.classify("вопрос?", "bob") is None
