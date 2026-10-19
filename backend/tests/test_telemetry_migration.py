"""TW-622 Twistor Telemetry — PostHog migration import (TDD RED)."""

import pytest

from app.telemetry import migration, models


def test_posthog_event_maps_cleanly():
    ph = {
        "event": "user signed up",
        "distinct_id": "abc-123",
        "properties": {"plan": "pro", "$browser": "Chrome"},
        "timestamp": "2026-10-01T12:00:00+00:00",
    }
    ev = migration.from_posthog_event(ph)
    assert isinstance(ev, models.TelemetryEvent)
    assert ev.event == "user signed up"
    assert ev.distinct_id == "abc-123"
    assert ev.properties["plan"] == "pro"


def test_posthog_event_requires_name():
    with pytest.raises(migration.MigrationError):
        migration.from_posthog_event({"distinct_id": "x"})


def test_posthog_funnel_maps_steps_and_window():
    ph = {
        "name": "Onboarding",
        "steps": [
            {"name": "signed up"},
            {"name": "invited team"},
            {"name": "created project"},
        ],
        "funnel_window_days": 14,
    }
    f = migration.from_posthog_funnel(ph)
    assert isinstance(f, models.FunnelDefinition)
    assert [s.event for s in f.steps] == [
        "signed up",
        "invited team",
        "created project",
    ]
    assert f.window_hours == 14 * 24


def test_migrate_with_report_lists_unmapped_honestly():
    export = {
        "events": [
            {"event": "a", "distinct_id": "u1", "properties": {}},
        ],
        "funnels": [
            {"name": "f", "steps": [{"name": "a"}, {"name": "b"}]},
        ],
        "session_replays": [{"id": "r1"}],
        "heatmaps": [{"id": "h1"}],
        "experiments": [{"id": "e1"}],
        "feature_flags": [{"key": "fl"}],
    }
    report = migration.migrate_with_report(export)
    assert len(report.events) == 1
    assert len(report.funnels) == 1
    unmapped = " ".join(report.unmapped).lower()
    for word in ("session replay", "heatmap", "experiment", "feature flag"):
        assert word in unmapped


def test_migrate_empty_export():
    report = migration.migrate_with_report({})
    assert report.events == []
    assert report.funnels == []
