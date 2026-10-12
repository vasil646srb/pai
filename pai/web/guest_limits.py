"""In-memory sliding-window limits for public (guest) endpoints. A restart clears the counters."""

from __future__ import annotations

import threading
import time
from collections import deque

HOUR = 3600
DAY = 24 * 3600


class GuestLimitError(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def _window(self, key: str, now: float) -> deque[float]:
        dq = self._hits.setdefault(key, deque())
        while dq and now - dq[0] >= DAY:  # nothing older than a day is ever needed
            dq.popleft()
        return dq

    def take(self, rules: list[tuple[str, int, int, str]]) -> None:
        """rules: (key, window_seconds, limit, message). Records one hit per key if every rule passes."""
        with self._lock:
            now = self._clock()
            for key, window, limit, message in rules:
                dq = self._window(key, now)
                inside = [t for t in dq if now - t < window]
                if len(inside) >= limit:
                    raise GuestLimitError(message, retry_after=int(window - (now - inside[0])) + 1)
            for key in {r[0] for r in rules}:
                self._window(key, now).append(now)

    def check(self, key: str, window: int, limit: int, message: str) -> None:
        """Raise if `key` already has `limit` hits inside `window`. Does not record anything."""
        with self._lock:
            now = self._clock()
            inside = [t for t in self._window(key, now) if now - t < window]
            if len(inside) >= limit:
                raise GuestLimitError(message, retry_after=int(window - (now - inside[0])) + 1)

    def hit(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._window(key, now).append(now)

    def remaining(self, key: str, window: int, limit: int) -> int:
        with self._lock:
            now = self._clock()
            dq = self._window(key, now)
            return max(0, limit - sum(1 for t in dq if now - t < window))
