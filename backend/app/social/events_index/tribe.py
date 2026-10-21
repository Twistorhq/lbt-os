"""TW-651: the Tribe Events WordPress connector (spec 10.3, map 3.3).

Sites running The Events Calendar plugin expose
/wp-json/tribe/events/v1/events by default. Each Denver community
site is onboarded as one configuration entry, not new code. Only
published events are extracted; drafts and other statuses are the
site's own business. Description and image fields are stripped at
this boundary and never reach bronze (spec 10.6).
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import ExtractedEvent, SourceConfig, VenueFacts
from .redact import canonical_json, redact_json


def feed_items(payload: dict) -> list[dict]:
    return list(payload.get("events", []))


def _parse_dt(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=tz)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def convert_item(
    event: dict, source: SourceConfig, now: datetime
) -> list[ExtractedEvent]:
    """Convert one Tribe event; [] when it is not published."""
    if str(event.get("status", "")).lower() != "publish":
        return []
    event_id = event.get("id")
    if event_id is None:
        raise ValueError("tribe event without id")
    title = html.unescape(str(event.get("title", ""))).strip()
    if not title:
        raise ValueError(f"tribe event {event_id} without title")
    tz = ZoneInfo(event.get("timezone") or "America/Denver")
    starts_at = _parse_utc(event.get("utc_start_date")) or _parse_dt(
        event.get("start_date"), tz
    )
    if starts_at is None:
        raise ValueError(f"tribe event {event_id} without start date")
    ends_at = _parse_utc(event.get("utc_end_date")) or _parse_dt(
        event.get("end_date"), tz
    )

    venue_raw = event.get("venue") or {}
    venue = VenueFacts()
    if isinstance(venue_raw, dict) and venue_raw:
        venue = VenueFacts(
            name=str(venue_raw.get("venue") or "").strip() or None,
            street_address=str(venue_raw.get("address") or "").strip() or None,
            city=str(venue_raw.get("city") or "").strip() or None,
            latitude=_to_float(venue_raw.get("geo_lat")),
            longitude=_to_float(venue_raw.get("geo_lng")),
        )

    organizer_ref = None
    organizers = event.get("organizer") or []
    if organizers and isinstance(organizers[0], dict):
        organizer_ref = str(organizers[0].get("organizer") or "").strip() or None
    category = None
    categories = event.get("categories") or []
    if categories and isinstance(categories[0], dict):
        category = str(categories[0].get("name") or "").strip() or None

    bronze = redact_json(event)
    return [
        ExtractedEvent(
            source_item_key=str(event_id),
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            venue=venue,
            category=category,
            source_url=event.get("url"),
            source_organizer_ref=organizer_ref,
            status="active",
            payload_hash=hashlib.sha256(
                canonical_json(bronze).encode("utf-8")
            ).hexdigest(),
            bronze_payload=bronze,
        )
    ]


def parse_tribe(
    payload: dict, source: SourceConfig, now: datetime
) -> list[ExtractedEvent]:
    """Strict whole-page parse (the pipeline isolates per item)."""
    events: list[ExtractedEvent] = []
    for item in feed_items(payload):
        events.extend(convert_item(item, source, now))
    return events


def parse_tribe_text(
    text: str, source: SourceConfig, now: datetime
) -> list[ExtractedEvent]:
    return parse_tribe(json.loads(text), source, now)
