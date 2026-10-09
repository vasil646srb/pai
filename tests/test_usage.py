from pai.usage import UsageTracker, estimate_tokens


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_nonzero_for_short_text():
    assert estimate_tokens("hi") >= 1


def test_estimate_tokens_scales_with_length():
    short = estimate_tokens("a" * 40)
    long = estimate_tokens("a" * 400)
    assert long > short


def test_usage_tracker_record_and_summary():
    tracker = UsageTracker()
    tracker.record("chat", "input text " * 10, "output text", 1, 0.5)
    summary = tracker.summary()
    assert summary["total_calls"] == 1
    assert summary["total_input_tokens"] > 0
    assert summary["calls_by_kind"]["chat"] == 1
    assert summary["total_cost_usd"] > 0


def test_usage_tracker_multiple_kinds():
    tracker = UsageTracker()
    tracker.record("chat", "x", "y", 0, 0.1)
    tracker.record("background_summary", "x" * 100, "", 0, 1.0)
    summary = tracker.summary()
    assert summary["calls_by_kind"]["chat"] == 1
    assert summary["calls_by_kind"]["background_summary"] == 1


def test_usage_tracker_avg_latency():
    tracker = UsageTracker()
    tracker.record("chat", "x", "y", 0, 1.0)
    tracker.record("chat", "x", "y", 0, 3.0)
    summary = tracker.summary()
    assert summary["avg_latency_seconds"] == 2.0
