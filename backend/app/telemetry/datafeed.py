"""TW-622 Twistor Telemetry — benchmark datafeed aggregates.

The event stream feeds Twistor's benchmark data niches (trades benchmarks,
conversation outcomes). Only cohort-level aggregates leave the building:

- grouped by caller-chosen property values (the cohort definition),
- gated on a minimum distinct-user count (k-anonymity style),
- carrying NO identifiers — the output is provably free of
  distinct_id_hash values.

This is the "product pays for itself in data" half of the TW-630 wedge.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

DEFAULT_MIN_COHORT = 5


def cohort_aggregates(
    events: list[dict[str, Any]],
    event_name: str,
    group_props: list[str],
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> list[dict[str, Any]]:
    """Aggregate one event into cohort rows. Small cohorts suppressed."""
    groups: dict[tuple[str, ...], dict[str, Any]] = defaultdict(
        lambda: {"users": set(), "count": 0}
    )
    for e in events:
        if e.get("event") != event_name:
            continue
        props = e.get("properties") or {}
        if any(p not in props for p in group_props):
            continue
        key = tuple(str(props[p]) for p in group_props)
        groups[key]["users"].add(e.get("distinct_id_hash", ""))
        groups[key]["count"] += 1
    rows = []
    for key, agg in groups.items():
        if len(agg["users"]) < min_cohort:
            continue
        rows.append(
            {
                "cohort": dict(zip(group_props, key)),
                "distinct_users": len(agg["users"]),
                "event_count": agg["count"],
            }
        )
    rows.sort(key=lambda r: (-r["distinct_users"], str(r["cohort"])))
    return rows
