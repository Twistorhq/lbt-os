"""TW-651: storage for the events pipeline.

The pipeline talks to an EventsStore protocol so its logic (dedupe,
freshness, cancellation, the gate) is testable without a database.
MemoryStore is the test and local-dev implementation; SupabaseStore
is the production one, writing the evt_ tables from
migration_events_v1.sql through the service role (every evt_ table
is RLS deny-all to anything else, gold excepted by design).

Venue records are plain dicts at this boundary: {id, name,
street_address, city, neighborhood, latitude, longitude,
geocode_provider, geocoded_at, address_fingerprint}.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from .models import ListingRecord, ListingSourceRecord, SourceConfig, VenueFacts


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class EventsStore(Protocol):
    def add_source(self, source: SourceConfig) -> None: ...
    def get_source(self, source_id: str) -> SourceConfig | None: ...
    def all_sources(self) -> list[SourceConfig]: ...
    def save_source(self, source: SourceConfig) -> None: ...
    def raw_hash(self, source_id: str, item_key: str) -> str | None: ...
    def put_raw(
        self,
        source_id: str,
        item_key: str,
        payload: Any,
        payload_hash: str,
        fetched_at: datetime,
    ) -> None: ...
    def find_venue_by_fingerprint(self, address_fingerprint: str) -> dict | None: ...
    def upsert_venue(
        self,
        facts: VenueFacts,
        address_fingerprint: str,
        geocode_provider: str | None,
        geocoded_at: datetime | None,
    ) -> str: ...
    def get_venue(self, venue_id: str) -> dict | None: ...
    def find_listing_by_fingerprint(self, fingerprint: str) -> ListingRecord | None: ...
    def find_listing_by_source_key(
        self, source_id: str, item_key: str
    ) -> ListingRecord | None: ...
    def create_listing(self, listing: ListingRecord) -> ListingRecord: ...
    def update_listing(self, listing: ListingRecord) -> None: ...
    def upsert_listing_source(
        self, listing_id: str, row: ListingSourceRecord
    ) -> None: ...
    def source_listing_rows(
        self, source_id: str
    ) -> list[tuple[ListingRecord, ListingSourceRecord]]: ...
    def all_listings(self) -> list[ListingRecord]: ...
    def start_run(self, source_id: str, started_at: datetime) -> str: ...
    def finish_run(
        self, run_id: str, status: str, counts: dict, detail: dict
    ) -> None: ...
    def add_dead_letter(
        self,
        source_id: str,
        run_id: str | None,
        item_key: str | None,
        payload: Any,
        payload_hash: str | None,
        error_class: str,
        error_detail: str,
    ) -> None: ...
    def gold_put(self, listing_id: str, row: dict) -> None: ...
    def gold_remove(self, listing_id: str) -> None: ...
    def gold_get(self, listing_id: str) -> dict | None: ...
    def gold_rows(self) -> list[dict]: ...
    def add_publish_log(self, listing_id: str, action: str, reason: str) -> None: ...


class MemoryStore:
    """In-memory EventsStore. Canonical state lives here in tests."""

    def __init__(self):
        self.sources: dict[str, SourceConfig] = {}
        self.raw_items: dict[tuple[str, str, str], dict] = {}
        self.venues: dict[str, dict] = {}
        self.listings: dict[str, ListingRecord] = {}
        self.runs: dict[str, dict] = {}
        self.dead_letters: list[dict] = []
        self.gold: dict[str, dict] = {}
        self.publish_log: list[dict] = []

    # -- sources ---------------------------------------------------
    def add_source(self, source: SourceConfig) -> None:
        self.sources[source.id] = source

    def get_source(self, source_id: str) -> SourceConfig | None:
        return self.sources.get(source_id)

    def all_sources(self) -> list[SourceConfig]:
        return list(self.sources.values())

    def save_source(self, source: SourceConfig) -> None:
        self.sources[source.id] = source

    # -- bronze ------------------------------------------------------
    def raw_hash(self, source_id: str, item_key: str) -> str | None:
        hashes = [
            row["payload_hash"]
            for (sid, key, _), row in self.raw_items.items()
            if sid == source_id and key == item_key
        ]
        return hashes[-1] if hashes else None

    def put_raw(self, source_id, item_key, payload, payload_hash, fetched_at) -> None:
        self.raw_items[(source_id, item_key, payload_hash)] = {
            "source_id": source_id,
            "source_item_key": item_key,
            "payload": payload,
            "payload_hash": payload_hash,
            "fetched_at": fetched_at,
        }

    # -- venues ------------------------------------------------------
    def find_venue_by_fingerprint(self, address_fingerprint: str) -> dict | None:
        for venue in self.venues.values():
            if venue["address_fingerprint"] == address_fingerprint:
                return venue
        return None

    def upsert_venue(
        self, facts, address_fingerprint, geocode_provider, geocoded_at
    ) -> str:
        existing = self.find_venue_by_fingerprint(address_fingerprint)
        if existing is not None:
            if existing["latitude"] is None and facts.latitude is not None:
                existing["latitude"] = facts.latitude
                existing["longitude"] = facts.longitude
                existing["geocode_provider"] = geocode_provider
                existing["geocoded_at"] = geocoded_at
            return existing["id"]
        venue_id = _new_id("ven")
        self.venues[venue_id] = {
            "id": venue_id,
            "name": facts.name,
            "street_address": facts.street_address,
            "city": facts.city,
            "neighborhood": facts.neighborhood,
            "latitude": facts.latitude,
            "longitude": facts.longitude,
            "geocode_provider": geocode_provider,
            "geocoded_at": geocoded_at,
            "address_fingerprint": address_fingerprint,
        }
        return venue_id

    def get_venue(self, venue_id: str) -> dict | None:
        return self.venues.get(venue_id)

    # -- listings ----------------------------------------------------
    def find_listing_by_fingerprint(self, fingerprint: str) -> ListingRecord | None:
        for listing in self.listings.values():
            if listing.fingerprint == fingerprint:
                return listing
        return None

    def find_listing_by_source_key(
        self, source_id: str, item_key: str
    ) -> ListingRecord | None:
        for listing in self.listings.values():
            for row in listing.sources:
                if row.source_id == source_id and row.source_item_key == item_key:
                    return listing
        return None

    def create_listing(self, listing: ListingRecord) -> ListingRecord:
        if not listing.id:
            listing.id = _new_id("lst")
        self.listings[listing.id] = listing
        return listing

    def update_listing(self, listing: ListingRecord) -> None:
        self.listings[listing.id] = listing

    def upsert_listing_source(self, listing_id: str, row: ListingSourceRecord) -> None:
        listing = self.listings[listing_id]
        for i, existing in enumerate(listing.sources):
            if existing.source_id == row.source_id:
                listing.sources[i] = row
                return
        listing.sources.append(row)

    def source_listing_rows(self, source_id: str):
        out = []
        for listing in self.listings.values():
            for row in listing.sources:
                if row.source_id == source_id:
                    out.append((listing, row))
        return out

    def all_listings(self) -> list[ListingRecord]:
        return list(self.listings.values())

    # -- ops ---------------------------------------------------------
    def start_run(self, source_id: str, started_at: datetime) -> str:
        run_id = _new_id("run")
        self.runs[run_id] = {
            "id": run_id,
            "source_id": source_id,
            "started_at": started_at,
            "status": "running",
        }
        return run_id

    def finish_run(self, run_id: str, status: str, counts: dict, detail: dict) -> None:
        self.runs[run_id].update({"status": status, **counts, "detail": detail})

    def add_dead_letter(
        self,
        source_id,
        run_id,
        item_key,
        payload,
        payload_hash,
        error_class,
        error_detail,
    ) -> None:
        self.dead_letters.append(
            {
                "source_id": source_id,
                "ingest_run_id": run_id,
                "source_item_key": item_key,
                "payload": payload,
                "payload_hash": payload_hash,
                "error_class": error_class,
                "error_detail": error_detail,
                "status": "pending",
            }
        )

    # -- gold --------------------------------------------------------
    def gold_put(self, listing_id: str, row: dict) -> None:
        self.gold[listing_id] = copy.deepcopy(row)

    def gold_remove(self, listing_id: str) -> None:
        self.gold.pop(listing_id, None)

    def gold_get(self, listing_id: str) -> dict | None:
        return self.gold.get(listing_id)

    def gold_rows(self) -> list[dict]:
        return list(self.gold.values())

    def add_publish_log(self, listing_id: str, action: str, reason: str) -> None:
        self.publish_log.append(
            {"listing_id": listing_id, "action": action, "reason": reason}
        )


class SupabaseStore:
    """Production EventsStore over the Supabase service-role client.

    Mirrors MemoryStore semantics against the evt_ tables. Assembly
    of ListingRecord values joins evt_listing_sources in Python; at
    Denver launch volume (hundreds of listings per source run) that
    is a deliberate simplicity-over-cleverness trade.
    """

    def __init__(self, db):
        self.db = db

    # -- sources ---------------------------------------------------
    def add_source(self, source: SourceConfig) -> None:
        self.db.table("evt_sources").insert(self._source_row(source)).execute()

    def get_source(self, source_id: str) -> SourceConfig | None:
        rows = (
            self.db.table("evt_sources").select("*").eq("id", source_id).execute().data
        )
        return self._source_from_row(rows[0]) if rows else None

    def all_sources(self) -> list[SourceConfig]:
        rows = self.db.table("evt_sources").select("*").execute().data
        return [self._source_from_row(r) for r in rows]

    def save_source(self, source: SourceConfig) -> None:
        self.db.table("evt_sources").update(
            {
                "consecutive_failures": source.consecutive_failures,
                "circuit_open_until": _ts(source.circuit_open_until),
                "last_polled_at": _ts(source.last_polled_at),
                "last_success_at": _ts(source.last_success_at),
            }
        ).eq("id", source.id).execute()

    @staticmethod
    def _source_row(source: SourceConfig) -> dict:
        return {
            "id": source.id,
            "name": source.name,
            "kind": source.kind,
            "connector": source.connector,
            "terms_posture": source.terms_posture,
            "attribution_label": source.attribution_label,
            "feed_url": source.feed_url,
            "config": source.config,
            "poll_interval_seconds": source.poll_interval_seconds,
            "active": source.active,
        }

    @staticmethod
    def _source_from_row(row: dict) -> SourceConfig:
        return SourceConfig(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            connector=row["connector"],
            terms_posture=row["terms_posture"],
            attribution_label=row["attribution_label"],
            feed_url=row.get("feed_url"),
            config=row.get("config") or {},
            poll_interval_seconds=row.get("poll_interval_seconds", 21600),
            active=row.get("active", True),
            consecutive_failures=row.get("consecutive_failures", 0),
            circuit_open_until=_parse_ts(row.get("circuit_open_until")),
            last_polled_at=_parse_ts(row.get("last_polled_at")),
            last_success_at=_parse_ts(row.get("last_success_at")),
        )

    # -- bronze ------------------------------------------------------
    def raw_hash(self, source_id: str, item_key: str) -> str | None:
        rows = (
            self.db.table("evt_raw_items")
            .select("payload_hash")
            .eq("source_id", source_id)
            .eq("source_item_key", item_key)
            .order("fetched_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0]["payload_hash"] if rows else None

    def put_raw(self, source_id, item_key, payload, payload_hash, fetched_at) -> None:
        self.db.table("evt_raw_items").upsert(
            {
                "source_id": source_id,
                "source_item_key": item_key,
                "payload": payload
                if not isinstance(payload, str)
                else {"text": payload},
                "payload_hash": payload_hash,
                "fetched_at": _ts(fetched_at),
            },
            on_conflict="source_id,source_item_key,payload_hash",
        ).execute()

    # -- venues ------------------------------------------------------
    def find_venue_by_fingerprint(self, address_fingerprint: str) -> dict | None:
        rows = (
            self.db.table("evt_venues")
            .select("*")
            .eq("address_fingerprint", address_fingerprint)
            .execute()
            .data
        )
        return rows[0] if rows else None

    def upsert_venue(
        self, facts, address_fingerprint, geocode_provider, geocoded_at
    ) -> str:
        existing = self.find_venue_by_fingerprint(address_fingerprint)
        if existing is not None:
            if existing["latitude"] is None and facts.latitude is not None:
                self.db.table("evt_venues").update(
                    {
                        "latitude": facts.latitude,
                        "longitude": facts.longitude,
                        "geocode_provider": geocode_provider,
                        "geocoded_at": _ts(geocoded_at),
                    }
                ).eq("id", existing["id"]).execute()
            return existing["id"]
        rows = (
            self.db.table("evt_venues")
            .insert(
                {
                    "name": facts.name,
                    "street_address": facts.street_address,
                    "city": facts.city,
                    "neighborhood": facts.neighborhood,
                    "latitude": facts.latitude,
                    "longitude": facts.longitude,
                    "geocode_provider": geocode_provider,
                    "geocoded_at": _ts(geocoded_at),
                    "address_fingerprint": address_fingerprint,
                }
            )
            .execute()
            .data
        )
        return rows[0]["id"]

    def get_venue(self, venue_id: str) -> dict | None:
        rows = self.db.table("evt_venues").select("*").eq("id", venue_id).execute().data
        return rows[0] if rows else None

    # -- listings ----------------------------------------------------
    def _assemble(self, row: dict) -> ListingRecord:
        source_rows = (
            self.db.table("evt_listing_sources")
            .select("*")
            .eq("listing_id", row["id"])
            .execute()
            .data
        )
        return ListingRecord(
            id=row["id"],
            fingerprint=row["fingerprint"],
            title=row["title"],
            starts_at=_parse_ts(row["starts_at"]),
            ends_at=_parse_ts(row["ends_at"]),
            venue_id=row["venue_id"],
            category=row.get("category"),
            min_age_band=row.get("min_age_band"),
            rating_source=row.get("rating_source"),
            rated_at=_parse_ts(row.get("rated_at")),
            status=row["status"],
            claimed_group_id=row.get("claimed_group_id"),
            display_source_id=row.get("display_source_id"),
            cancelled_at=_parse_ts(row.get("cancelled_at")),
            tombstone_until=_parse_ts(row.get("tombstone_until")),
            first_seen_at=_parse_ts(row.get("first_seen_at")),
            updated_at=_parse_ts(row.get("updated_at")),
            sources=[
                ListingSourceRecord(
                    source_id=r["source_id"],
                    source_item_key=r["source_item_key"],
                    source_url=r.get("source_url"),
                    source_organizer_ref=r.get("source_organizer_ref"),
                    authority=r["authority"],
                    fetched_at=_parse_ts(r["fetched_at"]),
                    last_seen_at=_parse_ts(r["last_seen_at"]),
                    missed_syncs=r.get("missed_syncs", 0),
                )
                for r in source_rows
            ],
        )

    def _listing_row(self, listing: ListingRecord) -> dict:
        return {
            "fingerprint": listing.fingerprint,
            "title": listing.title,
            "starts_at": _ts(listing.starts_at),
            "ends_at": _ts(listing.ends_at),
            "venue_id": listing.venue_id,
            "category": listing.category,
            "min_age_band": listing.min_age_band,
            "rating_source": listing.rating_source,
            "rated_at": _ts(listing.rated_at),
            "status": listing.status,
            "claimed_group_id": listing.claimed_group_id,
            "display_source_id": listing.display_source_id,
            "cancelled_at": _ts(listing.cancelled_at),
            "tombstone_until": _ts(listing.tombstone_until),
        }

    def find_listing_by_fingerprint(self, fingerprint: str) -> ListingRecord | None:
        rows = (
            self.db.table("evt_listings")
            .select("*")
            .eq("fingerprint", fingerprint)
            .execute()
            .data
        )
        return self._assemble(rows[0]) if rows else None

    def find_listing_by_source_key(
        self, source_id: str, item_key: str
    ) -> ListingRecord | None:
        rows = (
            self.db.table("evt_listing_sources")
            .select("listing_id")
            .eq("source_id", source_id)
            .eq("source_item_key", item_key)
            .execute()
            .data
        )
        if not rows:
            return None
        listing_rows = (
            self.db.table("evt_listings")
            .select("*")
            .eq("id", rows[0]["listing_id"])
            .execute()
            .data
        )
        return self._assemble(listing_rows[0]) if listing_rows else None

    def create_listing(self, listing: ListingRecord) -> ListingRecord:
        rows = (
            self.db.table("evt_listings")
            .insert(self._listing_row(listing))
            .execute()
            .data
        )
        listing.id = rows[0]["id"]
        return listing

    def update_listing(self, listing: ListingRecord) -> None:
        self.db.table("evt_listings").update(self._listing_row(listing)).eq(
            "id", listing.id
        ).execute()

    def upsert_listing_source(self, listing_id: str, row: ListingSourceRecord) -> None:
        self.db.table("evt_listing_sources").upsert(
            {
                "listing_id": listing_id,
                "source_id": row.source_id,
                "source_item_key": row.source_item_key,
                "source_url": row.source_url,
                "source_organizer_ref": row.source_organizer_ref,
                "authority": row.authority,
                "fetched_at": _ts(row.fetched_at),
                "last_seen_at": _ts(row.last_seen_at),
                "missed_syncs": row.missed_syncs,
            },
            on_conflict="listing_id,source_id",
        ).execute()

    def source_listing_rows(self, source_id: str):
        rows = (
            self.db.table("evt_listing_sources")
            .select("*")
            .eq("source_id", source_id)
            .execute()
            .data
        )
        out = []
        for r in rows:
            listing = self.find_listing_by_fingerprint(
                self._listing_fingerprint(r["listing_id"])
            )
            if listing is not None:
                out.append(
                    (
                        listing,
                        next(s for s in listing.sources if s.source_id == source_id),
                    )
                )
        return out

    def _listing_fingerprint(self, listing_id: str) -> str:
        rows = (
            self.db.table("evt_listings")
            .select("fingerprint")
            .eq("id", listing_id)
            .execute()
            .data
        )
        return rows[0]["fingerprint"]

    def all_listings(self) -> list[ListingRecord]:
        rows = self.db.table("evt_listings").select("*").execute().data
        return [self._assemble(r) for r in rows]

    # -- ops ---------------------------------------------------------
    def start_run(self, source_id: str, started_at: datetime) -> str:
        rows = (
            self.db.table("evt_ingest_runs")
            .insert({"source_id": source_id, "started_at": _ts(started_at)})
            .execute()
            .data
        )
        return rows[0]["id"]

    def finish_run(self, run_id: str, status: str, counts: dict, detail: dict) -> None:
        self.db.table("evt_ingest_runs").update(
            {
                "status": status,
                "finished_at": _ts(datetime.now(timezone.utc)),
                **counts,
                "detail": detail,
            }
        ).eq("id", run_id).execute()

    def add_dead_letter(
        self,
        source_id,
        run_id,
        item_key,
        payload,
        payload_hash,
        error_class,
        error_detail,
    ) -> None:
        self.db.table("evt_dead_letters").insert(
            {
                "source_id": source_id,
                "ingest_run_id": run_id,
                "source_item_key": item_key,
                "payload": payload
                if not isinstance(payload, str)
                else {"text": payload},
                "payload_hash": payload_hash,
                "error_class": error_class,
                "error_detail": error_detail,
            }
        ).execute()

    # -- gold --------------------------------------------------------
    def gold_put(self, listing_id: str, row: dict) -> None:
        self.db.table("evt_gold_listings").upsert(
            {"listing_id": listing_id, **{k: _ts(v) for k, v in row.items()}},
            on_conflict="listing_id",
        ).execute()

    def gold_remove(self, listing_id: str) -> None:
        self.db.table("evt_gold_listings").delete().eq(
            "listing_id", listing_id
        ).execute()

    def gold_get(self, listing_id: str) -> dict | None:
        rows = (
            self.db.table("evt_gold_listings")
            .select("*")
            .eq("listing_id", listing_id)
            .execute()
            .data
        )
        return rows[0] if rows else None

    def gold_rows(self) -> list[dict]:
        return self.db.table("evt_gold_listings").select("*").execute().data

    def add_publish_log(self, listing_id: str, action: str, reason: str) -> None:
        self.db.table("evt_publish_log").insert(
            {"listing_id": listing_id, "action": action, "reason": reason}
        ).execute()


def _ts(value):
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


def _parse_ts(value):
    """Parse a PostgREST timestamp into an aware datetime.

    Read rows come back with string timestamps, and the pipeline
    and gate do arithmetic on the assembled dataclasses; passing
    the strings through raises TypeError at the first comparison
    (Rosa call 10). Naive strings are read as UTC: every writer in
    this layer stamps aware UTC values.
    """
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
