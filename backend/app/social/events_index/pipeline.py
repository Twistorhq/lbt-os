"""TW-651: the ingest pipeline (spec 10.3) on the standing
self-healing rule (TW-208): one error never crushes the process.

Per run: fetch with retry and backoff, parse with per-item fault
isolation (poison items dead-letter to evt_dead_letters and the run
continues), extract facts to silver with fingerprint dedupe,
propagate freshness and cancellations, and age listings out. A
source that keeps failing trips a circuit breaker persisted on its
evt_sources row, so a dead feed becomes a gap, not an outage (Diego
source map risk 4). Publishing is a separate step: silver promotes
to gold only through the fail-closed provenance gate, and every
decision leaves a publish-log line.

Connector registry: each connector exposes feed_items + convert_item
(JSON pipes) or split_vevents + parse_vevent (ICS). The Eventbrite
API client and organizer OAuth import are further connectors behind
this same registry when their approvals land (source map build
order 4 and 5); their source kinds already exist in the schema.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Callable

from app.self_healing import DeadLetterQueue, retry_with_backoff, run_isolated

from . import gate, geocode, ics, localist, tribe
from .models import (
    ExtractedEvent,
    IngestReport,
    ListingRecord,
    ListingSourceRecord,
    SourceConfig,
    VenueFacts,
)
from .normalize import address_fingerprint, listing_fingerprint, venue_fingerprint

log = logging.getLogger("twistor.events_ingest")

CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN = timedelta(minutes=30)
TOMBSTONE_DAYS = 90
VANISH_MISSED_SYNCS = 2

_JSON_CONNECTORS = {"localist": localist, "tribe_events": tribe}
_UID_RE = re.compile(r"^UID:(.+)$", re.MULTILINE)


def circuit_state(source: SourceConfig, now: datetime) -> str:
    if source.circuit_open_until is not None and now < source.circuit_open_until:
        return "open"
    if source.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
        return "half_open"
    return "closed"


class IngestPipeline:
    def __init__(
        self,
        store,
        fetchers: dict[str, Callable[[SourceConfig], str]],
        geocode_provider=None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.store = store
        self.fetchers = fetchers
        self.geocode_provider = geocode_provider
        self.sleep = sleep

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------
    def run_source(self, source_id: str, now: datetime) -> IngestReport:
        source = self.store.get_source(source_id)
        if source is None:
            raise KeyError(f"unknown source {source_id}")
        report = IngestReport(source_id=source_id, status="ok")
        if source.terms_posture == "prohibited" or not source.active:
            # A prohibited source is never ingested, full stop (10.6).
            report.status = "skipped_prohibited"
            return report
        if circuit_state(source, now) == "open":
            report.status = "circuit_open"
            return report

        run_id = self.store.start_run(source_id, now)
        source.last_polled_at = now
        fetcher = self.fetchers.get(source.connector)
        try:
            if fetcher is None:
                raise ValueError(f"no fetcher for connector {source.connector}")
            raw = retry_with_backoff(
                lambda: fetcher(source), attempts=3, sleep=self.sleep
            )
            events, skipped = self._parse_feed(source, raw, now)
        except Exception as exc:  # noqa: BLE001 - a failed run is a report, not a crash
            return self._fail_run(source, run_id, report, exc, now)

        dlq = DeadLetterQueue(pipeline=f"events_ingest:{source_id}")
        for skipped_item in skipped:
            dlq.collect(
                skipped_item.index, skipped_item.preview, ValueError(skipped_item.error)
            )
            self.store.add_dead_letter(
                source_id,
                run_id,
                _item_key_of(skipped_item.preview),
                skipped_item.preview,
                None,
                skipped_item.error.split(":", 1)[0],
                skipped_item.error,
            )
        report.dead_lettered = len(skipped)
        report.fetched = len(events)

        outcomes, merge_skipped = run_isolated(
            events,
            lambda e: self._merge_item(source, e, now),
            preview=_event_key_preview,
        )
        for skipped_item in merge_skipped:
            dlq.collect(
                skipped_item.index, skipped_item.preview, ValueError(skipped_item.error)
            )
            self.store.add_dead_letter(
                source_id,
                run_id,
                None,
                skipped_item.preview,
                None,
                skipped_item.error.split(":", 1)[0],
                skipped_item.error,
            )
            report.dead_lettered += 1
        for outcome, became_cancelled in outcomes:
            if outcome == "created":
                report.created += 1
            elif outcome == "updated":
                report.updated += 1
            elif outcome == "unchanged":
                report.unchanged += 1
            if became_cancelled:
                report.cancelled += 1

        self._vanish_sweep(source, {e.source_item_key for e in events}, now)

        source.consecutive_failures = 0
        source.circuit_open_until = None
        source.last_success_at = now
        self.store.save_source(source)
        report.status = "partial" if report.dead_lettered else "ok"
        report.detail["dlq"] = dlq.summarize()
        self.store.finish_run(
            run_id,
            report.status,
            {
                "items_fetched": report.fetched,
                "items_new": report.created,
                "items_updated": report.updated,
                "items_unchanged": report.unchanged,
                "items_cancelled": report.cancelled,
                "items_dead_lettered": report.dead_lettered,
            },
            report.detail,
        )
        return report

    def _fail_run(self, source, run_id, report, exc, now) -> IngestReport:
        source.consecutive_failures += 1
        if source.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
            source.circuit_open_until = now + CIRCUIT_COOLDOWN
        self.store.save_source(source)
        report.status = "failed"
        report.detail["error"] = f"{type(exc).__name__}: {exc}"
        log.warning(
            "events ingest: source %s failed: %s", source.id, report.detail["error"]
        )
        self.store.finish_run(run_id, "failed", {}, report.detail)
        return report

    def _parse_feed(self, source: SourceConfig, raw: str, now: datetime):
        """Parse with per-item isolation. Returns (events, skipped).

        Skipped items are previewed by key only (Rosa m3): a preview
        is persisted on the dead-letter row and the run detail, and
        the platform default preview carries item content, which
        spec 10.6 forbids persisting even in an ops table.
        """
        if source.connector == "ics_generic":
            blocks = ics.split_vevents(raw)
            results, skipped = run_isolated(
                blocks,
                lambda b: ics.parse_vevent(b, source, now),
                preview=_ics_key_preview,
            )
            return [e for batch in results for e in batch], skipped
        module = _JSON_CONNECTORS.get(source.connector)
        if module is None:
            raise ValueError(f"unknown connector {source.connector}")
        payload = json.loads(raw)
        items = module.feed_items(payload)
        results, skipped = run_isolated(
            items,
            lambda item: module.convert_item(item, source, now),
            preview=_feed_key_preview,
        )
        return [e for batch in results for e in batch], skipped

    # ------------------------------------------------------------------
    # Merge one extracted event into silver
    # ------------------------------------------------------------------
    def _merge_item(
        self, source: SourceConfig, event: ExtractedEvent, now: datetime
    ) -> tuple[str, bool]:
        """Returns (outcome, became_cancelled)."""
        existing_hash = self.store.raw_hash(source.id, event.source_item_key)
        if existing_hash is not None and existing_hash == event.payload_hash:
            return self._touch_seen(source, event, now)
        self.store.put_raw(
            source.id,
            event.source_item_key,
            event.bronze_payload,
            event.payload_hash,
            now,
        )

        addr_fp = address_fingerprint(
            event.venue.street_address, event.venue.city, event.venue.name
        )
        cached_row = self.store.find_venue_by_fingerprint(addr_fp)
        cached_facts = None
        if cached_row is not None:
            cached_facts = VenueFacts(
                name=cached_row["name"],
                street_address=cached_row["street_address"],
                city=cached_row["city"],
                neighborhood=cached_row["neighborhood"],
                latitude=cached_row["latitude"],
                longitude=cached_row["longitude"],
            )
        resolved, provider_name = geocode.resolve_venue(
            event.venue, cached_facts, self.geocode_provider
        )
        venue_id = self.store.upsert_venue(
            resolved, addr_fp, provider_name, now if provider_name else None
        )
        venue_fp = venue_fingerprint(resolved.latitude, resolved.longitude, addr_fp)
        fingerprint = listing_fingerprint(event.title, event.starts_at, venue_fp)

        listing = self.store.find_listing_by_fingerprint(
            fingerprint
        ) or self.store.find_listing_by_source_key(source.id, event.source_item_key)

        if listing is None:
            listing = ListingRecord(
                id="",
                fingerprint=fingerprint,
                title=event.title,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                venue_id=venue_id,
                category=event.category,
                status="active",
                display_source_id=source.id,
                first_seen_at=now,
                updated_at=now,
            )
            self.store.create_listing(listing)
            became_cancelled = self._apply_status(listing, event, now, creating=True)
            self._upsert_source_row(listing, source, event, now)
            self.store.update_listing(listing)
            return "created", became_cancelled

        outcome = "updated"
        if self._should_take_facts(listing, source):
            # The fingerprint is the listing's identity (the tombstone
            # doctrine treats it as such), so it moves only with the
            # facts it was computed from. An item whose facts are not
            # taken must not reassign it, or the fingerprint oscillates
            # between source variants on every poll (Rosa m2).
            listing.fingerprint = fingerprint
            listing.title = event.title
            listing.starts_at = event.starts_at
            listing.ends_at = event.ends_at
            listing.category = event.category
            listing.venue_id = venue_id
            listing.display_source_id = source.id
        became_cancelled = self._apply_status(listing, event, now, creating=False)
        self._upsert_source_row(listing, source, event, now)
        listing.updated_at = now
        self.store.update_listing(listing)
        return outcome, became_cancelled

    def _should_take_facts(self, listing: ListingRecord, source: SourceConfig) -> bool:
        if listing.display_source_id is None or listing.display_source_id == source.id:
            return True
        display_source = self.store.get_source(listing.display_source_id)
        display_authority = display_source.authority if display_source else 99
        return source.authority < display_authority

    def _apply_status(
        self,
        listing: ListingRecord,
        event: ExtractedEvent,
        now: datetime,
        creating: bool,
    ) -> bool:
        """Apply the event's status to the listing.

        Returns True when this call moved the listing into cancelled.
        """
        if listing.status == "claimed":
            return False
        if event.status == "cancelled":
            if listing.status == "cancelled":
                return False
            listing.status = "cancelled"
            listing.cancelled_at = now
            listing.tombstone_until = now + timedelta(days=TOMBSTONE_DAYS)
            return True
        # Event is active at its source.
        if listing.status == "cancelled":
            if listing.tombstone_until is not None and now < listing.tombstone_until:
                return False  # Tombstoned: a flaky feed cannot resurrect it.
            listing.status = "active"
            listing.cancelled_at = None
            listing.tombstone_until = None
            return False
        if listing.status == "stale":
            listing.status = "active"
        return False

    def _touch_seen(
        self, source: SourceConfig, event: ExtractedEvent, now: datetime
    ) -> tuple[str, bool]:
        """Unchanged payload: refresh freshness and reconcile status.

        Reprocessing is skipped, but 'seen' is a fact the gate and the
        tombstone rule both depend on, so it is always recorded.
        """
        listing = self.store.find_listing_by_source_key(
            source.id, event.source_item_key
        )
        if listing is None:
            # Bronze says seen but silver lost the listing: rebuild it.
            return self._merge_item_fresh(source, event, now)
        row = next((r for r in listing.sources if r.source_id == source.id), None)
        was = listing.status
        if row is not None:
            row.last_seen_at = now
            row.missed_syncs = 0
            self.store.upsert_listing_source(listing.id, row)
        became_cancelled = self._apply_status(listing, event, now, creating=False)
        self.store.update_listing(listing)
        if became_cancelled:
            return "updated", True
        if listing.status != was:
            return "updated", False
        return "unchanged", False

    def _merge_item_fresh(
        self, source: SourceConfig, event: ExtractedEvent, now: datetime
    ) -> tuple[str, bool]:
        # Force the full merge path by pretending the hash is new.
        original = self.store.raw_hash
        self.store.raw_hash = lambda sid, key: None  # type: ignore[method-assign]
        try:
            return self._merge_item(source, event, now)
        finally:
            self.store.raw_hash = original  # type: ignore[method-assign]

    def _upsert_source_row(
        self,
        listing: ListingRecord,
        source: SourceConfig,
        event: ExtractedEvent,
        now: datetime,
    ) -> None:
        existing = next((r for r in listing.sources if r.source_id == source.id), None)
        row = ListingSourceRecord(
            source_id=source.id,
            source_item_key=event.source_item_key,
            source_url=event.source_url,
            source_organizer_ref=event.source_organizer_ref,
            authority=source.authority,
            fetched_at=existing.fetched_at if existing else now,
            last_seen_at=now,
            missed_syncs=0,
        )
        if existing is not None:
            row.fetched_at = now  # this version was fetched now
        self.store.upsert_listing_source(listing.id, row)
        listing.sources = [r for r in listing.sources if r.source_id != source.id] + [
            row
        ]

    def _vanish_sweep(
        self, source: SourceConfig, seen_keys: set[str], now: datetime
    ) -> None:
        """Full-sync sources only: an item missing across two
        consecutive syncs cancels its listing (spec 10.3), unless
        another source still carries the event fresh."""
        if not source.config.get("full_sync"):
            return
        for listing, row in self.store.source_listing_rows(source.id):
            if row.source_item_key in seen_keys:
                continue
            row.missed_syncs += 1
            self.store.upsert_listing_source(listing.id, row)
            if row.missed_syncs < VANISH_MISSED_SYNCS:
                continue
            if listing.status in ("cancelled", "claimed"):
                continue
            others_fresh = any(
                other.source_id != source.id and self._co_carrier_fresh(other, now)
                for other in listing.sources
            )
            if others_fresh:
                continue
            listing.status = "cancelled"
            listing.cancelled_at = now
            listing.tombstone_until = now + timedelta(days=TOMBSTONE_DAYS)
            listing.updated_at = now
            self.store.update_listing(listing)

    def _co_carrier_fresh(self, row: ListingSourceRecord, now: datetime) -> bool:
        """Whether another source still carries the event, judged by
        the freshness definition the rest of the layer uses: the row's
        last_seen_at inside that source's gate staleness ceiling.

        The missed_syncs counter cannot answer this (Rosa m1): only
        full-sync sources maintain it, so a non-full-sync co-carrier
        sat at zero forever and suppressed cancellation indefinitely.
        """
        if row.last_seen_at is None:
            return False
        carrier = self.store.get_source(row.source_id)
        if carrier is None:
            return False
        return now - row.last_seen_at <= gate.staleness_ceiling(carrier)

    # ------------------------------------------------------------------
    # Publish: silver to gold through the provenance gate
    # ------------------------------------------------------------------
    def publish(self, now: datetime) -> dict[str, int]:
        counts = {"published": 0, "withheld": 0, "removed": 0}
        source_by_id = {s.id: s for s in self.store.all_sources()}
        for listing in self.store.all_listings():
            decision = gate.gate_decision(listing, listing.sources, source_by_id, now)
            if decision.reason == "stale" and listing.status == "active":
                listing.status = "stale"
                listing.updated_at = now
                self.store.update_listing(listing)
            existing_gold = self.store.gold_get(listing.id)
            if decision.publish:
                self.store.gold_put(listing.id, self._gold_row(listing, source_by_id))
                self.store.add_publish_log(listing.id, "published", decision.reason)
                counts["published"] += 1
            elif existing_gold is not None:
                self.store.gold_remove(listing.id)
                self.store.add_publish_log(listing.id, "removed", decision.reason)
                counts["removed"] += 1
            else:
                self.store.add_publish_log(listing.id, "withheld", decision.reason)
                counts["withheld"] += 1
        return counts

    def _gold_row(
        self, listing: ListingRecord, source_by_id: dict[str, SourceConfig]
    ) -> dict:
        indexable = [
            row
            for row in listing.sources
            if (src := source_by_id.get(row.source_id)) is not None
            and src.terms_posture == "index_facts"
            and row.source_url
        ]
        indexable.sort(key=lambda r: (r.authority, -r.last_seen_at.timestamp()))
        primary = indexable[0]
        labels: list[str] = []
        for row in sorted(indexable, key=lambda r: r.authority):
            label = source_by_id[row.source_id].attribution_label
            if label not in labels:
                labels.append(label)
        venue = self.store.get_venue(listing.venue_id) if listing.venue_id else None
        return {
            "title": listing.title,
            "starts_at": listing.starts_at,
            "ends_at": listing.ends_at,
            "venue_id": listing.venue_id,
            "venue_name": venue["name"] if venue else None,
            "street_address": venue["street_address"] if venue else None,
            "city": venue["city"] if venue else None,
            "neighborhood": venue["neighborhood"] if venue else None,
            "latitude": venue["latitude"] if venue else None,
            "longitude": venue["longitude"] if venue else None,
            "category": listing.category,
            "min_age_band": listing.min_age_band,
            "attribution_text": "Listed from " + " and ".join(labels),
            "primary_source_url": primary.source_url,
            "last_seen_at": max(r.last_seen_at for r in indexable),
        }


def _ics_key_preview(block: str) -> str:
    """Key-only preview for an ICS block: its UID line, nothing else."""
    match = _UID_RE.search(block or "")
    return f"UID:{match.group(1).strip()}" if match else "<unkeyed ics item>"


def _feed_key_preview(item) -> str:
    """Key-only preview for a JSON feed item: its id, nothing else."""
    if isinstance(item, dict):
        for key in ("id", "uid"):
            value = item.get(key)
            if value is not None:
                return f"id:{value}"
    return "<unkeyed feed item>"


def _event_key_preview(event: ExtractedEvent) -> str:
    return event.source_item_key


_KEY_OF_RE = re.compile(r"^(?:UID|id):(.+)$")


def _item_key_of(preview: str) -> str | None:
    match = _KEY_OF_RE.match((preview or "").strip())
    return match.group(1) if match else None
