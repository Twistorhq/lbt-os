"""TW-651: the fail-closed provenance gate (spec 10.3).

A listing may surface publicly only when ALL of these hold: at least
one evt_listing_sources row with a source_url; that source's terms
posture is index_facts; fetched and last seen are present and fresh
under the staleness ceiling (7 days, or three missed poll cycles for
that source, whichever is later); and the listing status is active.
Any miss withholds the listing and the publish log records why.
"""

from datetime import datetime, timedelta, timezone

from app.social.events_index import gate
from app.social.events_index.models import (
    ListingRecord,
    ListingSourceRecord,
    SourceConfig,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _source(**overrides) -> SourceConfig:
    base = dict(
        id="src-1",
        name="Source",
        kind="ics",
        connector="ics_generic",
        terms_posture="index_facts",
        attribution_label="Source",
        poll_interval_seconds=21600,
    )
    base.update(overrides)
    return SourceConfig(**base)


def _listing(**overrides) -> ListingRecord:
    base = dict(
        id="lst-1",
        fingerprint="f" * 64,
        title="Storytime",
        starts_at=NOW + timedelta(days=2),
        ends_at=NOW + timedelta(days=2, hours=1),
        venue_id="ven-1",
        category=None,
        status="active",
    )
    base.update(overrides)
    return ListingRecord(**base)


def _row(**overrides) -> ListingSourceRecord:
    base = dict(
        source_id="src-1",
        source_item_key="item-1",
        source_url="https://example.org/event/1",
        source_organizer_ref=None,
        authority=3,
        fetched_at=NOW - timedelta(hours=1),
        last_seen_at=NOW - timedelta(hours=1),
    )
    base.update(overrides)
    return ListingSourceRecord(**base)


def test_gate_passes_for_fresh_attributed_active_listing():
    decision = gate.gate_decision(_listing(), [_row()], {"src-1": _source()}, NOW)
    assert decision.publish is True
    assert decision.reason == "ok"


def test_gate_withholds_without_source_url():
    decision = gate.gate_decision(
        _listing(), [_row(source_url=None)], {"src-1": _source()}, NOW
    )
    assert decision.publish is False
    assert decision.reason == "missing_source_url"


def test_gate_withholds_with_no_sources_at_all():
    decision = gate.gate_decision(_listing(), [], {"src-1": _source()}, NOW)
    assert decision.publish is False


def test_gate_withholds_organizer_only_source():
    src = _source(terms_posture="organizer_only")
    decision = gate.gate_decision(_listing(), [_row()], {"src-1": src}, NOW)
    assert decision.publish is False
    assert decision.reason == "no_indexable_source"


def test_gate_withholds_stale_listing():
    old = NOW - timedelta(days=8)
    decision = gate.gate_decision(
        _listing(),
        [_row(fetched_at=old, last_seen_at=old)],
        {"src-1": _source()},
        NOW,
    )
    assert decision.publish is False
    assert decision.reason == "stale"


def test_staleness_ceiling_is_seven_days_or_three_cycles():
    fast = _source(poll_interval_seconds=3600)
    assert gate.staleness_ceiling(fast) == timedelta(days=7)
    slow = _source(poll_interval_seconds=4 * 86400)
    assert gate.staleness_ceiling(slow) == timedelta(days=12)


def test_gate_uses_freshest_indexable_source():
    stale_row = _row(last_seen_at=NOW - timedelta(days=30), fetched_at=NOW - timedelta(days=30))
    fresh_row = _row(source_id="src-2", last_seen_at=NOW - timedelta(hours=2))
    sources = {"src-1": _source(), "src-2": _source(id="src-2")}
    decision = gate.gate_decision(_listing(), [stale_row, fresh_row], sources, NOW)
    assert decision.publish is True


def test_gate_withholds_non_active_statuses():
    for status in ("cancelled", "stale", "claimed"):
        decision = gate.gate_decision(
            _listing(status=status), [_row()], {"src-1": _source()}, NOW
        )
        assert decision.publish is False, status
        assert decision.reason == f"status_{status}"


def test_gate_withholds_ended_listings():
    ended = _listing(
        starts_at=NOW - timedelta(days=1), ends_at=NOW - timedelta(hours=2)
    )
    decision = gate.gate_decision(ended, [_row()], {"src-1": _source()}, NOW)
    assert decision.publish is False
    assert decision.reason == "ended"


def test_gate_ignores_organizer_only_freshness():
    # Freshness from an organizer_only pipe cannot rescue a stale
    # indexable row: only index_facts provenance counts toward gold.
    idx = _source(id="src-idx")
    org = _source(id="src-org", terms_posture="organizer_only", kind="organizer_import")
    rows = [
        _row(source_id="src-idx", last_seen_at=NOW - timedelta(days=30),
             fetched_at=NOW - timedelta(days=30)),
        _row(source_id="src-org", authority=1),
    ]
    decision = gate.gate_decision(_listing(), rows, {"src-idx": idx, "src-org": org}, NOW)
    assert decision.publish is False
    assert decision.reason == "stale"
