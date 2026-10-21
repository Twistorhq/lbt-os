"""TW-651: Localist and Tribe Events connectors (spec 10.3 pipes 2).

Both connectors read the source's own published machine-readable
feed, extract facts only, and drop the expression fields (descrip-
tion, photos) at the connector boundary: they never reach bronze,
silver, or gold (spec 10.6).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.social.events_index import localist, tribe
from app.social.events_index.models import SourceConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"
LOCALIST = json.loads((FIXTURES / "localist_sample.json").read_text())
TRIBE = json.loads((FIXTURES / "tribe_sample.json").read_text())
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def localist_source() -> SourceConfig:
    return SourceConfig(
        id="src-cu-denver",
        name="CU Denver Events",
        kind="api",
        connector="localist",
        terms_posture="index_facts",
        attribution_label="CU Denver",
        feed_url="https://calendar.ucdenver.edu/api/2/events",
    )


@pytest.fixture()
def tribe_source() -> SourceConfig:
    return SourceConfig(
        id="src-rino",
        name="RiNo Art District",
        kind="api",
        connector="tribe_events",
        terms_posture="index_facts",
        attribution_label="RiNo Art District",
        feed_url="https://thisisrinodenver.com/wp-json/tribe/events/v1/events",
    )


def test_localist_extracts_public_events_only(localist_source):
    events = localist.parse_localist(LOCALIST, localist_source, NOW)
    titles = [e.title for e in events]
    # The academic deadline is calendar noise, not a meetup (Diego map 3.2).
    assert "Graduate School Open House" in titles
    assert "Fall Tuition Payment Deadline" not in titles


def test_localist_facts(localist_source):
    events = localist.parse_localist(LOCALIST, localist_source, NOW)
    e = next(e for e in events if e.title == "Graduate School Open House")
    assert e.source_item_key.startswith("4501234")
    assert e.starts_at == datetime(2026, 10, 29, 17, 30, tzinfo=timezone.utc)
    assert e.ends_at == datetime(2026, 10, 29, 19, 0, tzinfo=timezone.utc)
    assert e.venue.name == "Student Commons Building"
    assert e.venue.street_address == "1201 Larimer St"
    assert e.venue.city == "Denver"
    assert e.venue.latitude == pytest.approx(39.7445)
    assert e.venue.longitude == pytest.approx(-104.9999)
    assert e.source_url.endswith("/event/graduate_school_open_house")
    assert e.status == "active"


def test_localist_cancelled_maps_to_cancelled(localist_source):
    events = localist.parse_localist(LOCALIST, localist_source, NOW)
    e = next(e for e in events if "Urban Ecology" in e.title)
    assert e.status == "cancelled"


def test_localist_never_stores_description_or_photo(localist_source):
    events = localist.parse_localist(LOCALIST, localist_source, NOW)
    for e in events:
        assert "expression" not in repr(e).lower()
        assert "photo" not in repr(e).lower()
        assert e.bronze_payload is not None
        bronze_text = json.dumps(e.bronze_payload).lower()
        assert "description" not in bronze_text
        assert "photo_url" not in bronze_text


def test_tribe_extracts_published_only(tribe_source):
    events = tribe.parse_tribe(TRIBE, tribe_source, NOW)
    titles = [e.title for e in events]
    assert "Draft Event Not Yet Published" not in titles
    assert len(events) == 2


def test_tribe_facts(tribe_source):
    events = tribe.parse_tribe(TRIBE, tribe_source, NOW)
    e = next(e for e in events if "Art Walk" in e.title)
    # HTML entities in titles are facts hygiene, not expression copying.
    assert e.title == "First Friday Art Walk & Night Market"
    assert e.starts_at == datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)
    assert e.ends_at == datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)
    assert e.venue.name == "RiNo Art District HQ"
    assert e.venue.street_address == "2901 Brighton Blvd"
    assert e.venue.city == "Denver"
    assert e.venue.latitude == pytest.approx(39.76012)
    assert e.source_organizer_ref == "RiNo Art District"
    assert e.category == "Arts"
    assert e.source_url.endswith("/event/first-friday-art-walk/")


def test_tribe_event_without_venue(tribe_source):
    events = tribe.parse_tribe(TRIBE, tribe_source, NOW)
    e = next(e for e in events if "Gallery Talk" in e.title)
    assert e.venue.name is None or e.venue.name == ""
    assert e.venue.latitude is None


def test_tribe_never_stores_description_or_image(tribe_source):
    events = tribe.parse_tribe(TRIBE, tribe_source, NOW)
    for e in events:
        assert "food trucks outside" not in repr(e)
        assert e.bronze_payload is not None
        bronze_text = json.dumps(e.bronze_payload).lower()
        assert "description" not in bronze_text
        assert "image" not in bronze_text
