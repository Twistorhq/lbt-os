"""TW-622 Twistor Telemetry — pure query engine (TDD RED)."""

from datetime import datetime, timedelta, timezone

from app.telemetry import query

UTC = timezone.utc


def _ev(user, event, day_offset, hour=12, props=None):
    return {
        "event": event,
        "distinct_id_hash": f"h-{user}",
        "occurred_at": datetime(2026, 10, 1, hour, tzinfo=UTC)
        + timedelta(days=day_offset),
        "properties": props or {},
    }


def test_trend_day_buckets_zero_filled():
    events = [
        _ev("u1", "signup", 0),
        _ev("u2", "signup", 0),
        _ev("u3", "signup", 2),
        _ev("u4", "other", 1),
    ]
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 4, tzinfo=UTC)
    rows = query.trend(events, "signup", start, end, "day")
    assert [r["count"] for r in rows] == [2, 0, 1]
    assert rows[0]["bucket_start"] == "2026-10-01T00:00:00+00:00"


def test_trend_hour_bucket_alignment():
    events = [_ev("u1", "click", 0, hour=9), _ev("u2", "click", 0, hour=9)]
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    end = datetime(2026, 10, 1, 11, tzinfo=UTC)
    rows = query.trend(events, "click", start, end, "hour")
    assert [r["count"] for r in rows] == [0, 2, 0]


def test_trend_ignores_out_of_range():
    events = [_ev("u1", "x", -5), _ev("u2", "x", 10)]
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    rows = query.trend(events, "x", start, end, "day")
    assert rows[0]["count"] == 0


def test_funnel_ordered_conversion():
    events = []
    for i in range(100):
        u = f"u{i}"
        events.append(_ev(u, "sent", 0))
        if i < 60:
            events.append(_ev(u, "opened", 0, hour=13))
        if i < 30:
            events.append(_ev(u, "replied", 0, hour=14))
    # out-of-order user: opened before sent must not count for opened
    events.append(_ev("ooo", "opened", 0, hour=10))
    events.append(_ev("ooo", "sent", 0, hour=11))
    # late user: opened after the window must not count
    events.append(_ev("late", "sent", 0, hour=12))
    events.append(_ev("late", "opened", 5, hour=12))
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 8, tzinfo=UTC)
    res = query.funnel(
        events, ["sent", "opened", "replied"], timedelta(hours=72), start, end
    )
    counts = [s["count"] for s in res["steps"]]
    assert counts == [102, 60, 30]
    assert res["conversion"] == [1.0, 60 / 102, 30 / 102]


def test_funnel_empty():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    res = query.funnel([], ["a", "b"], timedelta(hours=24), start, end)
    assert [s["count"] for s in res["steps"]] == [0, 0]
    assert res["conversion"] == [0.0, 0.0]


def test_retention_cohort():
    events = []
    for i in range(10):
        u = f"u{i}"
        events.append(_ev(u, "signup", 0))
        if i < 5:
            events.append(_ev(u, "active", 1))
        if i < 3:
            events.append(_ev(u, "active", 2))
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    res = query.retention(events, "signup", "active", 2, start, end)
    assert res["cohort_size"] == 10
    assert res["retention"] == [1.0, 0.5, 0.3]


def test_retention_ignores_pre_cohort_return():
    events = [_ev("u1", "active", -1), _ev("u1", "signup", 0)]
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    res = query.retention(events, "signup", "active", 1, start, end)
    assert res["retention"] == [1.0, 0.0]


def test_breakdown_top_values():
    events = (
        [_ev(f"a{i}", "open", 0, props={"channel": "email"}) for i in range(5)]
        + [_ev(f"b{i}", "open", 0, props={"channel": "sms"}) for i in range(3)]
        + [_ev("c0", "open", 0, props={"channel": "push"})]
        + [_ev("d0", "open", 0)]  # missing property -> excluded
    )
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    rows = query.breakdown(events, "open", "channel", start, end, limit=2)
    assert [(r["value"], r["count"]) for r in rows] == [("email", 5), ("sms", 3)]


def test_breakdown_stringifies_values():
    events = [_ev("u1", "x", 0, props={"n": 42})]
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    rows = query.breakdown(events, "x", "n", start, end, limit=10)
    assert rows[0]["value"] == "42"


def test_retention_right_censoring():
    # u1: full history. u2: cohort day too recent to have had day 1 yet —
    # u2 must not drag day 1 down.
    events = [
        _ev("u1", "signup", 0),
        _ev("u1", "active", 1),
        _ev("u2", "signup", 4),
    ]
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 6, tzinfo=UTC)
    res = query.retention(events, "signup", "active", 2, start, end)
    assert res["cohort_size"] == 2
    # latest observed date is Oct 5 (u2's signup). Day 1: only u1 eligible
    # (Oct 2 <= Oct 5); u2's Oct 6 > Oct 5, censored. Day 2: u1 eligible.
    assert res["retention"] == [1.0, 1.0, 0.0]


def test_breakdown_excludes_none_values():
    events = [
        _ev("u1", "x", 0, props={"channel": "email"}),
        _ev("u2", "x", 0, props={"channel": None}),
    ]
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    rows = query.breakdown(events, "x", "channel", start, end, limit=10)
    assert [(r["value"], r["count"]) for r in rows] == [("email", 1)]


def test_naive_datetimes_treated_as_utc():
    events = [
        {
            "event": "x",
            "distinct_id_hash": "h-u1",
            "occurred_at": datetime(2026, 10, 1, 12),  # naive
            "properties": {},
        }
    ]
    start = datetime(2026, 10, 1)  # naive
    end = datetime(2026, 10, 2)  # naive
    rows = query.trend(events, "x", start, end, "day")
    assert rows[0]["count"] == 1
    assert rows[0]["bucket_start"] == "2026-10-01T00:00:00+00:00"
