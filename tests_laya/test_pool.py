import json, os, sys, threading, http.server, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pai.laya as L
from pai.laya import JevLaya, laya_pool_shadow, last_user_text

YES = {"search_web"}
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ans = {n: {"type": "choice", "choice": "yes" if n in YES else "no",
                   "probabilities": {"yes": 0.9 if n in YES else 0.1, "no": 0.1 if n in YES else 0.9}}
               for n in body["questions"]}
        out = json.dumps({"model": "m", "answers": ans}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

class St:
    def __init__(self): self.rows = []
    def log_action(self, kind, name, request, **k): self.rows.append((kind, name, request, k))

def _schemas():
    return [{"function": {"name": "search_web", "description": "Search the web."}},
            {"function": {"name": "calendar_read", "description": "Read calendar."}}]

def _wait(st, t=10):
    end = time.time() + t
    while time.time() < end and not st.rows: time.sleep(0.05)

def test_pool_shadow_logs_jev_vs_llm(monkeypatch):
    L._instance = JevLaya(f"http://127.0.0.1:{srv.server_port}/api", "shadow", api_key="k")
    st = St()
    laya_pool_shadow(st, "personal", "what's the news today", _schemas(), [{"name": "calendar_read"}])
    _wait(st)
    kind, name, req, k = st.rows[0]
    assert name == "pool_plan" and req["tools"] == 2
    assert k["response"]["laya"]["search_web"]["label"] == "yes"
    assert k["response"]["laya"]["calendar_read"]["label"] == "no"
    assert k["response"]["laya"] and k["latency_seconds"] is not None
    # actual сохраняется в response["actual"]
    st2_row = st.rows[0][3]
    assert st2_row is not None
    L._instance = None

def test_pool_shadow_off_and_empty_are_noops():
    st = St()
    os.environ["PAI_LAYA_MODE"] = "off"
    L._instance = L.Laya("", "off")
    laya_pool_shadow(st, "s", "hi", _schemas(), [])
    time.sleep(0.3); assert st.rows == []
    os.environ.pop("PAI_LAYA_MODE", None); L._instance = None

def test_last_user_text_handles_multimodal():
    assert last_user_text([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]) == "a"
    assert last_user_text([{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url"}]}]) == "x"
    assert last_user_text([]) == ""
