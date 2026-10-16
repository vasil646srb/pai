"""Telegram publishing through the Bot API. Two channels:

  public  — default mode 'approve': a post goes to the queue and is sent only after the owner confirms it;
  private — default mode 'auto': the post is sent at once (only the owner and the assistant read it).

The bot token lives in the secret store (name TELEGRAM_BOT_TOKEN), never in settings files or logs.
Settings: <data_dir>/telegram_settings.json. Queue: <data_dir>/telegram_queue.json.
Env: PAI_TELEGRAM_API overrides the API base (tests use a fake server).
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Markdown -> Telegram HTML and message splitting (see md_to_tg_html).

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
UPDATE_TYPES = ["message", "channel_post", "callback_query"]
# Reactions shown on the incoming message while the assistant works on it.
REACT_PROCESSING = "👀"   # picked up, working on it
REACT_DONE = "👍"         # reply sent / published
REACT_HELD = "🤔"         # reply drafted, waiting for approval
# Group messages older than this are not answered (first poll after a restart can return a backlog).
ANALYSIS_KEYS = ("verdict", "needs_reply", "analyzed", "tries", "reply", "held", "draft", "error")
# Telegram has no font-size control for bots: the owner sets how long one message may be, per destination.
TEXT_SIZES = {"short": 600, "normal": 1500, "long": 4000}
TEXT_SIZE_KEYS = ("owner", "public", "private", "group")
DEFAULT_TEXT_SIZES = {"owner": "normal", "public": "normal", "private": "long", "group": "short"}
POLICY_DEFAULTS = {
    "text_sizes": dict(DEFAULT_TEXT_SIZES),  # destination -> short | normal | long
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

    def text_limit(self, destination: str) -> int:
        """Max characters of one message for owner / public / private / group."""
        size = self.policy()["text_sizes"].get(destination, "normal")
        return TEXT_SIZES.get(size, TEXT_SIZES["normal"])

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
        if "text_sizes" in body:
            sizes = dict(new["text_sizes"])
            raw = body["text_sizes"] or {}
            for key, val in raw.items():
                if key not in TEXT_SIZE_KEYS or val not in TEXT_SIZES:
                    raise TelegramError(f"размер сообщения: неизвестное значение {key}={val}")
                sizes[key] = val
            new["text_sizes"] = {k: sizes.get(k, DEFAULT_TEXT_SIZES[k]) for k in TEXT_SIZE_KEYS}
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
            callbacks: list = []
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
                    if u.get("callback_query"):
                        callbacks.append(u["callback_query"])
                        continue
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
            for cq in callbacks:
                try:
                    self._on_callback(cq)
                except Exception:
                    _log.exception("telegram callback failed")
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
            self._react(it["id"], REACT_PROCESSING)
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
                        self._react(it["id"], None)
                    continue
            else:
                verdict = {"needs_reply": True, "kind": "unknown"}
            it["verdict"], it["needs_reply"], it["analyzed"] = verdict["kind"], verdict["needs_reply"], True
            handled += 1
            if (verdict["kind"] == "spam" or not verdict["needs_reply"]
                    or (verdict["kind"] != "unknown" and verdict["kind"] not in pol["jev_reply_kinds"])):
                self._react(it["id"], None)
                continue
            final = None
            try:
                reply = _responder(it)
                if reply:
                    reply = shorten(reply, self.text_limit("group"))
                    r = self.submit("group", reply, verified=True, reply_to=_source_message_id(it))
                    it["reply"] = {"status": r.get("status"), "id": r.get("id"), "error": r.get("error", "")}
                    final = REACT_DONE
            except HeldReply as h:
                it["held"], it["draft"] = str(h), h.draft
                final = REACT_HELD
            except TelegramError as e:
                it["error"] = str(e)
            except Exception as e:
                _log.exception("telegram responder failed")
                it["error"] = f"{type(e).__name__}"
            self._react(it["id"], final)
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
            revising = next((i for i in self._load_queue()
                             if i.get("status") == "pending" and i.get("revise")), None)
        todo = [i for i in items if i.get("channel") == "owner" and not i.get("analyzed")][:limit]
        for it in todo:
            it["analyzed"] = True
            self._react(it["id"], REACT_PROCESSING)
            prompt = it
            if revising is not None:
                prompt = dict(it, text=(
                    f"Владелец просит переделать пост для канала «{revising['channel']}».\n"
                    f"Текущий черновик:\n---\n{revising['text']}\n---\n"
                    f"Правка владельца: {it.get('text', '')}\n\n"
                    f"Верни новую версию текста и сразу вызови telegram_post с channel={revising['channel']}. "
                    "Не публикуй напрямую: черновик уйдёт владельцу на подтверждение."))
            try:
                reply = _owner_responder(prompt) or "(готово, текстового ответа нет)"
            except Exception as e:
                _log.exception("owner command failed")
                reply = f"ошибка выполнения: {type(e).__name__}"
            try:
                for part in split_text(reply, self.text_limit("owner")):
                    self._send(str(owner_id), part, reply_to=_source_message_id(it))
                it["reply_status"] = "sent"
            except TelegramError as e:
                it["reply_status"], it["error"] = "failed", str(e)
            self._react(it["id"], REACT_DONE if it.get("reply_status") == "sent" else None)
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

    def _react(self, item_id: str, emoji: str | None) -> None:
        """Set (or clear, when emoji is None) the bot's reaction on an inbox message. Never fails the flow."""
        try:
            chat_id, msg_id = str(item_id).rsplit(":", 1)
            reaction = [{"type": "emoji", "emoji": emoji}] if emoji else []
            self._call("setMessageReaction", {"chat_id": int(chat_id), "message_id": int(msg_id),
                                              "reaction": reaction})
        except (TelegramError, ValueError, IndexError):
            _log.debug("setMessageReaction failed for %s", item_id, exc_info=True)

    def _send(self, chat_id: str, text: str, reply_to: int | None = None, markup: dict | None = None) -> int:
        base = {"chat_id": chat_id, "disable_web_page_preview": True}
        if reply_to:
            base["reply_to_message_id"] = reply_to
        if markup is not None:
            base["reply_markup"] = markup
        try:
            res = self._call("sendMessage", {**base, "text": md_to_tg_html(text), "parse_mode": "HTML"})
        except TelegramError as e:
            if "parse" not in str(e).lower() and "entit" not in str(e).lower():
                raise
            res = self._call("sendMessage", {**base, "text": text})  # markup rejected: send the raw text
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
        ch = self._channel(key)
        limit = self.text_limit(key)
        if len(text) > limit:
            raise TelegramError(f"текст длиннее {limit} символов: размер для этого канала задан в настройках Telegram, сократите текст")
        # public: never goes out without the owner's button press, whatever the channel mode says
        mode = "approve" if key == "public" or (not verified and key == "group") else ch["mode"]
        item = {"id": uuid.uuid4().hex[:10], "channel": key, "text": text, "status": "pending",
                "created": time.time(), "sent": None, "message_id": None, "error": "", "verified": verified,
                "reply_to": reply_to}
        if mode == "auto":
            return self._publish(item, ch)
        with self._lock:
            items = self._load_queue()
            items.append(item)
            self._save_queue(items)
        if key == "public" or (key == "group" and mode == "approve"):
            self._offer(item)
        return {"id": item["id"], "status": "pending",
                "note": "черновик отправлен владельцу в Telegram с кнопками подтверждения"}

    # ---------- confirmation buttons in the owner's chat ----------
    def _offer(self, item: dict) -> None:
        """Send the draft to the owner with buttons. A new draft supersedes a draft that is being revised."""
        owner_id = self.policy()["owner_id"]
        if not owner_id:
            return
        with self._lock:
            items = self._load_queue()
            for old in items:
                if old.get("status") == "pending" and old.get("revise") and old.get("id") != item["id"]:
                    old["status"], old["revise"] = "superseded", False
            self._save_queue(items)
        iid = item["id"]
        markup = {"inline_keyboard": [
            [{"text": "✅ Опубликовать", "callback_data": f"pub:{iid}"},
             {"text": "✏️ Переделать", "callback_data": f"rev:{iid}"}],
            [{"text": "❌ Отменить", "callback_data": f"rej:{iid}"}],
        ]}
        label = {"public": "публичного канала", "group": "группы"}.get(item["channel"], "канала")
        parts = split_text(f"Черновик для {label}:\n\n" + item["text"], 3000)
        try:
            for part in parts[:-1]:
                self._send(str(owner_id), part)
            mid = self._send(str(owner_id), parts[-1], markup=markup)
        except TelegramError as e:
            _log.warning("draft offer failed: %s", e)
            return
        self._update_item(iid, draft_msg_id=mid)

    def _update_item(self, item_id: str, **fields) -> None:
        with self._lock:
            items = self._load_queue()
            for i in items:
                if i.get("id") == item_id:
                    i.update(fields)
            self._save_queue(items)

    def _pending_item(self, item_id: str) -> dict:
        item = next((i for i in self._load_queue() if i.get("id") == item_id), None)
        if item is None or item.get("status") != "pending":
            raise TelegramError("уже обработано или не найдено")
        return item

    def _answer_cb(self, cq_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", {"callback_query_id": cq_id, "text": text[:200]})
        except TelegramError:
            pass

    def _edit_status(self, chat_id: str, message_id, text: str) -> None:
        if not message_id:
            return
        try:
            self._call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                           "reply_markup": {"inline_keyboard": []}})
        except TelegramError:
            pass

    def _on_callback(self, cq: dict) -> None:
        cq_id = cq.get("id", "")
        owner_id = self.policy()["owner_id"]
        if not owner_id or (cq.get("from") or {}).get("id") != owner_id:
            self._answer_cb(cq_id, "Нет доступа")
            return
        action, _, item_id = str(cq.get("data") or "").partition(":")
        msg = cq.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id") or owner_id)
        mid = msg.get("message_id")
        try:
            if action == "pub":
                res = self._act(item_id, approve=True)
                note = ("✅ Опубликовано." if res["status"] == "sent"
                        else f"❌ Не отправлено: {res.get('error', '')}")
                self._answer_cb(cq_id, note)
                self._edit_status(chat_id, mid, note)
            elif action == "rej":
                self._act(item_id, approve=False)
                self._answer_cb(cq_id, "Отменено")
                self._edit_status(chat_id, mid, "❌ Отменено.")
            elif action == "rev":
                if not self.policy()["owner_commands"]:
                    self._answer_cb(cq_id, "Включите команды владельца, чтобы править текст")
                    return
                self._pending_item(item_id)
                self._update_item(item_id, revise=True)
                self._answer_cb(cq_id, "Жду правки")
                self._send(chat_id, "Напишите, что изменить в черновике.")
            elif action == "cok":
                self._run_action(item_id, cq_id, chat_id, mid)
            elif action == "cno":
                self._reject_action(item_id, cq_id, chat_id, mid)
            else:
                self._answer_cb(cq_id, "Неизвестное действие")
        except TelegramError as e:
            self._answer_cb(cq_id, str(e))

    # ---------- confirmations of bot actions (owner presses buttons) ----------
    @property
    def _actions_path(self) -> Path:
        return self.dir / "telegram_actions.json"

    def _load_actions(self) -> list[dict]:
        try:
            d = json.loads(self._actions_path.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save_actions(self, items: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._actions_path.write_text(json.dumps(items[-QUEUE_KEEP:], ensure_ascii=False), encoding="utf-8")

    def _set_action(self, item_id: str, **fields) -> None:
        with self._lock:
            acts = self._load_actions()
            for a in acts:
                if a.get("id") == item_id:
                    a.update(fields)
            self._save_actions(acts)

    def request_action(self, question: str, tool: str, arguments: dict) -> dict:
        """The bot asks the owner to confirm one tool call. Nothing runs until the owner presses confirm."""
        if tool not in CONFIRMABLE_TOOLS:
            raise TelegramError(f"инструмент {tool} нельзя запускать по подтверждению")
        if not isinstance(arguments, dict):
            raise TelegramError("arguments должен быть объектом")
        if _action_executor is None:
            raise TelegramError("исполнитель подтверждённых действий не подключён")
        owner_id = self.policy()["owner_id"]
        if not owner_id:
            raise TelegramError("владелец в Telegram не задан")
        question = (question or "").strip()[:500] or f"Выполнить {tool}"
        iid = uuid.uuid4().hex[:10]
        item = {"id": iid, "status": "pending", "question": question, "tool": tool,
                "arguments": arguments, "created": time.time(), "message_id": None}
        with self._lock:
            acts = self._load_actions()
            acts.append(item)
            self._save_actions(acts)
        details = json.dumps(arguments, ensure_ascii=False)[:800]
        text = f"Нужно подтверждение:\n\n{question}\n\nДействие: {tool}\n{details}"
        markup = {"inline_keyboard": [[
            {"text": "✅ Подтвердить", "callback_data": f"cok:{iid}"},
            {"text": "❌ Отмена", "callback_data": f"cno:{iid}"},
        ]]}
        try:
            mid = self._send(str(owner_id), text, markup=markup)
        except TelegramError:
            self._set_action(iid, status="failed")
            raise
        self._set_action(iid, message_id=mid)
        return {"status": "awaiting_confirmation", "id": iid,
                "note": "кнопки подтверждения отправлены владельцу в Telegram; результат придёт туда же"}

    def _run_action(self, item_id: str, cq_id: str, chat_id: str, mid) -> None:
        with self._lock:
            acts = self._load_actions()
            item = next((a for a in acts if a.get("id") == item_id), None)
            if item is None or item.get("status") != "pending":
                raise TelegramError("уже обработано или не найдено")
            if time.time() - float(item.get("created", 0)) > ACTION_TTL:
                item["status"] = "expired"
                self._save_actions(acts)
                raise TelegramError("запрос устарел")
            item["status"] = "running"  # a second press must not run it twice
            self._save_actions(acts)
        try:
            result = _action_executor(item["tool"], item["arguments"])
            ok = not (isinstance(result, dict) and result.get("tool_failed"))
        except Exception as e:
            _log.exception("confirmed action failed")
            result, ok = {"tool_failed": True, "error": str(e)}, False
        self._set_action(item_id, status="done" if ok else "failed")
        summary = json.dumps(result, ensure_ascii=False, default=str)[:1500]
        note = (("✅ Выполнено" if ok else "❌ Не выполнено") + f": {item['question']}\n\n{summary}")
        self._answer_cb(cq_id, "Выполнено" if ok else "Ошибка")
        self._edit_status(chat_id, mid, note)

    def _reject_action(self, item_id: str, cq_id: str, chat_id: str, mid) -> None:
        with self._lock:
            acts = self._load_actions()
            item = next((a for a in acts if a.get("id") == item_id), None)
            if item is None or item.get("status") != "pending":
                raise TelegramError("уже обработано или не найдено")
            item["status"] = "rejected"
            self._save_actions(acts)
        self._answer_cb(cq_id, "Отменено")
        self._edit_status(chat_id, mid, f"❌ Отменено: {item['question']}")

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


import html as _html
import re

_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def md_to_tg_html(text: str) -> str:
    """Model Markdown -> Telegram HTML (b, i, s, code, pre, a, blockquote). Telegram shows no Markdown.
    Code is held aside first, so markup inside it is never touched; everything else is HTML-escaped."""
    holds: list[str] = []

    def hold(tag: str, content: str) -> str:
        holds.append(f"<{tag}>{_html.escape(content, quote=False)}</{tag}>")
        return f"\x00{len(holds) - 1}\x00"

    t = _FENCE_RE.sub(lambda m: hold("pre", m.group(1).rstrip("\n")), text or "")
    t = _INLINE_CODE_RE.sub(lambda m: hold("code", m.group(1)), t)

    lines: list[str] = []
    table: list[str] = []

    def flush_table():
        if table:
            lines.append(hold("pre", "\n".join(table)))
            table.clear()

    for raw in t.split("\n"):
        line = raw.rstrip()
        if _TABLE_LINE_RE.match(line):
            table.append(line)
            continue
        flush_table()
        if re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line):
            lines.append("")
            continue
        m = re.match(r"\s*#{1,6}\s+(.*)", line)
        if m:
            lines.append("**" + m.group(1).strip() + "**")
            continue
        m = re.match(r"\s*[-*+]\s+(.*)", line)
        if m:
            lines.append("• " + m.group(1))
            continue
        m = re.match(r"\s*>\s?(.*)", line)
        if m:
            lines.append("\x01Q" + m.group(1))
            continue
        lines.append(line)
    flush_table()

    out: list[str] = []
    quote: list[str] = []
    for line in lines:
        if line.startswith("\x01Q"):
            quote.append(_html.escape(line[2:], quote=False))
            continue
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote = []
        out.append(line if line.startswith("\x00") else _html.escape(line, quote=False))
    if quote:
        out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
    s = "\n".join(out)

    s = re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)"<>]+)\)', lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: holds[int(m.group(1))], s)
    return s.strip()


def split_text(text: str, limit: int) -> list[str]:
    """Split at paragraph, line, sentence or word boundary so every part is at most `limit` characters."""
    text = (text or "").strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = -1
        for sep in ("\n\n", "\n", ". ", " "):
            i = text.rfind(sep, 0, limit)
            if i >= limit // 2:
                cut = i + (0 if sep == "\n\n" else len(sep.rstrip()))
                break
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text or not parts:
        parts.append(text)
    return parts


def shorten(text: str, limit: int) -> str:
    parts = split_text(text, limit)
    return parts[0] if len(parts) == 1 else parts[0].rstrip(" ,;:") + "…"




_svc: TelegramService | None = None
_poller_started = False
_responder = None   # fn(item) -> reply text | None   (persona brain, see telegram_agent.py)
_classifier = None  # fn(text, sender) -> {"needs_reply": bool, "kind": str} | None   (Jev)
_owner_responder = None  # fn(item) -> reply text | None   (brain persona runs the owner's command)
_options_source = None   # fn() -> {"tools": [...], "spaces": [...]}
_action_executor = None  # fn(tool, arguments) -> result dict   (runs a tool call the owner confirmed)
# Tools that may run only after the owner presses confirm in Telegram.
CONFIRMABLE_TOOLS = frozenset({"http_request", "shell_exec"})
ACTION_TTL = 24 * 3600  # an unconfirmed request expires after a day


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


def set_action_executor(fn) -> None:
    global _action_executor
    _action_executor = fn


def request_from_tool(question: str, tool: str, arguments: dict) -> dict:
    """Entry point for the ask_confirmation tool."""
    return get_services().request_action(question, tool, arguments)


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
