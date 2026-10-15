"""Telegram publishing through the Bot API. Two channels:

  public  — default mode 'approve': a post goes to the queue and is sent only after the owner confirms it;
  private — default mode 'auto': the post is sent at once (only the owner and the assistant read it).

The bot token lives in the secret store (name TELEGRAM_BOT_TOKEN), never in settings files or logs.
Settings: <data_dir>/telegram_settings.json. Queue: <data_dir>/telegram_queue.json.
Env: PAI_TELEGRAM_API overrides the API base (tests use a fake server).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_log = logging.getLogger(__name__)

TOKEN_SECRET = "TELEGRAM_BOT_TOKEN"
MAX_LEN = 4000  # Telegram allows 4096 characters per message
CHANNEL_KEYS = ("public", "private", "group")
MODES = ("approve", "auto")
DEFAULT_CHANNELS = [
    {"key": "public", "title": "Публичный канал", "chat_id": "", "mode": "approve", "read": False},
    {"key": "private", "title": "Приватный канал", "chat_id": "", "mode": "auto", "read": False},
    {"key": "group", "title": "Группа", "chat_id": "", "mode": "approve", "read": True},
]
QUEUE_KEEP = 200  # finished items kept for history
INBOX_KEEP = 500
UPDATE_TYPES = ["message", "channel_post"]
# Group messages older than this are not answered (first poll after a restart can return a backlog).
ANALYSIS_KEYS = ("verdict", "needs_reply", "analyzed", "tries", "reply", "held", "draft", "error")
POLICY_DEFAULTS = {
    "owner_id": 0,                    # numeric Telegram user id of the owner; 0 = not set
    "owner_commands": False,          # private messages from the owner are commands for the brain persona
    "owner_space": "personal",        # memory space the owner's commands run in
    "group_tools": ["web_search"],    # tools of the group persona (GROUP_TOOL_OPTIONS + active custom functions)
    "group_spaces": [],               # extra spaces the group persona may READ (its own group space is always on)
    "jev_min_confidence": 0.6,        # Jev threshold for "needs a reply": higher = stricter
    "jev_reply_kinds": ["question", "request", "chat", "other"],
}
GROUP_TOOL_OPTIONS = ("get_fragment", "search_archive", "calculate_time_delta",
                      "list_fragments_by_timeframe", "web_search", "web_fetch")
JEV_KINDS = ("question", "request", "chat", "other", "spam")
MAX_POLICY_SPACES = 20


class TelegramError(Exception):
    pass


class HeldReply(Exception):
    """The model's reply did not pass the answer check: nothing is sent; the draft is kept for review."""
    def __init__(self, reason: str, draft: str = ""):
        super().__init__(reason)
        self.draft = draft


class TelegramService:
    def __init__(self, data_dir, secrets, api_base: str = ""):
        self.dir = Path(data_dir)
        self.secrets = secrets
        self.api_base = (api_base or os.environ.get("PAI_TELEGRAM_API") or "https://api.telegram.org").rstrip("/")
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._group_lock = threading.Lock()  # background poller and the "process now" button must not answer twice
        self._owner_lock = threading.Lock()

    # ---------- settings ----------
    @property
    def _settings_path(self) -> Path:
        return self.dir / "telegram_settings.json"

    def channels(self) -> list[dict]:
        saved = {}
        try:
            d = json.loads(self._settings_path.read_text(encoding="utf-8"))
            saved = {c.get("key"): c for c in d.get("channels", []) if isinstance(c, dict)}
        except (OSError, ValueError):
            pass
        out = []
        for base in DEFAULT_CHANNELS:
            c = {**base, **{k: v for k, v in saved.get(base["key"], {}).items() if k in ("chat_id", "mode", "title", "read")}}
            out.append(c)
        return out

    def _settings_doc(self) -> dict:
        try:
            d = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def policy(self) -> dict:
        saved = self._settings_doc().get("policy")
        saved = saved if isinstance(saved, dict) else {}
        return {**POLICY_DEFAULTS, **{k: saved[k] for k in POLICY_DEFAULTS if k in saved}}

    def save_policy(self, body: dict) -> None:
        new = dict(self.policy())
        if "owner_id" in body:
            raw = str(body.get("owner_id") or "").strip()
            if raw and not raw.isdigit():
                raise TelegramError("ID владельца: только цифры (числовой Telegram ID, например 123456789)")
            new["owner_id"] = int(raw) if raw else 0
        if "owner_commands" in body:
            new["owner_commands"] = bool(body["owner_commands"])
        if "owner_space" in body:
            new["owner_space"] = (str(body["owner_space"] or "").strip()[:80]) or "personal"
        opts = tg_options()
        if "group_tools" in body:
            tools = [str(t) for t in (body["group_tools"] or [])]
            bad = [t for t in tools if t not in opts["tools"]]
            if bad:
                raise TelegramError(f"инструменты группы недоступны: {', '.join(bad)}")
            new["group_tools"] = list(dict.fromkeys(tools))
        if "group_spaces" in body:
            spaces = [str(x).strip() for x in (body["group_spaces"] or []) if str(x).strip()]
            if len(spaces) > MAX_POLICY_SPACES:
                raise TelegramError(f"пространств памяти не больше {MAX_POLICY_SPACES}")
            new["group_spaces"] = list(dict.fromkeys(spaces))
        if "jev_min_confidence" in body:
            try:
                v = float(body["jev_min_confidence"])
            except (TypeError, ValueError):
                raise TelegramError("порог Jev: число от 0.5 до 0.95")
            new["jev_min_confidence"] = round(min(0.95, max(0.5, v)), 2)
        if "jev_reply_kinds" in body:
            kinds = [str(k) for k in (body["jev_reply_kinds"] or [])]
            bad = [k for k in kinds if k not in JEV_KINDS]
            if bad:
                raise TelegramError(f"неизвестные типы сообщений: {', '.join(bad)}")
            new["jev_reply_kinds"] = [k for k in JEV_KINDS if k in kinds]
        doc = self._settings_doc()
        doc["policy"] = new
        self.dir.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def auto_reply_enabled(self) -> bool:
        return bool(self._settings_doc().get("group_auto_reply", True))

    def set_auto_reply(self, value: bool) -> None:
        doc = self._settings_doc()
        doc["group_auto_reply"] = bool(value)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def save_channels(self, channels: list[dict]) -> None:
        by_key = {c.get("key"): c for c in channels if isinstance(c, dict)}
        clean = []
        for base in DEFAULT_CHANNELS:
            c = by_key.get(base["key"], {})
            chat_id = str(c.get("chat_id", "") or "").strip()
            if chat_id and not (chat_id.startswith("@") or chat_id.lstrip("-").isdigit()):
                raise TelegramError(f"{base['title']}: укажите @username или числовой id (например -100…)")
            mode = str(c.get("mode", base["mode"]))
            if mode not in MODES:
                raise TelegramError(f"{base['title']}: режим должен быть approve или auto")
            read = bool(c.get("read", base["read"]))
            clean.append({"key": base["key"], "title": base["title"], "chat_id": chat_id, "mode": mode, "read": read})
        doc = self._settings_doc()
        doc["channels"] = clean
        self.dir.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def token(self) -> str:
        if self.secrets is None:
            return ""
        return self.secrets.values().get(TOKEN_SECRET, "") or ""

    def set_token(self, value: str) -> None:
        value = (value or "").strip()
        if value:
            self.secrets.set(TOKEN_SECRET, value)

    def public_view(self) -> dict:
        tok = self.token()
        return {
            "token_set": bool(tok),
            "token_preview": f"{'*' * max(0, len(tok) - 4)}{tok[-4:]}" if tok else "",
            "channels": self.channels(),
            "group_auto_reply": self.auto_reply_enabled(),
            "policy": self.policy(),
            "api_base": self.api_base,
        }

    def _channel(self, key: str) -> dict:
        for c in self.channels():
            if c["key"] == key:
                if not c["chat_id"]:
                    raise TelegramError(f"{c['title']}: не задан chat_id в настройках Telegram")
                return c
        raise TelegramError(f"неизвестный канал '{key}', доступны: {', '.join(CHANNEL_KEYS)}")

    # ---------- Bot API ----------
    def _call(self, method: str, payload: dict | None = None):
        tok = self.token()
        if not tok:
            raise TelegramError("токен бота не задан (Telegram → токен)")
        req = urllib.request.Request(
            f"{self.api_base}/bot{tok}/{method}", data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read(200_000).decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read(200_000).decode("utf-8", "replace"))
            except ValueError:
                body = {}
            raise TelegramError(f"{method}: {body.get('description') or 'HTTP ' + str(e.code)}")
        except Exception as e:  # never echo the URL: it contains the token
            raise TelegramError(f"{method}: сеть или таймаут ({type(e).__name__})")
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description', 'ошибка')}")
        return body.get("result")

    def check(self) -> dict:
        me = self._call("getMe")
        out = {"bot": me.get("username", ""), "channels": []}
        for c in self.channels():
            row = {"key": c["key"], "title": c["title"], "chat_id": c["chat_id"], "ok": False, "detail": ""}
            if not c["chat_id"]:
                row["detail"] = "chat_id не задан"
            else:
                try:
                    m = self._call("getChatMember", {"chat_id": c["chat_id"], "user_id": me["id"]})
                    can = m.get("can_post_messages", m.get("status") in ("administrator", "creator"))
                    row["ok"] = bool(can)
                    row["detail"] = f"статус бота: {m.get('status')}, право публикации: {'да' if can else 'нет'}"
                except TelegramError as e:
                    row["detail"] = str(e)
            out["channels"].append(row)
        return out

    # ---------- inbox (incoming messages from groups and reading channels) ----------
    @property
    def _inbox_path(self) -> Path:
        return self.dir / "telegram_inbox.json"

    @property
    def _state_path(self) -> Path:
        return self.dir / "telegram_state.json"

    def _load_inbox(self) -> list[dict]:
        try:
            d = json.loads(self._inbox_path.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save_inbox(self, items: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._inbox_path.write_text(json.dumps(items[-INBOX_KEEP:], ensure_ascii=False), encoding="utf-8")

    def _offset(self) -> int:
        try:
            return int(json.loads(self._state_path.read_text(encoding="utf-8")).get("offset", 0))
        except (OSError, ValueError, TypeError):
            return 0

    def _set_offset(self, n: int) -> None:
        self._state_path.write_text(json.dumps({"offset": n}), encoding="utf-8")

    def _match_channel(self, chat: dict) -> dict | None:
        cid = str(chat.get("id", ""))
        uname = f"@{chat['username']}" if chat.get("username") else ""
        for c in self.channels():
            if c.get("read") and c.get("chat_id") and c["chat_id"] in (cid, uname):
                return c
        return None

    def poll(self) -> dict:
        """Fetch new updates from Telegram and keep messages of configured read-enabled chats. Idempotent."""
        with self._poll_lock:
            off = self._offset()
            owner_id = self.policy()["owner_id"]
            pairing: list = []
            payload = {"timeout": 0, "allowed_updates": UPDATE_TYPES, "limit": 100}
            if off:
                payload["offset"] = off + 1
            updates = self._call("getUpdates", payload) or []
            if not updates:
                return {"new": 0, "fetched": 0}
            last = off
            with self._lock:
                inbox = self._load_inbox()
                seen = {i["id"] for i in inbox}
                new = 0
                for u in updates:
                    last = max(last, int(u.get("update_id", 0)))
                    msg = u.get("message") or u.get("channel_post")
                    if not msg:
                        continue
                    chat = msg.get("chat", {})
                    if chat.get("type") == "private":
                        sender_id = (msg.get("from") or {}).get("id")
                        if not sender_id:
                            continue
                        if not owner_id:
                            pairing.append(sender_id)  # no owner yet: tell the sender their id, store nothing
                        elif sender_id == owner_id:
                            item_id = f"{chat.get('id')}:{msg.get('message_id')}"
                            if item_id not in seen:
                                sender = msg.get("from") or {}
                                inbox.append({
                                    "id": item_id, "channel": "owner", "chat_title": "",
                                    "from": sender.get("username") or sender.get("first_name") or "",
                                    "text": (msg.get("text") or "")[:2000], "date": msg.get("date"),
                                    "read": False, "bot": False, "chat_id": chat.get("id"),
                                })
                                seen.add(item_id)
                                new += 1
                        continue
                    ch = self._match_channel(chat)
                    if ch is None:
                        continue
                    item_id = f"{chat.get('id')}:{msg.get('message_id')}"
                    if item_id in seen:
                        continue
                    sender = msg.get("from") or msg.get("sender_chat") or {}
                    inbox.append({
                        "id": item_id, "channel": ch["key"],
                        "chat_title": chat.get("title") or ch["title"],
                        "from": sender.get("username") or sender.get("first_name") or sender.get("title") or "",
                        "text": (msg.get("text") or msg.get("caption") or "")[:2000],
                        "date": msg.get("date"), "read": False, "bot": bool(sender.get("is_bot")),
                    })
                    seen.add(item_id)
                    new += 1
                self._save_inbox(inbox)
            self._set_offset(last)  # after the inbox is saved: a crash re-reads and dedupes by id
            for uid in dict.fromkeys(pairing):
                try:
                    self._send(str(uid), f"Ваш Telegram ID: {uid}. Укажите его в PAI → Telegram → Владелец, "
                                         "чтобы включить команды.")
                except TelegramError:
                    pass
            return {"new": new, "fetched": len(updates)}

    def process_group(self, limit: int = 5) -> dict:
        """For new group messages: Jev decides if a reply is needed; if so the responder (persona brain) writes it
        and the text goes through the group channel (queue or auto, per channel mode). Each message is handled once."""
        if _responder is None:
            return {"processed": 0, "reason": "агент не подключён"}
        if not self.auto_reply_enabled():
            return {"processed": 0, "reason": "автоответы выключены"}
        if not self._group_lock.acquire(blocking=False):
            return {"processed": 0, "reason": "уже обрабатывается"}
        try:
            return self._process_group(limit)
        finally:
            self._group_lock.release()

    def _process_group(self, limit: int) -> dict:
        with self._lock:
            items = self._load_inbox()
        todo = [i for i in items if i.get("channel") == "group" and not i.get("analyzed") and not i.get("bot")][:limit]
        pol = self.policy()
        handled = 0
        max_age = _max_age()
        for it in todo:
            if max_age and it.get("date") and time.time() - int(it["date"]) > max_age:
                it["analyzed"], it["verdict"] = True, "stale"  # old backlog: never answer it late
                continue
            if _classifier is not None:
                try:
                    verdict = _classifier(it.get("text", ""), it.get("from", ""))
                except Exception:
                    _log.warning("telegram classifier failed", exc_info=True)
                    verdict = None
                if verdict is None:  # Jev unavailable: retry on the next cycle, give up after 3 tries
                    it["tries"] = it.get("tries", 0) + 1
                    if it["tries"] >= 3:
                        it["analyzed"], it["verdict"] = True, "unavailable"
                    continue
            else:
                verdict = {"needs_reply": True, "kind": "unknown"}
            it["verdict"], it["needs_reply"], it["analyzed"] = verdict["kind"], verdict["needs_reply"], True
            handled += 1
            if (verdict["kind"] == "spam" or not verdict["needs_reply"]
                    or (verdict["kind"] != "unknown" and verdict["kind"] not in pol["jev_reply_kinds"])):
                continue
            try:
                reply = _responder(it)
                if reply:
                    r = self.submit("group", reply, verified=True, reply_to=_source_message_id(it))
                    it["reply"] = {"status": r.get("status"), "id": r.get("id"), "error": r.get("error", "")}
            except HeldReply as h:
                it["held"], it["draft"] = str(h), h.draft
            except TelegramError as e:
                it["error"] = str(e)
            except Exception as e:
                _log.exception("telegram responder failed")
                it["error"] = f"{type(e).__name__}"
        with self._lock:  # re-read: telegram_read may have marked items read meanwhile; copy only analysis fields
            byid = {i["id"]: i for i in todo}
            fresh = self._load_inbox()
            for f in fresh:
                src = byid.get(f["id"])
                if src is not None:
                    f.update({k: src[k] for k in ANALYSIS_KEYS if k in src})
            self._save_inbox(fresh)
        return {"processed": handled, "checked": len(todo)}

    def process_owner(self, limit: int = 3) -> dict:
        """Owner's private messages are commands: the owner's responder runs them and the answer goes back
        to the owner's private chat. Not behind the approval queue: the owner is the one who asked."""
        if _owner_responder is None:
            return {"processed": 0, "reason": "агент не подключён"}
        pol = self.policy()
        if not pol["owner_commands"] or not pol["owner_id"]:
            return {"processed": 0, "reason": "команды владельца выключены"}
        if not self._owner_lock.acquire(blocking=False):
            return {"processed": 0, "reason": "уже выполняется"}
        try:
            return self._process_owner(limit, pol["owner_id"])
        finally:
            self._owner_lock.release()

    def _process_owner(self, limit: int, owner_id: int) -> dict:
        with self._lock:
            items = self._load_inbox()
        todo = [i for i in items if i.get("channel") == "owner" and not i.get("analyzed")][:limit]
        for it in todo:
            it["analyzed"] = True
            try:
                reply = _owner_responder(it) or "(готово, текстового ответа нет)"
            except Exception as e:
                _log.exception("owner command failed")
                reply = f"ошибка выполнения: {type(e).__name__}"
            try:
                for part in _chunks(reply):
                    self._send(str(owner_id), part, reply_to=_source_message_id(it))
                it["reply_status"] = "sent"
            except TelegramError as e:
                it["reply_status"], it["error"] = "failed", str(e)
        with self._lock:
            byid = {i["id"]: i for i in todo}
            fresh = self._load_inbox()
            for f in fresh:
                src = byid.get(f["id"])
                if src is not None:
                    f.update({k: src[k] for k in ("analyzed", "reply_status", "error") if k in src})
            self._save_inbox(fresh)
        return {"processed": len(todo), "checked": len(todo)}

    def inbox_view(self) -> dict:
        items = self._load_inbox()
        return {"unread": sum(1 for i in items if not i.get("read")), "recent": items[-30:][::-1]}

    def read_new(self, channel: str = "all", limit: int = 50) -> dict:
        """Return unread messages (after a fresh poll) and mark them read."""
        note = ""
        try:
            self.poll()
        except TelegramError as e:
            note = f"не удалось проверить Telegram сейчас: {e}"
        with self._lock:
            items = self._load_inbox()
            picked = [i for i in items if not i.get("read") and i.get("channel") != "owner"
                      and (channel == "all" or i.get("channel") == channel)]
            picked = picked[:limit]
            ids = {i["id"] for i in picked}
            for i in items:
                if i["id"] in ids:
                    i["read"] = True
            self._save_inbox(items)
        return {"count": len(picked), "messages": [
            {"channel": i["channel"], "from": i["from"], "text": i["text"], "date": i["date"]} for i in picked],
            "note": note}

    def _send(self, chat_id: str, text: str, reply_to: int | None = None) -> int:
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        res = self._call("sendMessage", payload)
        return int(res.get("message_id", 0))

    # ---------- queue ----------
    @property
    def _queue_path(self) -> Path:
        return self.dir / "telegram_queue.json"

    def _load_queue(self) -> list[dict]:
        try:
            d = json.loads(self._queue_path.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save_queue(self, items: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._queue_path.write_text(json.dumps(items[-QUEUE_KEEP:], ensure_ascii=False), encoding="utf-8")

    def submit(self, key: str, text: str, verified: bool = False, reply_to: int | None = None) -> dict:
        """verified=True only for text that passed the answer check (background replies, owner's own text).
        Unverified text (from the model via the tool) never goes out at once in public or group channels."""
        text = (text or "").strip()
        if not text:
            raise TelegramError("пустой текст")
        if len(text) > MAX_LEN:
            raise TelegramError(f"текст длиннее {MAX_LEN} символов")
        ch = self._channel(key)
        mode = ch["mode"]
        if not verified and key in ("public", "group"):
            mode = "approve"
        item = {"id": uuid.uuid4().hex[:10], "channel": key, "text": text, "status": "pending",
                "created": time.time(), "sent": None, "message_id": None, "error": "", "verified": verified,
                "reply_to": reply_to}
        if mode == "auto":
            return self._publish(item, ch)
        with self._lock:
            items = self._load_queue()
            items.append(item)
            self._save_queue(items)
        return {"id": item["id"], "status": "pending", "note": "ждёт подтверждения в настройках Telegram"}

    def _publish(self, item: dict, ch: dict) -> dict:
        try:
            item["message_id"] = self._send(ch["chat_id"], item["text"], item.get("reply_to"))
            item["status"], item["sent"] = "sent", time.time()
        except TelegramError as e:
            item["status"], item["error"] = "failed", str(e)
        with self._lock:
            items = [i for i in self._load_queue() if i.get("id") != item["id"]] + [item]
            self._save_queue(items)
        return {"id": item["id"], "status": item["status"], "message_id": item["message_id"], "error": item["error"]}

    def queue_view(self) -> dict:
        items = self._load_queue()
        return {"pending": [i for i in items if i.get("status") == "pending"],
                "history": [i for i in items if i.get("status") != "pending"][-30:][::-1]}

    def _act(self, item_id: str, approve: bool) -> dict:
        with self._lock:
            items = self._load_queue()
        item = next((i for i in items if i.get("id") == item_id), None)
        if item is None or item.get("status") != "pending":
            raise TelegramError("запись не найдена или уже обработана")
        if not approve:
            item["status"] = "rejected"
            with self._lock:
                self._save_queue([i for i in self._load_queue() if i.get("id") != item_id] + [item])
            return item
        return self._publish(item, self._channel(item["channel"]))


_svc: TelegramService | None = None
_poller_started = False
_responder = None   # fn(item) -> reply text | None   (persona brain, see telegram_agent.py)
_classifier = None  # fn(text, sender) -> {"needs_reply": bool, "kind": str} | None   (Jev)
_owner_responder = None  # fn(item) -> reply text | None   (brain persona runs the owner's command)
_options_source = None   # fn() -> {"tools": [...], "spaces": [...]}


def set_responder(fn) -> None:
    global _responder
    _responder = fn


def set_classifier(fn) -> None:
    global _classifier
    _classifier = fn


def set_owner_responder(fn) -> None:
    global _owner_responder
    _owner_responder = fn


def set_options_source(fn) -> None:
    global _options_source
    _options_source = fn


def tg_options() -> dict:
    """Tools and memory spaces the Telegram page may offer. The source is set by the web layer."""
    if _options_source is None:
        return {"tools": list(GROUP_TOOL_OPTIONS), "spaces": []}
    return _options_source()


def _chunks(text: str) -> list[str]:
    text = text or ""
    return [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]


def _max_age() -> int:
    try:
        return max(0, int(os.environ.get("PAI_TELEGRAM_MAX_AGE_SEC", "600")))
    except ValueError:
        return 600


def _source_message_id(item: dict) -> int | None:
    """Inbox ids are '<chat_id>:<message_id>'."""
    try:
        return int(str(item.get("id", "")).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _poll_loop(interval: int) -> None:
    while True:
        time.sleep(interval)
        try:
            svc = get_services()
            if svc.token():
                svc.poll()
                svc.process_owner()
                svc.process_group()
        except TelegramError as e:
            _log.warning("telegram poll: %s", e)
        except Exception:
            _log.exception("telegram poll crashed")


def init_services(secrets, data_dir) -> TelegramService:
    """Creates the service and, once per process, a background poller (PAI_TELEGRAM_POLL_SEC, default 60, 0 = off)."""
    global _svc, _poller_started
    _svc = TelegramService(data_dir, secrets)
    try:
        interval = int(os.environ.get("PAI_TELEGRAM_POLL_SEC", "15"))
    except ValueError:
        interval = 15
    if interval > 0 and not _poller_started:
        _poller_started = True
        threading.Thread(target=_poll_loop, args=(interval,), name="pai-telegram-poll", daemon=True).start()
    return _svc


def get_services() -> TelegramService:
    if _svc is None:
        raise TelegramError("Telegram не инициализирован")
    return _svc


def post_from_tool(channel: str, text: str) -> dict:
    """Entry point for the telegram_post tool. Public posts wait for approval, private ones go out at once."""
    return get_services().submit(channel, text)


def send_manual(channel: str, text: str) -> dict:
    """Used by the Telegram page: the owner's own text. Public/group -> queue, private -> at once."""
    return get_services().submit(channel, text, verified=True)


def read_from_tool(channel: str = "all") -> dict:
    """Entry point for the telegram_read tool: fresh messages from the group and reading channels."""
    if channel not in ("all", "group", "public", "private"):
        raise TelegramError("channel: all | group | public | private")
    return get_services().read_new(channel)
