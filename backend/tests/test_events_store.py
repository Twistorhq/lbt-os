"""TW-651 fix round (Rosa review, design call 10 + nit n2): the
SupabaseStore wiring contract.

PostgREST hands timestamps back as strings. The pipeline and the
gate do arithmetic on the dataclasses the store assembles, so
assembly must parse those strings into aware datetimes, and the
store's own writes must serialize datetimes instead of passing raw
objects the driver cannot place. Proven here against a fake client:
no live Supabase needed for the contract to hold.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.social.events_index import gate
from app.social.events_index.models import SourceConfig
from app.social.events_index.pipeline import circuit_state
from app.social.events_index.store import SupabaseStore

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


class _FakeQuery:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._filters: dict = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def insert(self, row):
        self._db.writes.append((self._table, "insert", row))
        return self

    def update(self, row):
        self._db.writes.append((self._table, "update", row))
        return self

    def upsert(self, row, on_conflict=None):
        self._db.writes.append((self._table, "upsert", row))
        return self

    def delete(self):
        return self

    def execute(self):
        rows = [
            r
            for r in self._db.tables.get(self._table, [])
            if all(r.get(k) == v for k, v in self._filters.items())
        ]
        return SimpleNamespace(data=[dict(r) for r in rows])


class _FakeDb:
    def __init__(self, tables):
        self.tables = tables
        self.writes: list = []

    def table(self, name):
        return _FakeQuery(self, name)


def _listing_tables():
    return {
        "evt_listings": [
            {
                "id": "lst-1",
                "fingerprint": "fp-1",
                "title": "Wired Event",
                "starts_at": "2026-10-29T01:00:00+00:00",
                "ends_at": "2026-10-29T03:00:00Z",
                "venue_id": "ven-1",
                "category": "Market",
                "min_age_band": None,
                "rating_source": None,
                "rated_at": None,
                "status": "active",
                "claimed_group_id": None,
                "display_source_id": "src-api",
                "cancelled_at": None,
                "tombstone_until": None,
                "first_seen_at": "2026-10-20T12:00:00+00:00",
                "updated_at": "2026-10-20T12:00:00+00:00",
            }
        ],
        "evt_listing_sources": [
            {
                "listing_id": "lst-1",
                "source_id": "src-api",
                "source_item_key": "900",
                "source_url": "https://example.org/e/900",
                "source_organizer_ref": None,
                "authority": 2,
                "fetched_at": "2026-10-20T11:00:00+00:00",
                "last_seen_at": "2026-10-20T11:30:00+00:00",
                "missed_syncs": 0,
            }
        ],
    }


def test_supabase_assembled_timestamps_are_typed_and_aware():
    store = SupabaseStore(_FakeDb(_listing_tables()))
    listing = store.all_listings()[0]
    for value in (
        listing.starts_at,
        listing.ends_at,
        listing.first_seen_at,
        listing.updated_at,
        listing.sources[0].fetched_at,
        listing.sources[0].last_seen_at,
    ):
        assert isinstance(value, datetime)
        assert value.tzinfo is not None
    # The gate's freshness arithmetic runs on the assembled values
    # without a TypeError, and the fresh listing passes.
    source = SourceConfig(
        id="src-api",
        name="API",
        kind="api",
        connector="tribe_events",
        terms_posture="index_facts",
        attribution_label="API",
    )
    decision = gate.gate_decision(listing, listing.sources, {"src-api": source}, NOW)
    assert decision.publish


def test_supabase_source_timestamps_are_typed_and_aware():
    tables = {
        "evt_sources": [
            {
                "id": "src-1",
                "name": "S",
                "kind": "api",
                "connector": "tribe_events",
                "terms_posture": "index_facts",
                "attribution_label": "S",
                "feed_url": None,
                "config": {},
                "poll_interval_seconds": 21600,
                "active": True,
                "consecutive_failures": 3,
                "circuit_open_until": "2026-10-20T12:30:00+00:00",
                "last_polled_at": "2026-10-20T11:00:00+00:00",
                "last_success_at": "2026-10-20T11:00:00Z",
            }
        ]
    }
    store = SupabaseStore(_FakeDb(tables))
    source = store.get_source("src-1")
    assert source is not None
    assert isinstance(source.circuit_open_until, datetime)
    assert source.circuit_open_until.tzinfo is not None
    # circuit_state compares the value against an aware now.
    assert circuit_state(source, NOW) == "open"
    assert circuit_state(source, NOW + timedelta(hours=1)) != "open"


def test_supabase_finish_run_stamps_aware_utc():
    db = _FakeDb({})
    store = SupabaseStore(db)
    store.finish_run("run-1", "ok", {"items_fetched": 1}, {})
    updates = [row for table, op, row in db.writes if table == "evt_ingest_runs"]
    assert len(updates) == 1
    stamped = datetime.fromisoformat(updates[0]["finished_at"])
    assert stamped.tzinfo is not None
    lag = abs(datetime.now(timezone.utc) - stamped)
    assert lag < timedelta(minutes=1)


def test_supabase_gold_put_serializes_datetimes():
    db = _FakeDb({})
    store = SupabaseStore(db)
    store.gold_put(
        "lst-1",
        {
            "title": "Wired Event",
            "starts_at": datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc),
            "ends_at": None,
            "last_seen_at": datetime(2026, 10, 20, 11, 30, tzinfo=timezone.utc),
        },
    )
    upserts = [row for table, op, row in db.writes if table == "evt_gold_listings"]
    assert len(upserts) == 1
    row = upserts[0]
    assert row["starts_at"] == "2026-10-29T01:00:00+00:00"
    assert row["last_seen_at"] == "2026-10-20T11:30:00+00:00"
    assert row["ends_at"] is None
