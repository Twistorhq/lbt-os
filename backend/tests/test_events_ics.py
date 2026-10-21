"""TW-651: the generic ICS importer (spec 10.3, Diego source map pipe 1).

One parser covers DPL LibCal, Lu.ma, and every future ICS source.
Contract pinned here: VEVENT facts only (the source's DESCRIPTION is
expression, never extracted, never in the bronze payload we keep),
recurrences expanded into instances inside a rolling 90-day window,
cancellations carried as status, all times normalized to UTC.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.social.events_index import ics
from app.social.events_index.models import SourceConfig

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "events" / "dpl_sample.ics"
).read_text()

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def source() -> SourceConfig:
    return SourceConfig(
        id="src-dpl",
        name="Denver Public Library",
        kind="ics",
        connector="ics_generic",
        terms_posture="index_facts",
        attribution_label="Denver Public Library",
        feed_url="https://denverlibrary.libcal.com/ical_subscribe.php?src=pical",
        config={"default_timezone": "America/Denver"},
    )


def _by_title(events, title):
    return [e for e in events if e.title == title]


def test_parses_all_events_and_expands_recurrences(source):
    events = ics.parse_ics(FIXTURE, source, NOW)
    # 4 storytime instances + tech help + cancelled + market + cleanup
    # + 2 choir instances (DTSTART Oct 25, Dec 8; Nov 10 is EXDATEd).
    assert len(events) == 10


def test_storytime_expands_weekly_count(source):
    events = _by_title(ics.parse_ics(FIXTURE, source, NOW), "Preschool Storytime")
    starts = sorted(e.starts_at for e in events)
    assert starts == [
        datetime(2026, 10, 24, 16, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 7, 17, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 14, 17, 30, tzinfo=timezone.utc),
    ]
    assert all(e.source_item_key.startswith("storytime-1001") for e in events)
    assert len({e.source_item_key for e in events}) == 4


def test_utc_event_keeps_utc(source):
    events = _by_title(ics.parse_ics(FIXTURE, source, NOW), "One-on-One Tech Help")
    assert len(events) == 1
    assert events[0].starts_at == datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc)
    assert events[0].ends_at == datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)


def test_cancelled_status_carried(source):
    events = _by_title(ics.parse_ics(FIXTURE, source, NOW), "Evening Book Club")
    assert len(events) == 1
    assert events[0].status == "cancelled"


def test_duration_and_geo_and_address_parsing(source):
    events = _by_title(ics.parse_ics(FIXTURE, source, NOW), "Neighborhood Night Market")
    assert len(events) == 1
    e = events[0]
    assert e.starts_at == datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)
    assert e.ends_at == datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)
    assert e.venue.name == "Mercado Hall"
    assert e.venue.street_address == "2901 Inca St"
    assert e.venue.city == "Denver"
    assert e.venue.latitude == pytest.approx(39.75891)
    assert e.venue.longitude == pytest.approx(-104.99988)
    assert e.category == "Market"


def test_all_day_event(source):
    events = _by_title(ics.parse_ics(FIXTURE, source, NOW), "Creek Cleanup Day")
    assert len(events) == 1
    e = events[0]
    # All-day dates are interpreted in the source timezone (Denver).
    assert e.starts_at == datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)
    assert e.ends_at == datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)


def test_monthly_byday_with_exdate(source):
    events = _by_title(ics.parse_ics(FIXTURE, source, NOW), "Community Choir Rehearsal")
    starts = sorted(e.starts_at for e in events)
    assert starts == [
        datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 12, 8, 23, 0, tzinfo=timezone.utc),
    ]


def test_description_never_extracted(source):
    events = ics.parse_ics(FIXTURE, source, NOW)
    for e in events:
        assert not hasattr(e, "description")
        blob = repr(e)
        assert "librarians" not in blob
        assert "Registration opens" not in blob
        assert "Food trucks" not in blob
        assert "Drop in for help" not in blob


def test_bronze_payload_is_redacted_and_hashed(source):
    events = ics.parse_ics(FIXTURE, source, NOW)
    storytime = _by_title(events, "Preschool Storytime")[0]
    assert storytime.bronze_payload is not None
    assert "DESCRIPTION" not in storytime.bronze_payload
    assert "librarians" not in storytime.bronze_payload
    assert len(storytime.payload_hash) == 64
    # Same instance content across runs hashes identically.
    again = _by_title(ics.parse_ics(FIXTURE, source, NOW), "Preschool Storytime")[0]
    assert again.payload_hash == storytime.payload_hash


def test_recurrence_window_clips_at_ninety_days(source):
    text = FIXTURE.replace(
        "RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=SA", "RRULE:FREQ=DAILY;COUNT=200"
    )
    events = _by_title(ics.parse_ics(text, source, NOW), "Preschool Storytime")
    horizon = NOW.timestamp() + 90 * 86400
    assert all(e.starts_at.timestamp() <= horizon for e in events)
    assert len(events) <= 91


def test_unfolded_lines_parse(source):
    # The fixture DESCRIPTION spans folded lines; the SUMMARY of the
    # market event also proves property parsing survived folding.
    events = ics.parse_ics(FIXTURE, source, NOW)
    assert any(e.source_url and e.source_url.endswith("/event/1001") for e in events)


def test_poison_vevent_raises_for_isolation(source):
    bad = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:broken-1\n"
        "DTSTART:NOT-A-DATE\nSUMMARY:Broken\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    with pytest.raises(ValueError):
        ics.parse_ics(bad, source, NOW)


def test_split_vevents_supports_per_item_isolation(source):
    blocks = ics.split_vevents(FIXTURE)
    assert len(blocks) == 6
    good = ics.parse_ics(FIXTURE, source, NOW)
    reparsed = []
    for block in blocks:
        reparsed.extend(ics.parse_vevent(block, source, NOW))
    assert len(reparsed) == len(good)


# ------------------------------------------------------------------
# Fix round (Rosa TW-651 review): M1 never-store contract, n1 location
# ------------------------------------------------------------------

CONTACT_VEVENT = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:hosted-1@example.org
DTSTART;TZID=America/Denver:20261028T190000
DTEND;TZID=America/Denver:20261028T210000
SUMMARY:Hosted Gathering
ORGANIZER;CN=Jane Host:mailto:jane.host@example.org
CONTACT:Questions\\, jane.host@example.org\\, 303-555-1212
ATTENDEE;CN=Bob Guest;RSVP=TRUE:mailto:bob.guest@example.org
END:VEVENT
END:VCALENDAR
"""

CONTACT_MARKERS = (
    "ORGANIZER",
    "CONTACT",
    "ATTENDEE",
    "mailto",
    "Jane Host",
    "jane.host@example.org",
    "bob.guest@example.org",
    "303-555-1212",
)


def test_organizer_contact_attendee_never_reach_bronze_or_silver(source):
    # Fixture-independent contract (Rosa M1): a VEVENT carrying
    # ORGANIZER, CONTACT, and ATTENDEE lines is built inline here, so
    # the never-store line (spec 10.6) cannot slip through on a
    # fixture that happens to lack those properties again.
    events = ics.parse_ics(CONTACT_VEVENT, source, NOW)
    assert len(events) == 1
    e = events[0]
    assert e.bronze_payload is not None
    for marker in CONTACT_MARKERS:
        assert marker not in e.bronze_payload, f"{marker} reached bronze"
        assert marker not in repr(e), f"{marker} reached silver"
    assert e.source_organizer_ref is None


def _one_event_with_location(source, location: str):
    text = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:loc-1@example.org\n"
        "DTSTART;TZID=America/Denver:20261028T190000\n"
        "DTEND;TZID=America/Denver:20261028T210000\n"
        "SUMMARY:Location Mapping\n"
        f"LOCATION:{location}\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )
    return ics.parse_ics(text, source, NOW)[0]


def test_location_three_part_venue_city_state(source):
    # "Venue, City, ST ZIP": the city belongs in the city slot, and no
    # street was published, so the street slot stays empty (Rosa n1).
    e = _one_event_with_location(source, "Mercado Hall\\, Denver\\, CO 80204")
    assert e.venue.name == "Mercado Hall"
    assert e.venue.city == "Denver"
    assert e.venue.street_address is None


def test_location_three_part_street_city_state(source):
    # "Street, City, ST ZIP": a street-shaped first part (it carries a
    # house number) is the street, not a venue name.
    e = _one_event_with_location(source, "2901 Inca St\\, Denver\\, CO 80204")
    assert e.venue.street_address == "2901 Inca St"
    assert e.venue.city == "Denver"
    assert e.venue.name is None


# ------------------------------------------------------------------
# Fix round 2 (Rosa TW-651 re-review): m4 X-ALT-DESC, n3 group prefix
# ------------------------------------------------------------------

OUTLOOK_VEVENT = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:outlook-1@example.org
DTSTART;TZID=America/Denver:20261028T190000
DTEND;TZID=America/Denver:20261028T210000
SUMMARY:Outlook Published Social
DESCRIPTION:Plain twin of the HTML body.
X-ALT-DESC;FMTTYPE=text/html:<html><body>Secret supper club details, RSVP to outlook.host@example.org</body></html>
END:VEVENT
END:VCALENDAR
"""


def test_x_alt_desc_never_reaches_bronze_or_silver(source):
    # Fixture-independent contract (Rosa m4): Outlook and Exchange
    # publish the description twice, once as DESCRIPTION and once as
    # the HTML twin X-ALT-DESC. Both are the source's expression
    # (spec 10.6 never-store), so neither the property name nor the
    # HTML body may reach bronze or any extracted field.
    events = ics.parse_ics(OUTLOOK_VEVENT, source, NOW)
    assert len(events) == 1
    e = events[0]
    assert e.bronze_payload is not None
    for marker in (
        "X-ALT-DESC",
        "Secret supper club",
        "outlook.host@example.org",
        "<html>",
    ):
        assert marker not in e.bronze_payload, f"{marker} reached bronze"
        assert marker not in repr(e), f"{marker} reached silver"


GROUPED_CONTACT_VEVENT = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:grouped-1@example.org
DTSTART;TZID=America/Denver:20261028T190000
DTEND;TZID=America/Denver:20261028T210000
SUMMARY:Grouped Contact Gathering
item1.ORGANIZER;CN=Jane Host:mailto:jane.host@example.org
item1.CONTACT:Questions\\, jane.host@example.org\\, 303-555-1212
END:VEVENT
END:VCALENDAR
"""


def test_group_prefixed_contact_props_never_reach_bronze(source):
    # Fixture-independent contract (Rosa n3): RFC 5545 content lines
    # are [group "."] name, so a group prefix (item1.ORGANIZER) is
    # still ORGANIZER. The redaction match must see through the
    # prefix, or the name and email land in bronze untouched.
    events = ics.parse_ics(GROUPED_CONTACT_VEVENT, source, NOW)
    assert len(events) == 1
    e = events[0]
    assert e.bronze_payload is not None
    for marker in CONTACT_MARKERS:
        assert marker not in e.bronze_payload, f"{marker} reached bronze"
        assert marker not in repr(e), f"{marker} reached silver"
    assert e.source_organizer_ref is None
