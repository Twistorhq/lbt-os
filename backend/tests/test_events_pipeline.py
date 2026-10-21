"""TW-651: the ingest pipeline end to end (spec 10.3, standing
self-healing rule).

Pinned behavior: fingerprint dedupe merges provenance instead of
duplicating listings; display facts come from the most authoritative
pipe; an unchanged payload hash skips reprocessing but still counts
as seen; explicit cancellations and two-sync disappearances cancel
in place; a cancelled fingerprint is tombstoned for 90 days; one
poison item dead-letters without touching its neighbors; a failing
source trips its circuit breaker; a prohibited source is never
ingested; gold is rebuilt only through the provenance gate.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.social.events_index.models import SourceConfig
from app.social.events_index.pipeline import IngestPipeline
from app.social.events_index.store import MemoryStore

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"
DPL_ICS = (FIXTURES / "dpl_sample.ics").read_text()
TRIBE_JSON = (FIXTURES / "tribe_sample.json").read_text()
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

MARKET_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:markethall-1004@example.org
DTSTART;TZID=America/Denver:20261028T190000
DURATION:PT2H
SUMMARY:Neighborhood Night Market
LOCATION:Mercado Hall\\, 2901 Inca St\\, Denver\\, CO
GEO:39.75891;-104.99988
URL:https://example.org/events/night-market
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

MARKET_TRIBE = {
    "events": [
        {
            "id": 900,
            "title": "Neighborhood Night Market",
            "url": "https://thisisrinodenver.com/event/night-market/",
            "start_date": "2026-10-28 19:00:00",
            "end_date": "2026-10-28 21:00:00",
            "utc_start_date": "2026-10-29 01:00:00",
            "utc_end_date": "2026-10-29 03:00:00",
            "timezone": "America/Denver",
            "status": "publish",
            "description": "<p>Expression we do not copy.</p>",
            "image": {"url": "https://example.org/x.jpg"},
            "venue": {
                "venue": "Mercado Hall",
                "address": "2901 Inca St",
                "city": "Denver",
                "geo_lat": "39.75891",
                "geo_lng": "-104.99988",
            },
            "organizer": [{"id": 3, "organizer": "Mercado Hall"}],
            "categories": [{"id": 4, "name": "Market", "slug": "market"}],
        }
    ],
    "total": 1,
    "total_pages": 1,
}


def _source(**overrides) -> SourceConfig:
    base = dict(
        id="src-dpl",
        name="Denver Public Library",
        kind="ics",
        connector="ics_generic",
        terms_posture="index_facts",
        attribution_label="Denver Public Library",
        feed_url="https://denverlibrary.libcal.com/feed.ics",
        config={"default_timezone": "America/Denver", "full_sync": True},
    )
    base.update(overrides)
    return SourceConfig(**base)


def _pipeline(store, feeds):
    return IngestPipeline(store=store, fetchers=feeds, sleep=lambda s: None)


def test_full_ics_run_creates_listings():
    store = MemoryStore()
    store.add_source(_source())
    pipe = _pipeline(store, {"ics_generic": lambda s: DPL_ICS})
    report = pipe.run_source("src-dpl", NOW)
    assert report.status == "ok"
    assert report.created == 10  # every extracted instance is one listing
    listings = store.all_listings()
    assert len(listings) == report.created
    assert all(x.sources for x in listings)


def test_cancelled_event_in_feed_is_cancelled_listing():
    store = MemoryStore()
    store.add_source(_source())
    pipe = _pipeline(store, {"ics_generic": lambda s: DPL_ICS})
    pipe.run_source("src-dpl", NOW)
    cancelled = [x for x in store.all_listings() if x.status == "cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].title == "Evening Book Club"
    assert cancelled[0].tombstone_until is not None
    assert cancelled[0].tombstone_until - NOW >= timedelta(days=89)


def test_cross_source_dedupe_merges_provenance():
    store = MemoryStore()
    store.add_source(_source())
    store.add_source(
        _source(
            id="src-rino",
            name="RiNo Art District",
            kind="api",
            connector="tribe_events",
            attribution_label="RiNo Art District",
            feed_url="https://thisisrinodenver.com/wp-json/tribe/events/v1/events",
            config={"full_sync": True},
        )
    )
    feeds = {
        "ics_generic": lambda s: MARKET_ICS,
        "tribe_events": lambda s: json.dumps(MARKET_TRIBE),
    }
    pipe = _pipeline(store, feeds)
    pipe.run_source("src-dpl", NOW)
    pipe.run_source("src-rino", NOW)
    listings = store.all_listings()
    assert len(listings) == 1, "the same event from two pipes is one listing"
    listing = listings[0]
    assert len(listing.sources) == 2
    assert {r.source_id for r in listing.sources} == {"src-dpl", "src-rino"}


def test_display_facts_come_from_most_authoritative_pipe():
    store = MemoryStore()
    store.add_source(_source())
    store.add_source(
        _source(
            id="src-rino",
            name="RiNo",
            kind="api",
            connector="tribe_events",
            attribution_label="RiNo Art District",
            feed_url="https://example.org/tribe",
            config={"full_sync": True},
        )
    )
    tribe_variant = json.loads(json.dumps(MARKET_TRIBE))
    tribe_variant["events"][0]["title"] = "Neighborhood Night Market!"
    feeds = {
        "ics_generic": lambda s: MARKET_ICS,
        "tribe_events": lambda s: json.dumps(tribe_variant),
    }
    pipe = _pipeline(store, feeds)
    pipe.run_source("src-dpl", NOW)
    pipe.run_source("src-rino", NOW)
    listing = store.all_listings()[0]
    # API (authority 2) outranks the ICS feed (authority 3).
    assert listing.title == "Neighborhood Night Market!"


def test_unchanged_hash_skips_reprocessing_but_marks_seen():
    store = MemoryStore()
    store.add_source(_source())
    pipe = _pipeline(store, {"ics_generic": lambda s: DPL_ICS})
    first = pipe.run_source("src-dpl", NOW)
    later = NOW + timedelta(hours=6)
    second = pipe.run_source("src-dpl", later)
    assert second.created == 0
    assert second.unchanged == first.fetched
    for listing in store.all_listings():
        for row in listing.sources:
            assert row.last_seen_at == later


def test_changed_item_updates_listing_in_place():
    store = MemoryStore()
    store.add_source(_source())
    pipe = _pipeline(store, {"ics_generic": lambda s: MARKET_ICS})
    pipe.run_source("src-dpl", NOW)
    changed = MARKET_ICS.replace("DURATION:PT2H", "DURATION:PT3H")
    pipe2 = _pipeline(store, {"ics_generic": lambda s: changed})
    report = pipe2.run_source("src-dpl", NOW + timedelta(hours=6))
    assert report.updated >= 1
    listing = store.all_listings()[0]
    assert listing.ends_at - listing.starts_at == timedelta(hours=3)


def test_poison_item_dead_letters_and_run_continues():
    store = MemoryStore()
    store.add_source(_source())
    poisoned = DPL_ICS.replace(
        "END:VCALENDAR",
        "BEGIN:VEVENT\nUID:broken-9\nDTSTART:NOT-A-DATE\nSUMMARY:Broken\n"
        "END:VEVENT\nEND:VCALENDAR",
    )
    pipe = _pipeline(store, {"ics_generic": lambda s: poisoned})
    report = pipe.run_source("src-dpl", NOW)
    assert report.status == "partial"
    assert report.dead_lettered == 1
    assert len(store.dead_letters) == 1
    assert store.dead_letters[0]["source_id"] == "src-dpl"
    assert report.created == 10


def test_prohibited_source_is_never_ingested():
    store = MemoryStore()
    store.add_source(_source(terms_posture="prohibited"))
    calls = []

    def fetch(s):
        calls.append(1)
        return DPL_ICS

    pipe = _pipeline(store, {"ics_generic": fetch})
    report = pipe.run_source("src-dpl", NOW)
    assert report.status == "skipped_prohibited"
    assert calls == []
    assert store.all_listings() == []


def test_fetch_failure_retries_then_reports_failed():
    store = MemoryStore()
    store.add_source(_source())
    attempts = []

    def flaky(s):
        attempts.append(1)
        raise ConnectionError("503 service unavailable")

    pipe = _pipeline(store, {"ics_generic": flaky})
    report = pipe.run_source("src-dpl", NOW)
    assert report.status == "failed"
    assert len(attempts) == 3  # retry_with_backoff attempts
    assert store.get_source("src-dpl").consecutive_failures == 1


def test_circuit_breaker_opens_after_repeated_failures():
    store = MemoryStore()
    store.add_source(_source())
    calls = []

    def down(s):
        calls.append(1)
        raise ConnectionError("503 service unavailable")

    pipe = _pipeline(store, {"ics_generic": down})
    for i in range(3):
        pipe.run_source("src-dpl", NOW + timedelta(minutes=i))
    calls_after_failures = len(calls)
    report = pipe.run_source("src-dpl", NOW + timedelta(minutes=10))
    assert report.status == "circuit_open"
    assert len(calls) == calls_after_failures, "open circuit must not fetch"


def test_vanished_item_cancels_after_two_syncs():
    store = MemoryStore()
    store.add_source(_source())
    pipe = _pipeline(store, {"ics_generic": lambda s: MARKET_ICS})
    pipe.run_source("src-dpl", NOW)
    empty_feed = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"
    pipe2 = _pipeline(store, {"ics_generic": lambda s: empty_feed})
    pipe2.run_source("src-dpl", NOW + timedelta(hours=6))
    listing = store.all_listings()[0]
    assert listing.status == "active", "one missed sync is a flake, not a cancellation"
    pipe2.run_source("src-dpl", NOW + timedelta(hours=12))
    listing = store.all_listings()[0]
    assert listing.status == "cancelled"
    assert listing.tombstone_until is not None


TOMBSTONE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:tombstone-1@example.org
DTSTART;TZID=America/Denver:20261024T050000
DTEND;TZID=America/Denver:20261024T054500
RRULE:FREQ=DAILY;COUNT=100
SUMMARY:Daily Vigil
LOCATION:Confluence Park
URL:https://example.org/events/vigil
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""


def test_tombstone_blocks_resurrection():
    store = MemoryStore()
    store.add_source(_source())
    cancelled_ics = TOMBSTONE_ICS.replace("STATUS:CONFIRMED", "STATUS:CANCELLED")
    pipe = _pipeline(store, {"ics_generic": lambda s: cancelled_ics})
    pipe.run_source("src-dpl", NOW)
    # The instance 90 days out is inside run 1's window and tombstoned.
    far_start = datetime(2027, 1, 18, 12, 0, tzinfo=timezone.utc)
    far = next(x for x in store.all_listings() if x.starts_at == far_start)
    assert far.status == "cancelled"
    assert far.tombstone_until is not None
    # The flaky feed brings it back as confirmed a day later: still dead.
    pipe2 = _pipeline(store, {"ics_generic": lambda s: TOMBSTONE_ICS})
    pipe2.run_source("src-dpl", NOW + timedelta(days=1))
    far = next(x for x in store.all_listings() if x.starts_at == far_start)
    assert far.status == "cancelled"
    # Ninety-one days later the tombstone has expired; it may return.
    pipe2.run_source("src-dpl", NOW + timedelta(days=91))
    far = next(x for x in store.all_listings() if x.starts_at == far_start)
    assert far.status == "active"


def test_publish_builds_gold_only_through_the_gate():
    store = MemoryStore()
    store.add_source(_source())
    pipe = _pipeline(store, {"ics_generic": lambda s: DPL_ICS})
    pipe.run_source("src-dpl", NOW)
    result = pipe.publish(NOW)
    assert result["published"] >= 8
    gold = store.gold_rows()
    assert gold, "gold must not be empty after a clean run"
    storytime = next(g for g in gold if g["title"] == "Preschool Storytime")
    assert "Denver Public Library" in storytime["attribution_text"]
    assert storytime["primary_source_url"].endswith("/event/1001")
    # The cancelled book club never reaches gold.
    assert all(g["title"] != "Evening Book Club" for g in gold)
    # Every publish decision left a log line.
    assert store.publish_log


def test_publish_removes_cancelled_from_gold():
    store = MemoryStore()
    store.add_source(_source())
    pipe = _pipeline(store, {"ics_generic": lambda s: MARKET_ICS})
    pipe.run_source("src-dpl", NOW)
    pipe.publish(NOW)
    assert len(store.gold_rows()) == 1
    cancelled = MARKET_ICS.replace("STATUS:CONFIRMED", "STATUS:CANCELLED")
    pipe2 = _pipeline(store, {"ics_generic": lambda s: cancelled})
    pipe2.run_source("src-dpl", NOW + timedelta(hours=6))
    pipe2.publish(NOW + timedelta(hours=6))
    assert store.gold_rows() == []


# ------------------------------------------------------------------
# Fix round (Rosa TW-651 review): m1 vanish freshness, m2 fingerprint
# pinning, m3 key-only dead letters.
# ------------------------------------------------------------------

EMPTY_ICS = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"


def _tribe_market_source(**overrides) -> SourceConfig:
    base = dict(
        id="src-rino",
        name="RiNo Art District",
        kind="api",
        connector="tribe_events",
        terms_posture="index_facts",
        attribution_label="RiNo Art District",
        feed_url="https://thisisrinodenver.com/wp-json/tribe/events/v1/events",
        config={},
    )
    base.update(overrides)
    return SourceConfig(**base)


def test_non_full_sync_co_carrier_does_not_suppress_vanish_cancellation():
    # m1: the Tribe co-carrier is NOT full-sync, so it never maintains
    # missed_syncs. Its staleness must be judged by last_seen_at, or
    # its mere presence suppresses the vanish cancellation forever.
    store = MemoryStore()
    store.add_source(_source())  # ICS, full_sync
    store.add_source(_tribe_market_source())  # not full-sync
    feeds = {
        "ics_generic": lambda s: MARKET_ICS,
        "tribe_events": lambda s: json.dumps(MARKET_TRIBE),
    }
    pipe = _pipeline(store, feeds)
    pipe.run_source("src-dpl", NOW)
    pipe.run_source("src-rino", NOW)
    assert len(store.all_listings()) == 1
    empty = _pipeline(store, {"ics_generic": lambda s: EMPTY_ICS})
    empty.run_source("src-dpl", NOW + timedelta(days=8))
    listing = store.all_listings()[0]
    assert listing.status == "active", "one missed sync is still a flake"
    empty.run_source("src-dpl", NOW + timedelta(days=8, hours=6))
    listing = store.all_listings()[0]
    assert listing.status == "cancelled"
    assert listing.tombstone_until is not None


def test_co_carrier_suppression_follows_last_seen_freshness():
    # m1, the other direction: while the co-carrier IS fresh under
    # the gate ceiling it still suppresses the cancellation; once its
    # last_seen_at ages past the ceiling, it stops suppressing.
    store = MemoryStore()
    store.add_source(_source())
    store.add_source(_tribe_market_source())
    feeds = {
        "ics_generic": lambda s: MARKET_ICS,
        "tribe_events": lambda s: json.dumps(MARKET_TRIBE),
    }
    pipe = _pipeline(store, feeds)
    pipe.run_source("src-dpl", NOW)
    pipe.run_source("src-rino", NOW)
    empty = _pipeline(store, {"ics_generic": lambda s: EMPTY_ICS})
    empty.run_source("src-dpl", NOW + timedelta(hours=6))
    pipe.run_source("src-rino", NOW + timedelta(hours=11))  # co-carrier re-seen
    empty.run_source("src-dpl", NOW + timedelta(hours=12))
    listing = store.all_listings()[0]
    assert listing.status == "active", "a fresh co-carrier still carries it"
    empty.run_source("src-dpl", NOW + timedelta(days=8))
    listing = store.all_listings()[0]
    assert listing.status == "cancelled"


def test_lower_authority_variant_does_not_move_fingerprint():
    # m2: the ICS pipe re-spells the venue (no GEO, longer street
    # name), which computes a different fingerprint variant. Its
    # facts are not taken (the API pipe is the display source), so
    # the listing's fingerprint must stay pinned to the display
    # source's variant instead of oscillating between the two.
    store = MemoryStore()
    store.add_source(_tribe_market_source())
    store.add_source(_source(config={"default_timezone": "America/Denver"}))
    pipe = _pipeline(store, {"tribe_events": lambda s: json.dumps(MARKET_TRIBE)})
    pipe.run_source("src-rino", NOW)
    pipe2 = _pipeline(store, {"ics_generic": lambda s: MARKET_ICS})
    pipe2.run_source("src-dpl", NOW)
    listings = store.all_listings()
    assert len(listings) == 1
    pinned = listings[0].fingerprint
    pinned_venue_id = listings[0].venue_id
    variant = MARKET_ICS.replace(
        "LOCATION:Mercado Hall\\, 2901 Inca St\\, Denver\\, CO\n"
        "GEO:39.75891;-104.99988\n",
        "LOCATION:Mercado Hall\\, 2901 Inca Street\\, Denver\\, Colorado\n",
    )
    assert variant != MARKET_ICS
    pipe3 = _pipeline(store, {"ics_generic": lambda s: variant})
    pipe3.run_source("src-dpl", NOW + timedelta(hours=6))
    listings = store.all_listings()
    assert len(listings) == 1
    assert listings[0].fingerprint == pinned
    assert listings[0].venue_id == pinned_venue_id


def test_dead_letter_payload_is_key_only_for_feed_items():
    # m3: a poison JSON item whose early keys carry never-store
    # content must dead-letter as its item key and nothing else; the
    # persisted payload can never contain item content.
    poison = {
        "id": "poison-7",
        "description": "quixotic-lynx-marker expression that must not persist",
        "title": "Broken Event",
        "status": "publish",
    }
    feed = {"events": [poison, MARKET_TRIBE["events"][0]]}
    store = MemoryStore()
    store.add_source(_tribe_market_source())
    pipe = _pipeline(store, {"tribe_events": lambda s: json.dumps(feed)})
    report = pipe.run_source("src-rino", NOW)
    assert report.dead_lettered == 1
    assert len(store.dead_letters) == 1
    letter = store.dead_letters[0]
    assert letter["source_item_key"] == "poison-7"
    payload_text = str(letter["payload"])
    assert "poison-7" in payload_text
    assert "quixotic-lynx-marker" not in payload_text
    assert "description" not in payload_text
    # The run's persisted DLQ summary carries the same key-only shape.
    assert "quixotic-lynx-marker" not in json.dumps(store.runs, default=str)


def test_organizer_only_ingests_but_never_publishes():
    store = MemoryStore()
    store.add_source(_source(terms_posture="organizer_only"))
    pipe = _pipeline(store, {"ics_generic": lambda s: MARKET_ICS})
    report = pipe.run_source("src-dpl", NOW)
    assert report.created == 1
    result = pipe.publish(NOW)
    assert result["published"] == 0
    assert store.gold_rows() == []
    reasons = {line["reason"] for line in store.publish_log}
    assert "no_indexable_source" in reasons
