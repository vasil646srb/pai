"""Registry of running chat turns, so a turn can be stopped from another request.

The streaming endpoint registers a turn under the client-chosen turn_id; POST /chat/stop
sets its event. The dialog loop checks the event before every model chunk and ends the
turn with a `cancelled` event. A cancelled turn stores no assistant reply."""
from __future__ import annotations

import threading


class TurnCancelled(Exception):
    """Raised inside the dialog loop when the user stopped the turn."""


class TurnRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}

    def open(self, turn_id: str | None) -> threading.Event:
        event = threading.Event()
        if turn_id:
            with self._lock:
                self._events[turn_id] = event
        return event

    def cancel(self, turn_id: str) -> bool:
        with self._lock:
            event = self._events.get(turn_id)
        if event is None:
            return False
        event.set()
        return True

    def close(self, turn_id: str | None) -> None:
        if turn_id:
            with self._lock:
                self._events.pop(turn_id, None)


TURNS = TurnRegistry()
