from datetime import datetime, timezone

import pytest

from pai.time_utils import TimeContext, calculate_time_delta, normalize_relative_dates


def test_time_context_header_contains_fields():
    ctx = TimeContext(now=datetime(2026, 8, 30, 14, 35, tzinfo=timezone.utc), timezone_name="UTC+3")
    header = ctx.header(persona_name="Друг")
    assert "2026-08-30 14:35" in header
    assert "UTC+3" in header
    assert "Друг" in header


def test_normalize_yesterday_ru():
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    results = normalize_relative_dates("что было вчера?", ref)
    assert len(results) == 1
    assert results[0].resolved_date == "2026-08-29"
    assert results[0].confidence == "high"


def test_normalize_does_not_mutate_original_text():
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    text = "вчера я обсуждал план"
    normalize_relative_dates(text, ref)
    assert text == "вчера я обсуждал план"  # unchanged


def test_normalize_months_ago_ru():
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    results = normalize_relative_dates("2 месяца назад мы начали проект", ref)
    assert len(results) == 1
    assert results[0].resolved_date == "2026-06-30"


def test_normalize_weeks_ago_ru():
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    results = normalize_relative_dates("неделю назад", ref)
    assert results[0].resolved_date == "2026-08-23"


def test_normalize_multiple_phrases():
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    results = normalize_relative_dates("вчера и позавчера", ref)
    resolved = {r.resolved_date for r in results}
    assert "2026-08-29" in resolved
    assert "2026-08-28" in resolved


def test_normalize_english_days_ago():
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    results = normalize_relative_dates("we discussed this 5 days ago", ref)
    assert results[0].resolved_date == "2026-08-25"


def test_normalize_no_match_returns_empty():
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    assert normalize_relative_dates("просто обычный текст без дат", ref) == []


def test_calculate_time_delta_basic():
    result = calculate_time_delta("2026-07-15", "2026-08-30")
    assert result["days"] == 46
    assert "1 mo" in result["human_readable"]


def test_calculate_time_delta_with_now():
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    result = calculate_time_delta("2026-07-15", "now", reference_now=ref)
    assert result["days"] == 46


def test_calculate_time_delta_negative_future():
    result = calculate_time_delta("2026-08-30", "2026-09-05")
    assert result["days"] == 6
    assert result["weeks"] >= 0


def test_calculate_time_delta_reverse_order_is_negative():
    result = calculate_time_delta("2026-09-05", "2026-08-30")
    assert result["days"] == -6
    assert "in " in result["human_readable"]


def test_calculate_time_delta_bad_date_raises():
    with pytest.raises(ValueError):
        calculate_time_delta("not-a-date", "now")
