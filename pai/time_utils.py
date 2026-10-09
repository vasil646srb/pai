"""Time-awareness utilities: current-time context header, relative-date
normalization, and the calculate_time_delta tool."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class TimeContext:
    now: datetime
    timezone_name: str = "UTC"
    locale: str = "ru-RU"

    def header(self, persona_name: str = "", tone: str = "") -> str:
        lines = [
            f"Current Date and Time: {self.now.strftime('%Y-%m-%d %H:%M')} {self.timezone_name}",
            f"Timezone: {self.timezone_name}",
            f"Locale: {self.locale}",
        ]
        if persona_name:
            lines.append(f"Active Persona: {persona_name}")
        if tone:
            lines.append(f"Persona tone: {tone}")
        return "\n".join(lines)


_RELATIVE_PATTERNS_RU = [
    (re.compile(r"\bсегодня\b", re.IGNORECASE), 0),
    (re.compile(r"\bвчера\b", re.IGNORECASE), -1),
    (re.compile(r"\bпозавчера\b", re.IGNORECASE), -2),
    (re.compile(r"\bзавтра\b", re.IGNORECASE), 1),
    (re.compile(r"\bпослезавтра\b", re.IGNORECASE), 2),
]

_WEEK_PATTERN_RU = re.compile(r"\bна\s+прошлой\s+неделе\b", re.IGNORECASE)
_BARE_WEEK_AGO_PATTERN_RU = re.compile(r"\bнеделю\s+назад\b", re.IGNORECASE)
_MONTH_AGO_PATTERN_RU = re.compile(r"\b(\d+)\s+месяц(?:а|ев)?\s+назад\b", re.IGNORECASE)
_WEEK_AGO_PATTERN_RU = re.compile(r"\b(\d+)\s+недел(?:ю|и|ь)?\s+назад\b", re.IGNORECASE)
_DAY_AGO_PATTERN_RU = re.compile(r"\b(\d+)\s+дн(?:я|ей|ь)?\s+назад\b", re.IGNORECASE)

_RELATIVE_PATTERNS_EN = [
    (re.compile(r"\btoday\b", re.IGNORECASE), 0),
    (re.compile(r"\byesterday\b", re.IGNORECASE), -1),
    (re.compile(r"\btomorrow\b", re.IGNORECASE), 1),
]
_DAYS_AGO_EN = re.compile(r"\b(\d+)\s+days?\s+ago\b", re.IGNORECASE)
_WEEKS_AGO_EN = re.compile(r"\b(\d+)\s+weeks?\s+ago\b", re.IGNORECASE)
_MONTHS_AGO_EN = re.compile(r"\b(\d+)\s+months?\s+ago\b", re.IGNORECASE)


@dataclass
class ResolvedDate:
    raw_phrase: str
    resolved_date: Optional[str]  # ISO date, or None if not resolvable
    confidence: str  # low | medium | high


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                       31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.replace(year=year, month=month, day=day)


def normalize_relative_dates(text: str, reference: datetime) -> list[ResolvedDate]:
    """Scan text for relative date phrases and resolve them against `reference`.
    Never mutates the original text — annotations are returned separately, per spec."""
    results: list[ResolvedDate] = []

    for pattern, delta_days in _RELATIVE_PATTERNS_RU + _RELATIVE_PATTERNS_EN:
        for m in pattern.finditer(text):
            resolved = (reference + timedelta(days=delta_days)).date().isoformat()
            results.append(ResolvedDate(raw_phrase=m.group(0), resolved_date=resolved, confidence="high"))

    for m in _WEEK_PATTERN_RU.finditer(text):
        resolved = (reference - timedelta(days=7)).date().isoformat()
        results.append(ResolvedDate(raw_phrase=m.group(0), resolved_date=resolved, confidence="medium"))

    for m in _BARE_WEEK_AGO_PATTERN_RU.finditer(text):
        resolved = (reference - timedelta(days=7)).date().isoformat()
        results.append(ResolvedDate(raw_phrase=m.group(0), resolved_date=resolved, confidence="high"))

    for pattern, unit_days in [(_DAY_AGO_PATTERN_RU, 1), (_WEEK_AGO_PATTERN_RU, 7)]:
        for m in pattern.finditer(text):
            n = int(m.group(1))
            resolved = (reference - timedelta(days=n * unit_days)).date().isoformat()
            results.append(ResolvedDate(raw_phrase=m.group(0), resolved_date=resolved, confidence="high"))

    for m in _MONTH_AGO_PATTERN_RU.finditer(text):
        n = int(m.group(1))
        resolved = _add_months(reference, -n).date().isoformat()
        results.append(ResolvedDate(raw_phrase=m.group(0), resolved_date=resolved, confidence="medium"))

    for pattern, unit_days in [(_DAYS_AGO_EN, 1), (_WEEKS_AGO_EN, 7)]:
        for m in pattern.finditer(text):
            n = int(m.group(1))
            resolved = (reference - timedelta(days=n * unit_days)).date().isoformat()
            results.append(ResolvedDate(raw_phrase=m.group(0), resolved_date=resolved, confidence="high"))

    for m in _MONTHS_AGO_EN.finditer(text):
        n = int(m.group(1))
        resolved = _add_months(reference, -n).date().isoformat()
        results.append(ResolvedDate(raw_phrase=m.group(0), resolved_date=resolved, confidence="medium"))

    return results


def calculate_time_delta(date1: str, date2: str, reference_now: Optional[datetime] = None) -> dict:
    """Tool: compute the delta between two dates/timestamps.
    Accepts ISO date/datetime strings, or the literal 'now'."""
    ref = reference_now or datetime.now(timezone.utc)

    def _parse(s: str) -> datetime:
        if s.strip().lower() == "now":
            return ref
        s = s.strip()
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as e:
            raise ValueError(f"Unparseable date: {s!r}") from e
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ref.tzinfo or timezone.utc)
        return dt

    d1 = _parse(date1)
    d2 = _parse(date2)
    delta = d2 - d1
    # timedelta.days floors toward -inf (-1.5 days -> -2); use the absolute
    # delta so negative spans are symmetrical with positive ones.
    abs_days = abs(delta).days
    sign = -1 if delta.total_seconds() < 0 else 1
    total_days = sign * abs_days
    months = abs_days // 30
    remaining_days = abs_days % 30
    weeks = abs_days // 7

    parts = []
    if months:
        parts.append(f"{months} mo.")
    if remaining_days or not months:
        parts.append(f"{remaining_days if months else abs_days} d.")
    human = " and ".join(parts) if months else f"{abs_days} d."
    if sign < 0:
        human = f"in {human}"

    return {
        "days": total_days,
        "weeks": sign * weeks,
        "months": sign * months,
        "human_readable": human,
    }
