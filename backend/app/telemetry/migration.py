"""TW-622 Twistor Telemetry — PostHog migration import.

Maps PostHog's event/funnel concepts into Telemetry definitions. Anything
without a V1 equivalent is REPORTED as unmapped, never silently dropped —
the honest-migration rule from the Flags lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from .models import FunnelDefinition, FunnelStep, TelemetryEvent

# PostHog export sections with no V1 equivalent. Named honestly so the
# importer knows exactly what did not come over.
UNMAPPED_SECTIONS = {
    "session_replays": "session replay (out of V1 scope — events/funnels first)",
    "heatmaps": "heatmap (out of V1 scope)",
    "experiments": "experiment / A-B test (out of V1 scope)",
    "feature_flags": "feature flag (lives in Twistor Flags, not Telemetry)",
    "cohorts": "cohort (recreate as a saved insight filter)",
    "actions": "action (recreate as a named event or insight)",
    "annotations": "annotation (no V1 equivalent)",
}


class MigrationError(Exception):
    """Fail-loud migration error. The router maps these to 4xx."""


@dataclass
class MappedFunnel:
    """A PostHog funnel plus its original name, so the import can persist
    it as a named insight instead of dropping the definition."""

    name: str | None
    definition: FunnelDefinition


@dataclass
class MigrationReport:
    events: list[TelemetryEvent] = field(default_factory=list)
    funnels: list[MappedFunnel] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)


def from_posthog_event(ph: dict[str, Any]) -> TelemetryEvent:
    """Map a PostHog capture payload to a TelemetryEvent."""
    name = ph.get("event") or ph.get("name")
    distinct_id = ph.get("distinct_id")
    if not name or not str(name).strip():
        raise MigrationError("posthog event is missing its event name")
    if not distinct_id:
        raise MigrationError("posthog event is missing distinct_id")
    props = ph.get("properties") or {}
    if not isinstance(props, dict):
        raise MigrationError("posthog event properties must be an object")
    try:
        return TelemetryEvent(
            event=str(name).strip(),
            distinct_id=str(distinct_id),
            properties=props,
            **({"occurred_at": ph["timestamp"]} if ph.get("timestamp") else {}),
        )
    except ValidationError as exc:
        raise MigrationError(f"posthog event failed validation: {exc}") from exc


def from_posthog_funnel(ph: dict[str, Any]) -> FunnelDefinition:
    """Map a PostHog funnel to a FunnelDefinition."""
    raw_steps = ph.get("steps") or []
    steps = []
    for s in raw_steps:
        name = (s or {}).get("name") or (s or {}).get("event")
        if name and str(name).strip():
            steps.append(FunnelStep(event=str(name).strip()))
    if len(steps) < 2:
        raise MigrationError(
            f"posthog funnel needs at least 2 named steps, got {len(steps)}"
        )
    window_days = ph.get("funnel_window_days") or ph.get("window_days") or 3
    try:
        window_hours = int(window_days) * 24
    except (TypeError, ValueError) as exc:
        raise MigrationError(
            f"posthog funnel window is not a number: {window_days}"
        ) from exc
    try:
        return FunnelDefinition(steps=steps, window_hours=window_hours)
    except ValidationError as exc:
        raise MigrationError(f"posthog funnel failed validation: {exc}") from exc


def migrate_with_report(export: dict[str, Any]) -> MigrationReport:
    """Migrate a PostHog export dict. Unmapped sections reported, not dropped."""
    report = MigrationReport()
    for ph in export.get("events") or []:
        try:
            report.events.append(from_posthog_event(ph))
        except MigrationError as exc:
            report.unmapped.append(f"event skipped: {exc}")
    for ph in export.get("funnels") or []:
        try:
            report.funnels.append(
                MappedFunnel(
                    name=(ph.get("name") or "").strip() or None,
                    definition=from_posthog_funnel(ph),
                )
            )
        except MigrationError as exc:
            report.unmapped.append(f"funnel skipped: {exc}")
    for section, label in UNMAPPED_SECTIONS.items():
        items = export.get(section) or []
        if items:
            report.unmapped.append(f"{len(items)} x {label}")
    return report


def funnel_window(funnel: FunnelDefinition) -> timedelta:
    return timedelta(hours=funnel.window_hours)
