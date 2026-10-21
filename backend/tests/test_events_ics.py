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
