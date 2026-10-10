"""Small helpers shared by the web layer."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException


def iso_to_ts(s: str | None, end_of_day: bool = False) -> float | None:
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Invalid ISO date: {s!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    if end_of_day and len(s) <= 10:  # bare date as upper bound = through end of that day
        ts += 86399.999
    return ts


def ensure_pai_logging() -> None:
    """Show pai.* INFO messages (e.g. answer checks) on the console. Idempotent."""
    import logging
    log = logging.getLogger("pai")
    if log.level == logging.NOTSET or log.level > logging.INFO:
        log.setLevel(logging.INFO)
    if not any(getattr(h, "_pai", False) for h in log.handlers):
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h._pai = True
        log.addHandler(h)
