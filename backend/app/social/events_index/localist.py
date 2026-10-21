"""TW-651: the Localist JSON connector (spec 10.3, Diego map 3.2).

CU Denver and CU Anschutz run Localist, whose public read-only JSON
API (/api/2/events) is pipe 2: an open feed. Facts plus attribution,
link back. The public filter follows the source map: drop events
flagged as academic deadlines (registration dates and application
windows are calendar noise, not meetups) and keep only live events;
a cancelled event is carried as a cancellation, never dropped, so
the index can kill the listing the source killed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .models import ExtractedEvent, SourceConfig, VenueFacts
from .redact import canonical_json, redact_json

_DEADLINE_MARKERS = ("deadline", "registration date", "application window")


def feed_items(payload: dict) -> list[dict]:
    return [entry.get("event", {}) for entry in payload.get("events", [])]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_deadline_noise(event: dict) -> bool:
    filters = event.get("filters") or {}
    names: list[str] = []
    for type_entry in filters.get("event_types") or []:
        names.append(str(type_entry.get("name", "")).lower())
    for tag in event.get("tags") or []:
        names.append(str(tag).lower())
    return any(marker in name for name in names for marker in _DEADLINE_MARKERS)


def convert_item(
    event: dict, source: SourceConfig, now: datetime
) -> list[ExtractedEvent]:
    """Convert one Localist event; [] when the public filter drops it."""
    if _is_deadline_noise(event):
        return []
    status_raw = str(event.get("status", "live")).lower()
    status = "cancelled" if status_raw in ("canceled", "cancelled") else "active"
    event_id = event.get("id")
    if event_id is None:
        raise ValueError("localist event without id")
    title = str(event.get("name", "")).strip()
    if not title:
        raise ValueError(f"localist event {event_id} without name")

    geo = event.get("geo") or {}
    address = str(event.get("address") or "").strip()
    street = None
    city = None
    if address:
        parts = [p.strip() for p in address.split(",") if p.strip()]
        street = parts[0] if parts else None
        if len(parts) >= 2:
            city = parts[1]
    venue = VenueFacts(
        name=str(event.get("location_name") or "").strip() or None,
        street_address=street,
        city=city,
        latitude=float(geo["latitude"]) if geo.get("latitude") is not None else None,
        longitude=float(geo["longitude"]) if geo.get("longitude") is not None else None,
    )
    source_url = event.get("localist_url") or event.get("url")
    bronze = redact_json(event)
    bronze_hash = hashlib.sha256(canonical_json(bronze).encode("utf-8")).hexdigest()

    instances = event.get("event_instances") or []
    extracted: list[ExtractedEvent] = []
    for entry in instances:
        instance = entry.get("event_instance", {})
        starts_at = _parse_dt(instance.get("start"))
        if starts_at is None:
            continue
        instance_id = instance.get("id", starts_at.isoformat())
        extracted.append(
            ExtractedEvent(
                source_item_key=f"{event_id}:{instance_id}",
                title=title,
                starts_at=starts_at,
                ends_at=_parse_dt(instance.get("end")),
                venue=venue,
                source_url=source_url,
                status=status,
                payload_hash=bronze_hash,
                bronze_payload=bronze,
            )
        )
    return extracted


def parse_localist(
    payload: dict, source: SourceConfig, now: datetime
) -> list[ExtractedEvent]:
    """Strict whole-page parse (the pipeline isolates per item)."""
    events: list[ExtractedEvent] = []
    for item in feed_items(payload):
        events.extend(convert_item(item, source, now))
    return events


def parse_localist_text(
    text: str, source: SourceConfig, now: datetime
) -> list[ExtractedEvent]:
    return parse_localist(json.loads(text), source, now)
