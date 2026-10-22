"""Прогресс индексации: сколько фрагментов всего, сколько обработано, скорость и оценка оставшегося времени.

Скорость считается по последним WINDOW_SEC секундам, чтобы не тянуть среднее от начала очень долгой работы.
"""
from __future__ import annotations

import threading
import time
from collections import deque

WINDOW_SEC = 60.0


class IndexProgress:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self.state = "idle"
        self.total = 0
        self.done = 0
        self._started = None
        self._events: deque = deque()  # (time, count) — обработанные за окно

    def start(self, total: int) -> None:
        with self._lock:
            self.state = "running"
            self.total = max(0, int(total))
            self.done = 0
            self._started = self._clock()
            self._events.clear()

    def add_total(self, n: int) -> None:
        with self._lock:
            if n > 0:
                self.state = "running"
                if self._started is None:
                    self._started = self._clock()
                self.total += int(n)

    def add_done(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            now = self._clock()
            self.done += int(n)
            self._events.append((now, int(n)))
            self._prune(now)

    def finish(self) -> None:
        with self._lock:
            self.state = "idle"

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] > WINDOW_SEC:
            self._events.popleft()

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            self._prune(now)
            window_done = sum(n for _, n in self._events)
            elapsed = min(WINDOW_SEC, now - self._started) if self._started is not None else 0.0
            rate = window_done / elapsed if elapsed > 0 else 0.0
            done = min(self.done, self.total) if self.total else self.done
            remaining = max(0, self.total - self.done)
            eta = round(remaining / rate) if rate > 0 and remaining > 0 else (0 if remaining == 0 and self.total else None)
            percent = min(100, round(self.done * 100 / self.total)) if self.total else 0
            return {
                "state": self.state,
                "total": self.total,
                "done": done,
                "remaining": remaining,
                "percent": percent,
                "rate_per_sec": round(rate, 2),
                "eta_sec": eta,
            }
