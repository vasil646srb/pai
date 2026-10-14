import json, os, sys, threading, http.server
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

CALLS = []
UPDATES = []  # what getUpdates returns (filtered by offset)
class FakeApi(http.server.BaseHTTPRequestHandler):
    def _send(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        parts = self.path.strip("/").split("/")
        if len(parts) != 2 or not parts[0].startswith("bot"):
            return self._send(404, {"ok": False, "description": "Not Found"})
        tok, method = parts[0][3:], parts[1]
        if tok != "T123456789":
            return self._send(401, {"ok": False, "description": "Unauthorized"})
        CALLS.append((method, body))
        if method == "getMe":
            return self._send(200, {"ok": True, "result": {"id": 42, "username": "pai_bot"}})
        if method == "getChatMember":
            if str(body.get("chat_id")) in ("@public_ch", "-1002", "-1003"):
                return self._send(200, {"ok": True, "result": {"status": "administrator", "can_post_messages": True}})
            return self._send(400, {"ok": False, "description": "Bad Request: chat not found"})
        if method == "getUpdates":
            off = body.get("offset", 0)
            res = [u for u in UPDATES if u["update_id"] >= off]
            return self._send(200, {"ok": True, "result": res})
        if method == "sendMessage":
            if body.get("chat_id") == "@no_rights":
                return self._send(403, {"ok": False, "description": "Forbidden: bot is not a member of the channel chat"})
            mid = 100 + len([c for c in CALLS if c[0] == "sendMessage"])
            return self._send(200, {"ok": True, "result": {"message_id": mid}})
        return self._send(404, {"ok": False, "description": "unknown"})
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeApi); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
API = f"http://127.0.0.1:{srv.server_port}"

class Secrets:
    def __init__(self): self.d = {}
    def set(self, n, v): self.d[n] = v
    def values(self): return dict(self.d)

def _msg(uid, chat_id, mid, text, username="alice", title="Группа AI", chat_type="supergroup"):
    return {"update_id": uid, "message": {"message_id": mid, "date": 1700000000 + uid,
            "chat": {"id": chat_id, "title": title, "type": chat_type},
            "from": {"id": 7, "username": username, "first_name": username}, "text": text}}

@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("PAI_TELEGRAM_API", API); monkeypatch.setenv("PAI_TELEGRAM_POLL_SEC", "0")
    from pai import telegram as T
    CALLS.clear(); UPDATES.clear()
    s = T.init_services(Secrets(), tmp_path)
    s.set_token("T123456789")
    s.save_channels([{"key": "public", "chat_id": "@public_ch", "mode": "approve"},
                     {"key": "private", "chat_id": "-1002", "mode": "auto"},
                     {"key": "group", "chat_id": "-1003", "mode": "approve", "read": True}])
    return s

def test_public_waits_for_approval(svc):
    from pai import telegram as T
    r = T.post_from_tool("public", "Привет, канал")
    assert r["status"] == "pending" and not [c for c in CALLS if c[0] == "sendMessage"]
    done = svc._act(r["id"], approve=True)
    assert done["status"] == "sent" and done["message_id"] >= 100
    assert [c[1]["chat_id"] for c in CALLS if c[0] == "sendMessage"] == ["@public_ch"]

def test_private_is_sent_at_once(svc):
    from pai import telegram as T
    r = T.post_from_tool("private", "Заметка себе")
    assert r["status"] == "sent"
    assert [str(c[1]["chat_id"]) for c in CALLS if c[0] == "sendMessage"] == ["-1002"]

def test_group_post_goes_through_queue(svc):
    from pai import telegram as T
    r = T.post_from_tool("group", "Ответ в группу")
    assert r["status"] == "pending" and not [c for c in CALLS if c[0] == "sendMessage"]

def test_reject_never_sends(svc):
    from pai import telegram as T
    r = T.post_from_tool("public", "не публиковать")
    assert svc._act(r["id"], approve=False)["status"] == "rejected"
    assert not [c for c in CALLS if c[0] == "sendMessage"]
    with pytest.raises(Exception):
        svc._act(r["id"], approve=True)

def test_validation(svc):
    from pai import telegram as T
    with pytest.raises(T.TelegramError, match="пустой"):
        T.post_from_tool("public", "   ")
    with pytest.raises(T.TelegramError, match="длиннее"):
        T.post_from_tool("public", "x" * 4001)
    with pytest.raises(T.TelegramError, match="неизвестный канал"):
        T.post_from_tool("other", "hi")

def test_group_messages_are_read_once(svc):
    from pai import telegram as T
    UPDATES[:] = [_msg(1, -1003, 10, "Кто знает про GeoJEPA?"),
                  _msg(2, -999, 11, "чужая группа"),           # не настроена -> игнорируется
                  _msg(3, -1003, 12, "и ещё вопрос")]
    r = T.read_from_tool("group")
    assert r["count"] == 2 and [m["text"] for m in r["messages"]] == ["Кто знает про GeoJEPA?", "и ещё вопрос"]
    assert r["messages"][0]["from"] == "alice" and r["messages"][0]["channel"] == "group"
    # повторная проверка: ничего нового, прочитанные не возвращаются
    assert T.read_from_tool("group")["count"] == 0
    # новое сообщение после опроса
    UPDATES.append(_msg(4, -1003, 13, "третье"))
    assert [m["text"] for m in T.read_from_tool("all")["messages"]] == ["третье"]

def test_poll_is_idempotent_and_offset_moves(svc):
    UPDATES[:] = [_msg(5, -1003, 20, "раз")]
    assert svc.poll()["new"] == 1
    assert svc.poll()["new"] == 0          # offset прошёл, дубля нет
    assert svc.inbox_view()["unread"] == 1

def test_channel_username_matching(svc):
    svc.save_channels([{"key": "group", "chat_id": "@my_group", "mode": "approve", "read": True}])
    UPDATES[:] = [{"update_id": 6, "message": {"message_id": 30, "date": 1, "chat": {"id": -555, "username": "my_group", "type": "supergroup"},
                   "from": {"username": "bob"}, "text": "по username"}}]
    assert svc.poll()["new"] == 1

def test_read_channel_only_if_enabled(svc):
    svc.save_channels([{"key": "group", "chat_id": "-1003", "mode": "approve", "read": False}])
    UPDATES[:] = [_msg(7, -1003, 40, "не читать")]
    assert svc.poll()["new"] == 0

def test_read_failure_returns_stored_with_note(svc, monkeypatch):
    from pai import telegram as T
    svc.set_token("WRONG")
    r = svc.read_new("group")
    assert r["count"] == 0 and "не удалось" in r["note"] and "WRONG" not in r["note"]

def test_token_never_in_public_view(svc):
    v = svc.public_view()
    assert v["token_set"] is True and "T123456789" not in json.dumps(v)

def test_tool_schemas_registered():
    from pai.tools.schemas import TOOL_SCHEMAS
    names = {t["function"]["name"]: t for t in TOOL_SCHEMAS}
    assert "telegram_read" in names and "telegram_post" in names
    assert "group" in names["telegram_post"]["function"]["parameters"]["properties"]["channel"]["enum"]
