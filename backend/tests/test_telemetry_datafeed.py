"""TW-622 Twistor Telemetry — benchmark datafeed aggregates (TDD RED).

The event stream feeds Twistor's benchmark niches. Only cohort-level
aggregates leave the building, gated on a minimum cohort size —
no identifiers, ever.
"""

from datetime import datetime, timezone

from app.telemetry import datafeed

UTC = timezone.utc


def _ev(user, event, props):
    return {
        "event": event,
        "distinct_id_hash": f"h-{user}",
        "occurred_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
        "properties": props,
    }


def test_cohort_aggregates_group_and_count():
    events = [_ev(f"a{i}", "job_done", {"trade": "hvac"}) for i in range(8)] + [
        _ev(f"b{i}", "job_done", {"trade": "plumbing"}) for i in range(6)
    ]
    rows = datafeed.cohort_aggregates(events, "job_done", ["trade"])
    by_trade = {r["cohort"]["trade"]: r for r in rows}
    assert by_trade["hvac"]["distinct_users"] == 8
    assert by_trade["hvac"]["event_count"] == 8
    assert by_trade["plumbing"]["distinct_users"] == 6


def test_small_cohorts_suppressed():
    events = [_ev(f"a{i}", "job_done", {"trade": "hvac"}) for i in range(8)] + [
        _ev("solo", "job_done", {"trade": "solar"})
    ]
    rows = datafeed.cohort_aggregates(events, "job_done", ["trade"], min_cohort=5)
    trades = [r["cohort"]["trade"] for r in rows]
    assert "hvac" in trades
    assert "solar" not in trades  # k-anonymity gate


def test_no_identifiers_in_output():
    events = [_ev(f"a{i}", "job_done", {"trade": "hvac"}) for i in range(8)]
    rows = datafeed.cohort_aggregates(events, "job_done", ["trade"])
    blob = str(rows)
    assert "h-a0" not in blob
    assert "distinct_id" not in blob


def test_empty_events():
    assert datafeed.cohort_aggregates([], "x", ["trade"]) == []
