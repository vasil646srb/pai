import json, os, sys, threading, http.server
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pai.laya import JevLaya, get_laya
import pai.laya as L

SEEN = {}
class H(http.server.BaseHTTPRequestHandler):
    shape = "probs"
    def do_POST(self):
        SEEN["path"] = self.path; SEEN["auth"] = self.headers.get("Authorization")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); SEEN["body"] = body
        qn = next(iter(body["questions"]))
        if H.shape == "probs":
            a = {"type": "choice", "choice": "critical", "probabilities": {"normal": 0.02, "warning": 0.1, "critical": 0.88}}
        else:
            a = {"type": "choice", "choice": "warning"}
        out = json.dumps({"model": "typesafe/jev-1.13", "answers": {qn: a}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}/api"

def _ask(j):
    return j.choose("device is 95C", "Classify urgency.", ["normal", "warning", "critical"])

def test_payload_headers_and_probs():
    j = JevLaya(URL, "on", api_key="sk-test", model="jev-1.13")
    H.shape = "probs"
    v = _ask(j)
    assert v.label == "critical" and abs(v.p - 0.88) < 1e-9 and v.sure()
    assert SEEN["auth"] == "Bearer sk-test" and SEEN["body"]["model"] == "jev-1.13"
    assert SEEN["body"]["questions"]["q"]["criteria"] == {"normal": "normal", "warning": "warning", "critical": "critical"}
    assert SEEN["path"] == "/api/v1/systemone"

def test_choice_only_gets_probability_one():
    j = JevLaya(URL, "on", api_key="sk-test"); H.shape = "choice_only"
    v = _ask(j)
    assert v.label == "warning" and v.probs == {"warning": 1.0}

def test_key_from_web_settings(tmp_path):
    import json
    (tmp_path / "llm_config.json").write_text(json.dumps({"api_key": "sk-from-ui", "base_url": "https://openrouter.ai/api/v1"}))
    j = JevLaya(URL, "on", data_dir=str(tmp_path)); H.shape = "probs"
    assert _ask(j).label == "critical" and SEEN["auth"] == "Bearer sk-from-ui"
    (tmp_path / "llm_config.json").write_text(json.dumps({"api_key": "sk-changed"}))
    _ask(j); assert SEEN["auth"] == "Bearer sk-changed"   # смена ключа в UI действует сразу

def test_no_key_is_skipped_with_reason(tmp_path):
    j = JevLaya(URL, "on", data_dir=str(tmp_path))
    assert _ask(j) is None and j.stats["skipped_not_ready"] == 1 and "нет API-ключа" in j.stats["last_error"]
    assert j.remote is True

def test_jev_is_default_provider(monkeypatch):
    monkeypatch.delenv("PAI_DECISION_PROVIDER", raising=False); L._instance = None
    assert isinstance(get_laya(), JevLaya); L._instance = None

def test_provider_switch(monkeypatch):
    monkeypatch.setenv("PAI_DECISION_PROVIDER", "jev")
    L._instance = None
    assert isinstance(get_laya(), JevLaya)
    monkeypatch.setenv("PAI_DECISION_PROVIDER", "laya"); L._instance = None
    assert not isinstance(get_laya(), JevLaya)
    L._instance = None
