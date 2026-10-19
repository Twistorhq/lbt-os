"""TW-622 Twistor Telemetry — models: events, batches, queries, insights (TDD RED)."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.telemetry import models


def test_event_requires_name_and_distinct_id():
    with pytest.raises(ValidationError):
        models.TelemetryEvent(distinct_id="u1")
    with pytest.raises(ValidationError):
        models.TelemetryEvent(event="")
    with pytest.raises(ValidationError):
        models.TelemetryEvent(event="  ", distinct_id="u1")


def test_event_defaults():
    e = models.TelemetryEvent(event="page_view", distinct_id="u1")
    assert e.properties == {}
    assert e.occurred_at is not None
    # occurred_at defaults to approximately now
    assert abs((datetime.now(timezone.utc) - e.occurred_at).total_seconds()) < 60


def test_event_rejects_too_many_properties():
    with pytest.raises(ValidationError):
        models.TelemetryEvent(
            event="x",
            distinct_id="u1",
            properties={f"k{i}": i for i in range(101)},
        )


def test_event_rejects_non_json_scalar_values():
    class Weird:
        pass

    with pytest.raises(ValidationError):
        models.TelemetryEvent(event="x", distinct_id="u1", properties={"k": Weird()})


def test_event_accepts_nested_properties():
    e = models.TelemetryEvent(
        event="x",
        distinct_id="u1",
        properties={"a": {"b": [1, 2, {"c": "d"}]}, "n": None, "f": 1.5},
    )
    assert e.properties["a"]["b"][2]["c"] == "d"


def test_batch_bounds():
    with pytest.raises(ValidationError):
        models.CaptureBatch(events=[])
    ev = models.TelemetryEvent(event="x", distinct_id="u1")
    with pytest.raises(ValidationError):
        models.CaptureBatch(events=[ev] * 1001)
    b = models.CaptureBatch(events=[ev])
    assert len(b.events) == 1


def test_trend_query_validation():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        models.TrendQuery(event="x", start=now, end=now - timedelta(days=1))
    with pytest.raises(ValidationError):
        models.TrendQuery(event="x", start=now, end=now, bucket="fortnight")
    q = models.TrendQuery(event="x", start=now - timedelta(days=7), end=now)
    assert q.bucket == "day"


def test_funnel_definition_validation():
    with pytest.raises(ValidationError):
        models.FunnelDefinition(steps=[models.FunnelStep(event="a")])
    with pytest.raises(ValidationError):
        models.FunnelDefinition(
            steps=[models.FunnelStep(event="a"), models.FunnelStep(event="b")],
            window_hours=0,
        )
    f = models.FunnelDefinition(
        steps=[models.FunnelStep(event="a"), models.FunnelStep(event="b")],
    )
    assert f.window_hours == 72


def test_retention_query_validation():
    now = datetime.now(timezone.utc)
    kw = dict(
        cohort_event="a", return_event="b", start=now - timedelta(days=30), end=now
    )
    with pytest.raises(ValidationError):
        models.RetentionQuery(days=0, **kw)
    with pytest.raises(ValidationError):
        models.RetentionQuery(days=91, **kw)
    r = models.RetentionQuery(days=30, **kw)
    assert r.days == 30


def test_insight_definition_kinds():
    with pytest.raises(ValidationError):
        models.InsightDefinition(key="k", kind="heatmap", definition={})
    i = models.InsightDefinition(
        key="weekly-signups",
        kind="trend",
        definition={"event": "signup"},
    )
    assert i.kind == "trend"
    with pytest.raises(ValidationError):
        models.InsightDefinition(key="Bad Key!", kind="trend", definition={})
