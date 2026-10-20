"""TW-622 Twistor Telemetry — pure query engine.

All functions are pure: event dicts in, result dicts out, no I/O, no DB.
Event dict shape:
    {"event": str, "distinct_id_hash": str,
     "occurred_at": datetime, "properties": dict}

This is what makes the engine testable without a database and fast to
reason about. The service layer fetches bounded event rows and hands
them here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

BUCKET_DELTAS = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
}


def _in_range(ts: datetime, start: datetime, end: datetime) -> bool:
    ts, start, end = _as_utc(ts), _as_utc(start), _as_utc(end)
    return start <= ts < end


def _as_utc(ts: datetime) -> datetime:
    """Coerce to tz-aware UTC. Naive datetimes are assumed UTC (documented) —
    never system-local, so results don't shift with the server's timezone."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _floor(ts: datetime, bucket: str) -> datetime:
    ts = _as_utc(ts)
    if bucket == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    # week: Monday 00:00 UTC
    day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def trend(
    events: list[dict[str, Any]],
    event_name: str,
    start: datetime,
    end: datetime,
    bucket: str = "day",
) -> list[dict[str, Any]]:
    """Bucketed counts for one event, zero-filled across [start, end)."""
    start, end = _as_utc(start), _as_utc(end)
    delta = BUCKET_DELTAS[bucket]
    buckets: list[datetime] = []
    cur = _floor(start, bucket)
    while cur < end:
        buckets.append(cur)
        cur += delta
    counts = [0] * len(buckets)
    index = {b: i for i, b in enumerate(buckets)}
    for e in events:
        if e.get("event") != event_name:
            continue
        ts = e.get("occurred_at")
        if ts is None or not _in_range(ts, start, end):
            continue
        i = index.get(_floor(ts, bucket))
        if i is not None:
            counts[i] += 1
    return [
        {"bucket_start": b.isoformat(), "count": c} for b, c in zip(buckets, counts)
    ]


def funnel(
    events: list[dict[str, Any]],
    steps: list[str],
    window: timedelta,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Ordered funnel: unique users completing step N after step N-1.

    A user counts for step k only if they completed step k-1 first and
    step k occurred after that completion and within `window` of it.
    """
    start, end = _as_utc(start), _as_utc(end)
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        ts = e.get("occurred_at")
        if ts is None or not _in_range(ts, start, end):
            continue
        by_user[e.get("distinct_id_hash", "")].append(e)
    for evs in by_user.values():
        evs.sort(key=lambda e: _as_utc(e["occurred_at"]))

    step_counts = [0] * len(steps)
    for evs in by_user.values():
        anchor: datetime | None = None
        step_idx = 0
        for e in evs:
            if step_idx >= len(steps):
                break
            if e.get("event") != steps[step_idx]:
                continue
            ts = _as_utc(e["occurred_at"])
            if step_idx == 0:
                anchor = ts
                step_counts[0] += 1
                step_idx = 1
            elif anchor is not None and ts - anchor <= window:
                # Step completed after the previous step, inside the window.
                # Out-of-window occurrences are skipped; scanning continues.
                anchor = ts
                step_counts[step_idx] += 1
                step_idx += 1
    base = step_counts[0] if step_counts else 0
    conversion = [c / base if base else 0.0 for c in step_counts]
    return {
        "steps": [
            {"event": name, "count": count} for name, count in zip(steps, step_counts)
        ],
        "conversion": conversion,
    }


def retention(
    events: list[dict[str, Any]],
    cohort_event: str,
    return_event: str,
    days: int,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Day-N retention: fraction of the cohort returning on each day.

    retention[0] is 1.0 by definition (cohort membership). Days 1..N are
    measured: user did `return_event` on (their cohort day + d).

    Right-censoring: the day-d denominator only includes users whose
    cohort_day + d falls on or before the latest observed date in the
    data. Users too recent to have had day d yet are excluded from that
    day's denominator instead of dragging it down.
    """
    cohort_day: dict[str, Any] = {}
    returned: dict[str, set[Any]] = defaultdict(set)
    latest: Any = None
    for e in events:
        ts = e.get("occurred_at")
        if ts is None:
            continue
        day = _as_utc(ts).date()
        if latest is None or day > latest:
            latest = day
        uid = e.get("distinct_id_hash", "")
        if e.get("event") == cohort_event and _in_range(ts, start, end):
            if uid not in cohort_day or day < cohort_day[uid]:
                cohort_day[uid] = day
        if e.get("event") == return_event:
            returned[uid].add(day)
    cohort = list(cohort_day)
    size = len(cohort)
    start = _as_utc(start)
    as_of = latest if latest is not None else start.date()
    curve = [1.0]
    for d in range(1, days + 1):
        eligible = [
            uid for uid in cohort if cohort_day[uid] + timedelta(days=d) <= as_of
        ]
        if not eligible:
            curve.append(0.0)
            continue
        hit = sum(
            1
            for uid in eligible
            if (cohort_day[uid] + timedelta(days=d)) in returned[uid]
        )
        curve.append(hit / len(eligible))
    return {"cohort_size": size, "retention": curve}


def breakdown(
    events: list[dict[str, Any]],
    event_name: str,
    prop: str,
    start: datetime,
    end: datetime,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Top values of one property for one event. Missing (or None) excluded."""
    start, end = _as_utc(start), _as_utc(end)
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        if e.get("event") != event_name:
            continue
        ts = e.get("occurred_at")
        if ts is None or not _in_range(ts, start, end):
            continue
        props = e.get("properties") or {}
        if prop not in props or props[prop] is None:
            continue
        counts[str(props[prop])] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"value": v, "count": c} for v, c in ranked[:limit]]
