"""TW-651: the generic ICS importer (spec 10.3, Diego source map pipe 1).

One parser covers DPL LibCal, Lu.ma calendars, and every future ICS
source: fetch the published feed, unfold, parse VEVENTs, expand
recurrences into instances inside a rolling 90-day window, extract
facts. Recurrence expansion rides python-dateutil's rrule engine
(already a platform dependency); DTSTART is always counted as the
first instance, as calendar clients do.

Never-store (spec 10.6) at this boundary: DESCRIPTION and image
properties are stripped from the item before it is hashed or landed
in bronze, and no extracted shape carries them at all. The contact
properties are stripped the same way: ORGANIZER and ATTENDEE lines
carry mailto addresses and CONTACT carries free-form contact text,
and spec 10.6 bars organizer contact details and attendee lists
outright. Bronze is storage, so redaction there is the line, not
just extraction (evt_listing_sources.source_organizer_ref is for
organizer names and ids published by API sources, never contact
details, and stays NULL for ICS).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.rrule import rruleset, rrulestr

from .models import ExtractedEvent, SourceConfig, VenueFacts
from .normalize import payload_hash

WINDOW_DAYS = 90
MAX_INSTANCES_PER_EVENT = 200

# Properties stripped from a VEVENT before hashing or bronze
# landing: DESCRIPTION and ATTACH/IMAGE below are the source's
# expression; ORGANIZER, CONTACT, and ATTENDEE are the contact
# carriers (spec 10.6). _redact_block matches on the property name
# before any ";" parameter, so parameterized forms (ORGANIZER;CN=,
# ATTENDEE;RSVP=TRUE:) are covered, as are lines nested inside
# VALARM blocks, because redaction runs per line over the block.
_REDACTED_PROPS = ("DESCRIPTION", "ORGANIZER", "CONTACT", "ATTENDEE")
_DURATION_RE = re.compile(
    r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)
_STATE_RE = re.compile(r"^[A-Z]{2}(\s+\d{5})?$")


class IcsError(ValueError):
    """One VEVENT is unparseable. Raised per item so the pipeline can
    dead-letter the poison item and keep the rest of the feed."""


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def split_vevents(text: str) -> list[str]:
    """Split a feed into raw VEVENT blocks for per-item isolation."""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = [line]
        elif line == "END:VEVENT" and current is not None:
            current.append(line)
            blocks.append("\n".join(current))
            current = None
        elif current is not None:
            current.append(line)
    return blocks


def _unescape_text(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in ("n", "N") else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_properties(block: str) -> dict[str, tuple[str, dict[str, str]]]:
    props: dict[str, tuple[str, dict[str, str]]] = {}
    for line in block.split("\n"):
        if ":" not in line or line.startswith(("BEGIN", "END")):
            continue
        head, value = line.split(":", 1)
        parts = head.split(";")
        name = parts[0].upper()
        params: dict[str, str] = {}
        for part in parts[1:]:
            if "=" in part:
                key, val = part.split("=", 1)
                params[key.upper()] = val
        props.setdefault(name, (value.strip(), params))
    return props


def _parse_datetime(
    value: str, params: dict[str, str], default_tz: ZoneInfo
) -> datetime:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        day = datetime.strptime(value, "%Y%m%d").date()
        return datetime(day.year, day.month, day.day, tzinfo=default_tz)
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    tzid = params.get("TZID")
    tz = ZoneInfo(tzid) if tzid else default_tz
    return naive.replace(tzinfo=tz)


def _parse_duration(value: str) -> timedelta:
    match = _DURATION_RE.match(value.strip())
    if not match:
        raise IcsError(f"unparseable DURATION: {value!r}")
    weeks, days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return timedelta(
        weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds
    )


def _parse_location(value: str) -> VenueFacts:
    # ICS escapes commas inside the value (\,), so unescape first;
    # the split below is a formatting heuristic over the plain text.
    text = _unescape_text(value).strip()
    if not text:
        return VenueFacts()
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 3 and _STATE_RE.match(parts[-1]):
        city = parts[-2]
        if len(parts) > 3:
            # "Venue, Street, City, ST ZIP": the middle is the street.
            street = ", ".join(parts[1:-2])
            return VenueFacts(name=parts[0], street_address=street, city=city)
        # Exactly three parts is "Venue, City, ST ZIP" (no street was
        # published) or "Street, City, ST ZIP"; a house number in the
        # first part is what tells them apart. The city never belongs
        # in the street slot.
        if any(ch.isdigit() for ch in parts[0]):
            return VenueFacts(street_address=parts[0], city=city)
        return VenueFacts(name=parts[0], city=city)
    if len(parts) == 2 and any(ch.isdigit() for ch in parts[0]):
        return VenueFacts(street_address=parts[0], city=parts[1])
    return VenueFacts(name=", ".join(parts))


def _redact_block(block: str) -> str:
    kept = []
    for line in block.split("\n"):
        head = line.split(":", 1)[0].split(";", 1)[0].upper()
        if head in _REDACTED_PROPS or "IMAGE" in head or head == "ATTACH":
            continue
        kept.append(line)
    return "\n".join(kept)


def _expand_instances(
    props: dict[str, tuple[str, dict[str, str]]],
    dtstart: datetime,
    default_tz: ZoneInfo,
    now: datetime,
) -> list[datetime]:
    window_start = now - timedelta(days=1)
    window_end = now + timedelta(days=WINDOW_DAYS)
    rrule_prop = props.get("RRULE")
    if rrule_prop is None:
        return [dtstart] if window_start <= dtstart <= window_end else []
    rule = rruleset()
    rule.rrule(rrulestr(rrule_prop[0], dtstart=dtstart))
    exdate_prop = props.get("EXDATE")
    if exdate_prop is not None:
        value, params = exdate_prop
        for chunk in value.split(","):
            if chunk.strip():
                rule.exdate(_parse_datetime(chunk, params, default_tz))
    instances = set(rule.between(window_start, window_end, inc=True))
    # DTSTART is the first instance by definition, even when the rule
    # pattern (say BYDAY=2TU) does not land on it.
    if window_start <= dtstart <= window_end:
        instances.add(dtstart)
    return sorted(instances)[:MAX_INSTANCES_PER_EVENT]


def parse_vevent(
    block: str, source: SourceConfig, now: datetime
) -> list[ExtractedEvent]:
    """Parse one VEVENT block into its instances inside the window."""
    props = _parse_properties(block)
    default_tz = ZoneInfo(source.config.get("default_timezone", "America/Denver"))
    uid_prop = props.get("UID")
    if uid_prop is None or not uid_prop[0]:
        raise IcsError("VEVENT without UID")
    uid = uid_prop[0]
    summary_prop = props.get("SUMMARY")
    if summary_prop is None or not _unescape_text(summary_prop[0]).strip():
        raise IcsError(f"VEVENT {uid} without SUMMARY")
    title = _unescape_text(summary_prop[0]).strip()
    dtstart_prop = props.get("DTSTART")
    if dtstart_prop is None:
        raise IcsError(f"VEVENT {uid} without DTSTART")
    try:
        dtstart = _parse_datetime(dtstart_prop[0], dtstart_prop[1], default_tz)
    except (ValueError, KeyError) as exc:
        raise IcsError(f"VEVENT {uid} bad DTSTART: {exc}") from exc

    ends_at: datetime | None = None
    dtend_prop = props.get("DTEND")
    duration_prop = props.get("DURATION")
    if dtend_prop is not None:
        ends_at = _parse_datetime(dtend_prop[0], dtend_prop[1], default_tz)
    elif duration_prop is not None:
        ends_at = dtstart + _parse_duration(duration_prop[0])
    elif dtstart_prop[1].get("VALUE") == "DATE" or re.fullmatch(
        r"\d{8}", dtstart_prop[0].strip()
    ):
        ends_at = dtstart + timedelta(days=1)

    venue = VenueFacts()
    location_prop = props.get("LOCATION")
    if location_prop is not None:
        venue = _parse_location(location_prop[0])
    geo_prop = props.get("GEO")
    if geo_prop is not None:
        try:
            lat_s, lng_s = geo_prop[0].split(";", 1)
            venue.latitude = float(lat_s)
            venue.longitude = float(lng_s)
        except ValueError:
            pass  # A bad GEO never kills the event; the venue cache retries.

    status = "active"
    status_prop = props.get("STATUS")
    if status_prop is not None and status_prop[0].strip().upper() == "CANCELLED":
        status = "cancelled"
    url_prop = props.get("URL")
    source_url = url_prop[0].strip() if url_prop is not None else None
    category = None
    categories_prop = props.get("CATEGORIES")
    if categories_prop is not None:
        category = _unescape_text(categories_prop[0]).split(",")[0].strip() or None

    bronze = _redact_block(block)
    recurring = "RRULE" in props
    events: list[ExtractedEvent] = []
    for start in _expand_instances(props, dtstart, default_tz, now):
        end = None
        if ends_at is not None:
            end = ends_at + (start - dtstart)
        key = uid
        if recurring:
            key = f"{uid}@{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
        events.append(
            ExtractedEvent(
                source_item_key=key,
                title=title,
                starts_at=start,
                ends_at=end,
                venue=venue,
                category=category,
                source_url=source_url,
                source_organizer_ref=None,
                status=status,
                payload_hash=payload_hash(f"{bronze}|{start.isoformat()}"),
                bronze_payload=bronze,
            )
        )
    return events


def parse_ics(text: str, source: SourceConfig, now: datetime) -> list[ExtractedEvent]:
    """Strict whole-feed parse: raises on the first poison VEVENT.

    The pipeline does not use this; it isolates per VEVENT via
    split_vevents + parse_vevent so one bad item dead-letters alone.
    """
    events: list[ExtractedEvent] = []
    for block in split_vevents(text):
        events.extend(parse_vevent(block, source, now))
    return events
