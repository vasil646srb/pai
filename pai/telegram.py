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
            return {"new": new, "fetched": len(updates)}

    def process_group(self, limit: int = 5) -> dict:
        """For new group messages: Jev decides if a reply is needed; if so the responder (persona brain) writes it
        and the text goes through the group channel (queue or auto, per channel mode). Each message is handled once."""
        if _responder is None:
            return {"processed": 0, "reason": "агент не подключён"}
        if not self.auto_reply_enabled():
            return {"processed": 0, "reason": "автоответы выключены"}
        with self._lock:
            items = self._load_inbox()
        todo = [i for i in items if i.get("channel") == "group" and not i.get("analyzed") and not i.get("bot")][:limit]
        handled = 0
        for it in todo:
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
            if verdict["kind"] == "spam" or not verdict["needs_reply"]:
                continue
            try:
                reply = _responder(it)
                if reply:
                    r = self.submit("group", reply, verified=True)
                    it["reply"] = {"status": r.get("status"), "id": r.get("id"), "error": r.get("error", "")}
            except HeldReply as h:
                it["held"], it["draft"] = str(h), h.draft
            except TelegramError as e:
                it["error"] = str(e)
            except Exception as e:
                _log.exception("telegram responder failed")
                it["error"] = f"{type(e).__name__}"
        with self._lock:
            byid = {i["id"]: i for i in todo}
            self._save_inbox([byid.get(i["id"], i) for i in self._load_inbox()])
        return {"processed": handled, "checked": len(todo)}

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
            picked = [i for i in items if not i.get("read") and (channel == "all" or i.get("channel") == channel)]
            picked = picked[:limit]
            ids = {i["id"] for i in picked}
            for i in items:
                if i["id"] in ids:
                    i["read"] = True
            self._save_inbox(items)
        return {"count": len(picked), "messages": [
            {"channel": i["channel"], "from": i["from"], "text": i["text"], "date": i["date"]} for i in picked],
            "note": note}

    def _send(self, chat_id: str, text: str) -> int:
        res = self._call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
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

    def submit(self, key: str, text: str, verified: bool = False) -> dict:
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
                "created": time.time(), "sent": None, "message_id": None, "error": "", "verified": verified}
        if mode == "auto":
            return self._publish(item, ch)
        with self._lock:
            items = self._load_queue()
            items.append(item)
            self._save_queue(items)
        return {"id": item["id"], "status": "pending", "note": "ждёт подтверждения в настройках Telegram"}

    def _publish(self, item: dict, ch: dict) -> dict:
        try:
            item["message_id"] = self._send(ch["chat_id"], item["text"])
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


def set_responder(fn) -> None:
    global _responder
    _responder = fn


def set_classifier(fn) -> None:
    global _classifier
    _classifier = fn


def _poll_loop(interval: int) -> None:
    while True:
        time.sleep(interval)
        try:
            svc = get_services()
            if svc.token():
                svc.poll()
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
        interval = int(os.environ.get("PAI_TELEGRAM_POLL_SEC", "60"))
    except ValueError:
        interval = 60
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
