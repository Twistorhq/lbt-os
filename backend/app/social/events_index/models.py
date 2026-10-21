"""TW-651 Twistor Social events data layer: shared data shapes.

The layer is an INDEX, not a copy (spec Section 10.1). Every shape
here carries facts and provenance only. There is deliberately no
description, image, price, or contact field anywhere in this module:
spec 10.6's never-store list is enforced by the shape of the data,
not by reviewer vigilance.

Medallion flow: connectors land redacted payloads in bronze
(evt_raw_items), extraction produces ExtractedEvent facts for silver
(evt_venues, evt_listings, evt_listing_sources), and the publish step
promotes silver to gold (evt_gold_listings) only through the
fail-closed provenance gate in gate.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SOURCE_KINDS = ("ics", "api", "organizer_import")
TERMS_POSTURES = ("index_facts", "organizer_only", "prohibited")
LISTING_STATUSES = ("active", "cancelled", "stale", "claimed")
AGE_BANDS = ("age_16_plus", "age_18_plus", "age_21_plus")

# Display authority by pipe (spec 10.3): when sources disagree, facts
# come from the most authoritative pipe present. Lower wins.
AUTHORITY_BY_KIND = {"organizer_import": 1, "api": 2, "ics": 3}


@dataclass
class SourceConfig:
    """One configured pipe (a row of evt_sources)."""

    id: str
    name: str
    kind: str  # ics | api | organizer_import
    connector: str  # ics_generic | localist | tribe_events | ...
    terms_posture: str  # index_facts | organizer_only | prohibited
    attribution_label: str
    feed_url: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    poll_interval_seconds: int = 21600
    active: bool = True
    consecutive_failures: int = 0
    circuit_open_until: datetime | None = None
    last_polled_at: datetime | None = None
    last_success_at: datetime | None = None

    @property
    def authority(self) -> int:
        return AUTHORITY_BY_KIND.get(self.kind, 3)


@dataclass
class VenueFacts:
    """Venue facts as published by a source (never organizer PII)."""

    name: str | None = None
    street_address: str | None = None
    city: str | None = None
    neighborhood: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class ExtractedEvent:
    """One event instance extracted from a source item, facts only."""

    source_item_key: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    venue: VenueFacts
    category: str | None = None
    source_url: str | None = None
    source_organizer_ref: str | None = None
    status: str = "active"  # active | cancelled
    payload_hash: str = ""
    bronze_payload: Any = None  # redacted raw item, bronze only


@dataclass
class ListingSourceRecord:
    """One source carrying a listing (a row of evt_listing_sources)."""

    source_id: str
    source_item_key: str
    source_url: str | None
    source_organizer_ref: str | None
    authority: int
    fetched_at: datetime
    last_seen_at: datetime
    missed_syncs: int = 0


@dataclass
class ListingRecord:
    """A silver listing (a row of evt_listings) with its provenance."""

    id: str
    fingerprint: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    venue_id: str | None
    category: str | None = None
    min_age_band: str | None = None
    rating_source: str | None = None
    rated_at: datetime | None = None
    status: str = "active"
    claimed_group_id: str | None = None
    display_source_id: str | None = None
    cancelled_at: datetime | None = None
    tombstone_until: datetime | None = None
    first_seen_at: datetime | None = None
    updated_at: datetime | None = None
    sources: list[ListingSourceRecord] = field(default_factory=list)


@dataclass
class GateDecision:
    publish: bool
    reason: str


@dataclass
class IngestReport:
    source_id: str
    status: str  # ok | partial | failed | circuit_open | skipped_prohibited
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    cancelled: int = 0
    dead_lettered: int = 0
    detail: dict[str, Any] = field(default_factory=dict)
