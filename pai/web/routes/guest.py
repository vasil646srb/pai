"""Public visitor endpoints (no owner password) and the owner's inbox (owner password).

/guest                        visitor page
/guest/common                 shared chat for all visitors (space 'guest')
/guest/common/messages        its history
/guest/rooms                  list of private rooms (names only); POST creates one (name + password)
/guest/rooms/{id}/unlock      password -> room token (header X-Room-Token)
/guest/rooms/{id}/messages    room history (needs token)
/guest/rooms/{id}/chat        chat in a room (needs token); memory space 'guest-room:<id>' only
/guest/inbox                  message to the owner
/inbox                        owner: list / read / delete (behind the auth gate)
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ...models import MemoryFilter
from ...storage import Storage
from ..deps import get_storage
from ..guest_limits import DAY, HOUR, GuestLimitError, RateLimiter
from ..ui import STATIC_DIR

_log = logging.getLogger(__name__)

GUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ROOM_ID_RE = re.compile(r"^[a-f0-9]{32}$")
COMMON_SPACE = "guest"
MAX_TEXT = 2000
MAX_IMAGES = 3
MAX_IMAGE_CHARS = 6_000_000  # base64 data URL, about 4.5 MB of image
INBOX_TEXT = 1000
ROOM_TTL = 30 * 24 * 3600
ROOM_PASSWORD_MIN = 6
ROOM_NAME_MAX = 40


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


class GuestChatRequest(BaseModel):
    guest_id: str = Field(default="", max_length=64)
    text: str = Field(default="", max_length=MAX_TEXT)
    images: list[str] = Field(default_factory=list, max_length=MAX_IMAGES)


class RoomCreateRequest(BaseModel):
    guest_id: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=ROOM_NAME_MAX)
    password: str = Field(default="", max_length=128)


class RoomUnlockRequest(BaseModel):
    password: str = Field(default="", max_length=128)


class InboxRequest(BaseModel):
    guest_id: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=60)
    text: str = Field(default="", max_length=INBOX_TEXT)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_guest_id(guest_id: str) -> str:
    if not GUEST_ID_RE.match(guest_id or ""):
        raise HTTPException(400, "Некорректный идентификатор посетителя")
    return guest_id


def _history(storage: Storage, space_id: str) -> list[dict]:
    out = []
    # the store returns newest first; the page shows oldest first
    for n in reversed(storage.get_chat_messages(space_id=space_id, limit=60)):
        tags = n.tags or []
        if "service" in tags or "notice" in tags:
            continue
        out.append({"role": "bot" if "assistant" in tags else "me", "text": n.raw_content})
    return out


# Human-readable labels for the guest persona's tools (shown while the assistant works).
TOOL_LABELS = {
    "web_search": "Ищу в интернете…",
    "web_fetch": "Открываю страницу…",
    "get_fragment": "Смотрю заметки…",
    "search_archive": "Ищу в архиве…",
}
NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "X-Accel-Buffering": "no"}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


def register(app: FastAPI) -> None:
    per_hour = _int_env("PAI_GUEST_PER_HOUR", 20)
    per_day = _int_env("PAI_GUEST_PER_DAY", 100)
    global_day = _int_env("PAI_GUEST_GLOBAL_PER_DAY", 500)   # cost cap for the whole site
    inbox_hour = _int_env("PAI_GUEST_INBOX_PER_HOUR", 5)
    inbox_day = _int_env("PAI_GUEST_INBOX_PER_DAY", 15)
    room_create_hour = _int_env("PAI_GUEST_ROOMS_PER_HOUR", 3)
    limiter = RateLimiter()
    inbox_limiter = RateLimiter()
    fails = RateLimiter()  # wrong room passwords; checked without recording, recorded on failure

    def visitor_rules(ip: str, gid: str):
        return [
            (f"ip:{ip}", HOUR, per_hour, "Слишком много сообщений с этого адреса. Попробуйте через час."),
            (f"ip:{ip}", DAY, per_day, "Дневной лимит сообщений исчерпан. Приходите завтра."),
            (f"guest:{gid}", HOUR, per_hour, "Слишком много сообщений. Попробуйте через час."),
            (f"guest:{gid}", DAY, per_day, "Дневной лимит сообщений исчерпан. Приходите завтра."),
            ("global", DAY, global_day, "Сегодня лимит гостевых сообщений на сайте исчерпан. Приходите завтра."),
        ]

    def run_turn(request: Request, gid: str, text: str, images, persona_id: str, space_id: str, stream: bool = False):
        ip = _client_ip(request)
        if not text.strip() and not images:
            raise HTTPException(400, "Пустое сообщение")
        for img in images:
            if not img.startswith("data:image/") or len(img) > MAX_IMAGE_CHARS:
                raise HTTPException(400, "Картинка должна быть изображением до 4.5 МБ")
        try:
            limiter.take(visitor_rules(ip, gid))
        except GuestLimitError as e:
            raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after)}) from e
        orch = app.state.orchestrator
        if stream:
            return stream_turn(orch, gid, text, images, persona_id, space_id)
        try:
            result = orch.handle_turn(text, persona_id=persona_id, space_id=space_id, images=images or None)
        except Exception:
            _log.exception("guest chat failed")
            raise HTTPException(503, "Помощник временно недоступен. Попробуйте позже.")
        return {
            "reply": result.reply,
            "remaining_hour": limiter.remaining(f"guest:{gid}", HOUR, per_hour),
            "remaining_day": limiter.remaining(f"guest:{gid}", DAY, per_day),
        }

    def remaining(gid: str) -> dict:
        return {
            "remaining_hour": limiter.remaining(f"guest:{gid}", HOUR, per_hour),
            "remaining_day": limiter.remaining(f"guest:{gid}", DAY, per_day),
        }

    def stream_turn(orch, gid: str, text: str, images, persona_id: str, space_id: str):
        """SSE: status events while the assistant works, then one done event with the reply."""
        def gen():
            final = None
            announced: set[str] = set()
            try:
                for ev in orch.handle_turn_stream(text, persona_id=persona_id, space_id=space_id,
                                                  images=images or None):
                    kind = ev.get("type")
                    if kind == "tool_call":
                        yield _sse({"type": "status", "text": TOOL_LABELS.get(ev.get("name"), "Использую инструмент…")})
                    elif kind == "reasoning_delta" and "think" not in announced:
                        announced.add("think")
                        yield _sse({"type": "status", "text": "Думаю…"})
                    elif kind == "delta" and "write" not in announced:
                        announced.add("write")
                        yield _sse({"type": "status", "text": "Пишу ответ…"})
                    elif kind in ("check_failed", "delta_reset"):
                        announced.discard("write")
                        if kind == "check_failed":
                            yield _sse({"type": "status", "text": "Ответ не прошёл проверку, исправляю…"})
                    elif kind == "done":
                        final = ev
            except Exception:
                _log.exception("guest stream failed")
                yield _sse({"type": "error", "message": "Помощник временно недоступен. Попробуйте позже."})
                return
            if final is None:
                yield _sse({"type": "error", "message": "Помощник не ответил. Попробуйте позже."})
                return
            yield _sse({"type": "done", "reply": final.get("reply") or "", **remaining(gid)})

        return StreamingResponse(gen(), media_type="text/event-stream", headers=NO_CACHE)

    @app.get("/guest")
    def guest_page():
        return FileResponse(STATIC_DIR / "guest.html", headers={"Cache-Control": "no-store"})

    # --- shared chat
    @app.get("/guest/common/messages")
    def common_messages(storage: Storage = Depends(get_storage)):
        return {"messages": _history(storage, COMMON_SPACE)}

    @app.post("/guest/chat")
    def guest_chat(req: GuestChatRequest, request: Request):
        gid = _check_guest_id(req.guest_id)
        return run_turn(request, gid, req.text, req.images, "guest", COMMON_SPACE)

    @app.post("/guest/chat/stream")
    def guest_chat_stream(req: GuestChatRequest, request: Request):
        gid = _check_guest_id(req.guest_id)
        return run_turn(request, gid, req.text, req.images, "guest", COMMON_SPACE, stream=True)

    # --- private rooms
    @app.get("/guest/rooms")
    def list_rooms(storage: Storage = Depends(get_storage)):
        return {"rooms": storage.list_guest_rooms()}

    @app.post("/guest/rooms")
    def create_room(req: RoomCreateRequest, request: Request, storage: Storage = Depends(get_storage)):
        gid = _check_guest_id(req.guest_id)
        name = req.name.strip()
        if not name or any(ord(ch) < 32 for ch in name):
            raise HTTPException(400, "Придумайте название комнаты")
        if len(req.password) < ROOM_PASSWORD_MIN:
            raise HTTPException(400, f"Пароль не короче {ROOM_PASSWORD_MIN} символов")
        ip = _client_ip(request)
        try:
            inbox_limiter.take([(f"room-create:{ip}", HOUR, room_create_hour, "Слишком много комнат. Попробуйте позже.")])
        except GuestLimitError as e:
            raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after)}) from e
        room = storage.create_guest_room(name, req.password)
        token = storage.unlock_guest_room(room["id"], req.password, ROOM_TTL)
        return {**room, "token": token, "guest_id": gid}

    @app.post("/guest/rooms/{room_id}/unlock")
    def unlock_room(room_id: str, req: RoomUnlockRequest, request: Request, storage: Storage = Depends(get_storage)):
        if not ROOM_ID_RE.match(room_id) or not storage.guest_room_exists(room_id):
            raise HTTPException(404, "Комната не найдена")
        ip = _client_ip(request)
        msg = "Слишком много неверных паролей. Подождите 15 минут."
        try:
            fails.check(f"room-fail:{room_id}", 900, 10, msg)
            fails.check(f"room-fail-ip:{ip}", 900, 30, msg)
        except GuestLimitError as e:
            raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after)}) from e
        token = storage.unlock_guest_room(room_id, req.password, ROOM_TTL)
        if token is None:
            fails.hit(f"room-fail:{room_id}")
            fails.hit(f"room-fail-ip:{ip}")
            raise HTTPException(401, "Неверный пароль")
        return {"token": token}

    def require_room(room_id: str, token: str | None, storage: Storage) -> None:
        if not ROOM_ID_RE.match(room_id) or storage.guest_room_for_token(token) != room_id:
            raise HTTPException(401, "Нужен пароль комнаты")

    @app.get("/guest/rooms/{room_id}/messages")
    def room_messages(room_id: str, x_room_token: str | None = Header(default=None),
                      storage: Storage = Depends(get_storage)):
        require_room(room_id, x_room_token, storage)
        return {"messages": _history(storage, f"guest-room:{room_id}")}

    def _room_chat(room_id: str, req: GuestChatRequest, request: Request, x_room_token, storage, stream: bool):
        require_room(room_id, x_room_token, storage)
        gid = _check_guest_id(req.guest_id)
        orch = app.state.orchestrator
        base = orch.personas.get("guest")
        if base is None:
            raise HTTPException(503, "Персона гостя не настроена")
        # One memory-only persona per room: it can see its own room, never another one.
        room_persona = dataclasses.replace(
            base,
            persona_id=f"guest-room-{room_id}",
            name="Гость (комната)",
            memory_filter=MemoryFilter(
                allow_spaces=["guest", "shared", f"guest-room:{room_id}"], deny_tags=["diary"],
            ),
        )
        orch.personas.ephemeral(room_persona)
        return run_turn(request, gid, req.text, req.images, room_persona.persona_id, f"guest-room:{room_id}",
                        stream=stream)

    @app.post("/guest/rooms/{room_id}/chat")
    def room_chat(room_id: str, req: GuestChatRequest, request: Request,
                  x_room_token: str | None = Header(default=None), storage: Storage = Depends(get_storage)):
        return _room_chat(room_id, req, request, x_room_token, storage, stream=False)

    @app.post("/guest/rooms/{room_id}/chat/stream")
    def room_chat_stream(room_id: str, req: GuestChatRequest, request: Request,
                         x_room_token: str | None = Header(default=None), storage: Storage = Depends(get_storage)):
        return _room_chat(room_id, req, request, x_room_token, storage, stream=True)

    # --- message to the owner
    @app.post("/guest/inbox")
    def guest_inbox(req: InboxRequest, request: Request, storage: Storage = Depends(get_storage)):
        gid = _check_guest_id(req.guest_id)
        if not req.text.strip():
            raise HTTPException(400, "Напишите сообщение")
        ip = _client_ip(request)
        try:
            inbox_limiter.take([
                (f"ip:{ip}", HOUR, inbox_hour, "Слишком много сообщений. Попробуйте позже."),
                (f"ip:{ip}", DAY, inbox_day, "Дневной лимит сообщений исчерпан."),
            ])
        except GuestLimitError as e:
            raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after)}) from e
        storage.add_inbox(gid, req.name.strip()[:60], req.text.strip()[:INBOX_TEXT])
        return {"ok": True}

    # --- owner side (behind the auth gate)
    @app.get("/inbox")
    def list_inbox(unread: bool = False, storage: Storage = Depends(get_storage)):
        return storage.list_inbox(unread_only=unread)

    @app.post("/inbox/{message_id}/read")
    def mark_read(message_id: str, read: bool = True, storage: Storage = Depends(get_storage)):
        if not storage.set_inbox_read(message_id, read):
            raise HTTPException(404, "Сообщение не найдено")
        return {"ok": True}

    @app.delete("/inbox/{message_id}")
    def delete_message(message_id: str, storage: Storage = Depends(get_storage)):
        if not storage.delete_inbox(message_id):
            raise HTTPException(404, "Сообщение не найдено")
        return {"ok": True}
