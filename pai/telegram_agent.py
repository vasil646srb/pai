"""Telegram group agent: Jev classifies a new group message, the brain persona writes the reply.

classify(): Jev asks two questions (needs a reply? what kind?). Returns None when Jev is unavailable,
so the message is retried later instead of being answered blindly.
make_responder(orch): calls the main model through the orchestrator with persona 'brain' and a
separate space, so group traffic does not mix into the personal chat.
"""
from __future__ import annotations

from .telegram import HeldReply  # noqa: E402

GROUP_SPACE = "telegram-group"
MIN_CONFIDENCE = 0.6


def get_laya():
    """Lazy: Telegram works without the Laya/Jev integration; classification then reports 'unavailable'."""
    try:
        from .laya import get_laya as _get
    except ImportError:
        return None
    return _get()


def classify(text: str, sender: str = "") -> dict | None:
    lay = get_laya()
    if lay is None or not lay.enabled or not (text or "").strip():
        return None
    r = lay.ask(f"Group chat message from {sender or 'a member'}:\n{text[:2000]}", {
        "needs_reply": ("Does this message need a reply from the assistant: a question, a request, "
                        "or a message addressed to the assistant? Answer yes or no.", ["yes", "no"]),
        "kind": ("Classify the message.", ["question", "request", "chat", "spam", "other"]),
    })
    if not r or "needs_reply" not in r or "kind" not in r:
        return None
    nr = r["needs_reply"]
    return {"needs_reply": nr.label == "yes" and nr.p >= MIN_CONFIDENCE, "kind": r["kind"].label}


def make_responder(orch, space_id: str = GROUP_SPACE):
    """Runs a brain turn and returns its reply ONLY if the answer check passed on the final text.
    Anything else raises HeldReply: nothing is sent, the draft is kept in the inbox for review."""
    def respond(item: dict) -> str | None:
        prompt = (
            "Новое сообщение в нашей группе Telegram.\n"
            f"От: {item.get('from') or 'участник'}\n"
            f"Текст:\n{item.get('text', '')}\n\n"
            "Напиши ответ для группы: коротко, до трёх предложений, на языке сообщения. "
            "Не раскрывай личные данные владельца и заметки. Если не знаешь ответа, так и скажи."
        )
        reply, checks = "", []
        for ev in orch.handle_turn_stream(prompt, persona_id="brain", space_id=space_id):
            if ev.get("type") == "done":
                reply = (ev.get("reply") or "").strip()
                checks = ev.get("verification") or []
        if not reply:
            return None
        last = checks[-1] if checks else None
        if last is None:
            raise HeldReply("проверка ответа не запущена (нужна проверочная модель)", draft=reply)
        if last.get("status") != "pass":
            why = last.get("report") or last.get("status") or "не прошёл проверку"
            raise HeldReply(f"ответ не прошёл проверку: {why}", draft=reply)
        return reply
    return respond
