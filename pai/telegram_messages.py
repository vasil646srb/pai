"""Reading inbound Telegram updates: message text, forwards, media references and owner batching.

Pure functions over Telegram update dicts. Re-exported from pai.telegram for existing callers."""
from __future__ import annotations

import time


MEDIA_KINDS = ("voice", "audio", "video", "video_note")  # speech is taken from these (animations are silent)
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API getFile limit


IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})  # what vision models accept


GROUP_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # strangers' images above this are not sent to the model


def _is_image_ref(f: dict) -> bool:
    return f.get("kind") == "photo" or f.get("mime") in IMAGE_MIMES


def _is_media_ref(f: dict) -> bool:
    """A voice note, audio, or video (also a document that is audio or video by its mime type)."""
    return f.get("kind") in MEDIA_KINDS or str(f.get("mime") or "").startswith(("audio/", "video/"))


def group_body(it: dict) -> str:
    """What Jev and the persona read of a group message: its caption and the speech of its voice or video."""
    parts = [(it.get("text") or "").strip()]
    if (it.get("transcript") or "").strip():
        parts.append(f"[Расшифровка голосового или видео]: {it['transcript'].strip()}")
    return "\n".join(p for p in parts if p)


def _group_input_text(it: dict) -> str:
    """What Jev and the persona see of a group message: its text, or a note when the message is only a file."""
    text = group_body(it)
    if text:
        return text
    kinds = [k for k in (it.get("kinds") or []) if k != "rich_message"]
    if it.get("files"):
        names = {"photo": "фото", "document": "файл", "audio": "аудио", "voice": "голосовое",
                 "video": "видео", "video_note": "видеосообщение", "animation": "анимация"}
        return "(сообщение без текста: " + ", ".join(names.get(f.get("kind"), "файл") for f in it["files"]) + ")"
    return f"(сообщение без текста{': ' + ', '.join(kinds) if kinds else ''})"


def _entity_name(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    if obj.get("title"):
        return obj["title"]
    full = " ".join(x for x in (obj.get("first_name"), obj.get("last_name")) if x)
    if obj.get("username"):
        return f"{full} (@{obj['username']})" if full else f"@{obj['username']}"
    return full


def _forward_info(msg: dict) -> dict | None:
    """Who wrote a forwarded message and where it came from. Bot API 7+ forward_origin, else the older forward_* fields."""
    origin = msg.get("forward_origin")
    if isinstance(origin, dict):
        kind, date = origin.get("type"), origin.get("date")
        if kind == "user":
            return {"from": _entity_name(origin.get("sender_user")), "source": "", "date": date}
        if kind == "hidden_user":
            return {"from": origin.get("sender_user_name", ""), "source": "", "date": date}
        if kind == "chat":
            return {"from": origin.get("author_signature", ""), "source": _entity_name(origin.get("sender_chat")), "date": date}
        if kind == "channel":
            return {"from": origin.get("author_signature", ""), "source": _entity_name(origin.get("chat")), "date": date}
    if msg.get("forward_from") or msg.get("forward_from_chat") or msg.get("forward_sender_name"):
        return {
            "from": _entity_name(msg.get("forward_from")) or msg.get("forward_sender_name", "") or msg.get("forward_signature", ""),
            "source": _entity_name(msg.get("forward_from_chat")),
            "date": msg.get("forward_date"),
        }
    return None


def _media_refs(msg: dict) -> list[dict]:
    """Files of one message, including those nested in rich_message blocks. Duplicates (same file) are dropped."""
    refs = _direct_media(msg)
    if isinstance(msg.get("rich_message"), (dict, list)):
        refs += _direct_media_tree(msg["rich_message"])
    out, seen = [], set()
    for r in refs:
        if r["file_id"] not in seen:
            seen.add(r["file_id"])
            out.append(r)
    return out


def _direct_media_tree(node) -> list[dict]:
    """Walk a nested structure (rich_message blocks) and collect files at every level."""
    refs: list[dict] = []
    if isinstance(node, dict):
        refs += _direct_media(node)
        for v in node.values():
            refs += _direct_media_tree(v)
    elif isinstance(node, list):
        for x in node:
            refs += _direct_media_tree(x)
    return refs


def _direct_media(msg: dict) -> list[dict]:
    """Files at this level: photo (largest size), document, audio, voice, video, video note, animation."""
    refs = []
    if isinstance(msg.get("photo"), list) and msg["photo"]:
        best = max(msg["photo"], key=lambda p: (p.get("file_size") or 0, p.get("width", 0) * p.get("height", 0)))
        refs.append({"file_id": best["file_id"], "kind": "photo",
                     "name": f"photo_{best.get('file_unique_id', best['file_id'][:12])}.jpg",
                     "mime": "image/jpeg", "size": best.get("file_size") or 0})
    for key, default_name, default_mime in (
        ("document", "", ""), ("audio", "", ""), ("voice", "voice.ogg", "audio/ogg"),
        ("video", "video.mp4", "video/mp4"), ("video_note", "video_note.mp4", "video/mp4"),
        ("animation", "animation.mp4", "video/mp4"),
    ):
        obj = msg.get(key)
        if not isinstance(obj, dict) or not obj.get("file_id"):
            continue
        refs.append({"file_id": obj["file_id"], "kind": key,
                     "name": obj.get("file_name") or default_name or f"{key}_{obj.get('file_unique_id', '')}",
                     "mime": obj.get("mime_type") or default_mime, "size": obj.get("file_size") or 0,
                     "duration": obj.get("duration") or 0})
    return refs


_MSG_META = frozenset({
    "message_id", "from", "sender_chat", "chat", "date", "edit_date", "forward_origin", "forward_from",
    "forward_from_chat", "forward_from_message_id", "forward_signature", "forward_sender_name", "forward_date",
    "is_automatic_forward", "is_topic_message", "message_thread_id", "reply_to_message", "external_reply",
    "quote", "reply_markup", "has_protected_content", "media_group_id", "author_signature", "entities",
    "caption_entities", "link_preview_options", "via_bot", "sender_boost_count", "effect_id",
    "business_connection_id", "sender_business_bot", "is_from_offline", "show_caption_above_media",
    "has_media_spoiler", "text", "caption",
})


def _content_kinds(msg: dict) -> list[str]:
    """Names of the content fields of a message (photo, poll, contact, ...): shown to the model when there is no text."""
    return sorted(k for k in msg if k not in _MSG_META)


_TEXT_KEYS = ("text", "caption", "alternative_text")


def _rich_text(node) -> list[str]:
    """Readable strings of a rich_message tree: text/caption/alternative_text at any depth, in order."""
    out: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("content"), list):  # a paragraph: its segments are one line of text
            joined = "".join(_rich_text(node["content"]))
            if joined:
                out.append(joined)
        for k, v in node.items():
            if k == "content" and isinstance(v, list):
                continue
            if k in _TEXT_KEYS and isinstance(v, str):
                if v:
                    out.append(v)
            else:
                out += _rich_text(v)
    elif isinstance(node, list):
        for x in node:
            out += _rich_text(x)
    return out


def _shape(node, depth: int = 0):
    """Key skeleton without values (no ids, no text): for logs when a payload is unfamiliar."""
    if depth > 6:
        return "…"
    if isinstance(node, dict):
        return {k: _shape(v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_shape(node[0], depth + 1)] if node else []
    return type(node).__name__


def _msg_text(msg: dict) -> str:
    """Text of a message: its text or caption, the text of rich_message blocks, or the readable text of a poll, contact, venue or location."""
    if msg.get("text") or msg.get("caption"):
        return msg.get("text") or msg.get("caption")
    if msg.get("rich_message"):
        rich = "\n".join(t.strip() for t in _rich_text(msg["rich_message"]) if t.strip())
        if rich:
            return rich
    if isinstance(msg.get("poll"), dict):
        p = msg["poll"]
        opts = "\n".join(f"- {o.get('text', '')}" for o in p.get("options", []) if isinstance(o, dict))
        return f"Опрос: {p.get('question', '')}\n{opts}".strip()
    if isinstance(msg.get("contact"), dict):
        c = msg["contact"]
        name = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
        return f"Контакт: {name} {c.get('phone_number', '')}".strip()
    if isinstance(msg.get("venue"), dict):
        v = msg["venue"]
        return f"Место: {v.get('title', '')}, {v.get('address', '')}".strip(", ")
    if isinstance(msg.get("location"), dict):
        loc = msg["location"]
        return f"Геолокация: {loc.get('latitude')}, {loc.get('longitude')}"
    return ""


def _forward_header(f: dict) -> str:
    who = f.get("from") or "неизвестный автор"
    src = f" из «{f['source']}»" if f.get("source") else ""
    when = f", {time.strftime('%Y-%m-%d %H:%M', time.localtime(int(f['date'])))}" if f.get("date") else ""
    return f"[Переслано от {who}{src}{when}]"


def _owner_text(m: dict) -> str:
    body = (m.get("text") or "").strip()
    if m.get("forward"):
        if not body:  # Telegram sent no text: say so, so the model does not answer as if it saw one
            kinds = ", ".join(m.get("kinds") or []) or "неизвестный тип"
            body = f"(в пересланном сообщении нет текста; содержимое: {kinds})"
        body = f"{_forward_header(m['forward'])}\n{body}".strip()
    return body


def _owner_batches(items: list[dict], limit: int) -> list[list[dict]]:
    """Unanswered owner messages, one batch each. Messages of one album (media_group_id) form one batch."""
    batches: list[list[dict]] = []
    albums: dict[str, list[dict]] = {}
    for it in items:
        if it.get("channel") != "owner" or it.get("analyzed"):
            continue
        g = str(it.get("media_group") or "")
        if g:
            if g not in albums:
                albums[g] = []
                batches.append(albums[g])
            albums[g].append(it)
        else:
            batches.append([it])
    return batches[:limit]


def _source_message_id(item: dict) -> int | None:
    """Inbox ids are '<chat_id>:<message_id>'."""
    try:
        return int(str(item.get("id", "")).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
