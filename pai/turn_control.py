"""Cancellation of in-flight chat turns, keyed by a client-chosen turn_id.

/chat/stop may arrive before /chat/stream has registered its turn (race between the two requests),
so cancel() creates the flag if it is missing and begin() picks it up. Unused flags are capped.
"""

from __future__ import annotations

import threading

_MAX_FLAGS = 256
_lock = threading.Lock()
_events: dict[str, threading.Event] = {}


def _event(turn_id: str) -> threading.Event:
    ev = _events.get(turn_id)
    if ev is None:
        if len(_events) >= _MAX_FLAGS:
            _events.pop(next(iter(_events)))
        ev = _events[turn_id] = threading.Event()
    return ev


def begin(turn_id: str) -> threading.Event:
    with _lock:
        return _event(turn_id)


def cancel(turn_id: str) -> None:
    with _lock:
        _event(turn_id).set()


def end(turn_id: str) -> None:
    with _lock:
        _events.pop(turn_id, None)
