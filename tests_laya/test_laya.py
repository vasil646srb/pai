import json, threading, http.server, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pai.laya import Laya

REPLY = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ans = {}
        for n, q in body["questions"].items():
            lab = REPLY.get(n, q["criteria"][0]); rest = [c for c in q["criteria"] if c != lab]
            probs = {lab: REPLY.get(n + "_p", 0.95), **{c: (1 - REPLY.get(n + "_p", 0.95)) / len(rest) for c in rest}}
            ans[n] = {"type": "choice", "choice": lab, "probabilities": probs, "confidence": 0.2}
        out = json.dumps({"model": "m", "answers": ans}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}"

def test_verdict_margin_and_sure():
    l = Laya(URL, "on")
    REPLY.clear(); REPLY.update(q="b", q_p=0.55)
    v = l.choose("x", "i", ["a", "b", "c"])
    assert v.label == "b" and not v.sure() and abs(v.margin - 0.325) < 1e-6   # их пример: уверенности нет -> fallback
    REPLY.update(q_p=0.97); assert l.choose("x", "i", ["a", "b", "c"]).sure()

def test_off_and_shadow_modes():
    assert not Laya("", "on").enabled
    l = Laya(URL, ""); assert l.mode == "shadow" and l.enabled and not l.acts

def test_breaker_and_never_raises():
    l = Laya("http://127.0.0.1:1", "on", timeout=0.2, fail_threshold=2, cooldown=60)
    assert l.choose("x", "i", ["a", "b"]) is None and l.choose("x", "i", ["a", "b"]) is None
    assert l._open_until > 0   # дальше не стучится 60с

def test_bad_choice_ignored():
    l = Laya(URL, "on"); REPLY.clear(); REPLY.update(q="zzz")
    assert l.choose("x", "i", ["a", "b"]) is None   # метка вне criteria -> None

def test_verify_gate_and_announce(monkeypatch=None):
    import pai.laya as L
    from pai.orchestrator import dialog as D
    L._instance = Laya(URL, "on")
    logs = []
    st = types.SimpleNamespace(log_action=lambda *a, **k: logs.append((a, k)))
    obj = types.SimpleNamespace(storage=st, _LAYA_VERIFY_Q=D.DialogLoop._LAYA_VERIFY_Q)
    REPLY.clear(); REPLY.update(answers="answers", answers_p=0.97, language="same_language", language_p=0.97)
    g = D.DialogLoop._laya_verify_gate(obj, "s", "привет", "ответ", 3)
    assert g and g["status"] == "pass" and g["skipped_by"] == "laya"
    assert D.DialogLoop._laya_verify_gate(obj, "s", "u", "a", 4) is None          # strict>3 -> всегда LLM
    REPLY.update(answers="only_promises_action"); assert D.DialogLoop._laya_verify_gate(obj, "s", "u", "a", 3) is None
    # announce: Sербский — regex не ловит, Laya ловит
    REPLY.clear(); REPLY.update(q="announces_action", q_p=0.97)
    assert D._announces_unfinished_action("Sada ću proveriti fajl.", st, "s") is True
    L._instance = Laya("", "on"); assert D._announces_unfinished_action("Sada ću proveriti fajl.") is False
    assert logs


def test_stats_count_ok_fail_skipped():
    l = Laya(URL, "on"); REPLY.clear(); REPLY.update(q="b", q_p=0.9)
    l.ready = False
    assert l.choose("x", "i", ["a", "b"]) is None and l.stats["skipped_not_ready"] == 1 and l.stats["calls"] == 0
    l.ready = True
    assert l.choose("x", "i", ["a", "b"]) is not None and l.stats["ok"] == 1 and l.stats["calls"] == 1
    bad = Laya("http://127.0.0.1:1", "on", timeout=0.2)
    assert bad.choose("x", "i", ["a", "b"]) is None and bad.stats["fail"] == 1
    assert "ConnectionRefusedError" in bad.stats["last_error"] or "refused" in bad.stats["last_error"].lower()
