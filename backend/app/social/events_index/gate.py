"""TW-651: the fail-closed provenance gate (spec 10.3).

A listing may surface publicly only when all of these hold:

- at least one evt_listing_sources row with a source_url,
- that source's terms_posture is index_facts,
- fetched_at and last_seen_at are present and fresh under the
  staleness ceiling (default 7 days, or three missed poll cycles for
  that source, whichever is later),
- and the listing status is active.

Any miss withholds the listing, and the publish step writes a
publish-log line with the reason. Nothing publishes without
provenance. Ratings are NOT a gate input: an unrated listing is
public facts for anonymous browsing and fails closed only for
members, which is Section 5.9 enforcement on the member surface, not
this gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import GateDecision, ListingRecord, ListingSourceRecord, SourceConfig

BASE_CEILING = timedelta(days=7)


def staleness_ceiling(source: SourceConfig) -> timedelta:
    return max(BASE_CEILING, timedelta(seconds=3 * source.poll_interval_seconds))


def gate_decision(
    listing: ListingRecord,
    sources: list[ListingSourceRecord],
    source_by_id: dict[str, SourceConfig],
    now: datetime,
) -> GateDecision:
    if listing.status != "active":
        return GateDecision(False, f"status_{listing.status}")
    end = listing.ends_at or listing.starts_at
    if end < now:
        return GateDecision(False, "ended")

    indexable = [
        row
        for row in sources
        if (src := source_by_id.get(row.source_id)) is not None
        and src.terms_posture == "index_facts"
    ]
    if not indexable:
        return GateDecision(False, "no_indexable_source")
    with_url = [row for row in indexable if row.source_url]
    if not with_url:
        return GateDecision(False, "missing_source_url")

    freshest: timedelta | None = None
    for row in with_url:
        src = source_by_id[row.source_id]
        if row.fetched_at is None or row.last_seen_at is None:
            continue
        age = now - row.last_seen_at
        if age <= staleness_ceiling(src):
            return GateDecision(True, "ok")
        if freshest is None or age < freshest:
            freshest = age
    return GateDecision(False, "stale")
