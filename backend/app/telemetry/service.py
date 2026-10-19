"""TW-622 Twistor Telemetry — Supabase persistence.

Ingest path: scrub PII -> hash identity -> budget decision -> batch
insert. One insert per batch, never one per event. Toll-ledger failures
never fail ingestion (TW-208); there is deliberately no per-event toll —
capture is covered by the flat subscription (the TW-630 wedge).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError
from supabase import Client

from . import budgets, scrub
from .models import InsightDefinition, TelemetryEvent

log = logging.getLogger(__name__)

# Generous monthly tier on the flat subscription. PostHog's own
# comparison point is 10M events/mo; matching it removes the "what if
# we grow" anxiety instead of monetizing it.
DEFAULT_MONTHLY_CAP = 10_000_000

FETCH_LIMIT = 10_000


class TelemetryError(Exception):
    """Fail-loud domain error. The router maps these to 4xx."""


class InsightNotFound(TelemetryError):
    pass


class InsightExists(TelemetryError):
    """Duplicate (org_id, key). The router maps this to 409."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_today() -> date:
    return _utcnow().date()


def get_or_create_budget(db: Client, org_id: str) -> dict[str, Any]:
    """Fetch the org's budget row, rolling over to a new month when the
    stored period is stale. Without the rollover the 'monthly' tier would
    accumulate for life and permanently sample after month one."""
    res = db.table("tlm_budgets").select("*").eq("org_id", org_id).execute()
    rows = res.data or []
    today = _utc_today()
    if rows:
        row = rows[0]
        period = str(row.get("period_start") or "")[:7]
        if period != today.strftime("%Y-%m"):
            row = {
                **row,
                "used": 0,
                "warn_sent": False,
                "period_start": today.isoformat(),
            }
            res2 = db.table("tlm_budgets").upsert(row, on_conflict="org_id").execute()
            return (res2.data or [row])[0]
        return row
    row = {
        "org_id": org_id,
        "monthly_cap": DEFAULT_MONTHLY_CAP,
        "used": 0,
        "period_start": today.isoformat(),
        "warn_sent": False,
    }
    created = db.table("tlm_budgets").upsert(row, on_conflict="org_id").execute()
    return (created.data or [row])[0]


def _bump_budget(
    db: Client,
    org_id: str,
    delta: int,
    cap: int | None,
    warn: bool,
    fallback_used: int,
    fallback_period: str,
) -> dict[str, Any]:
    """Atomically add `delta` to the org's budget counter.

    Preferred path: the `tlm_bump_budget` RPC (single statement, no
    lost-update race). Fallback: read-modify-write upsert, which can lose
    concurrent updates but fails toward the customer (under-counts usage).
    """
    today = _utc_today().isoformat()
    try:
        res = db.rpc(
            "tlm_bump_budget",
            {
                "p_org_id": org_id,
                "p_delta": delta,
                "p_cap": cap,
                "p_period_start": today,
                "p_warn": warn,
            },
        ).execute()
        rows = res.data or []
        if rows and isinstance(rows[0], dict) and "used" in rows[0]:
            return rows[0]
    except Exception as exc:  # RPC not deployed yet — fall back honestly
        log.warning("telemetry: budget rpc unavailable, upsert fallback: %s", exc)
    row = {
        "org_id": org_id,
        "monthly_cap": cap,
        "used": fallback_used + delta,
        "period_start": fallback_period,
        "warn_sent": warn,
    }
    res = db.table("tlm_budgets").upsert(row, on_conflict="org_id").execute()
    return (res.data or [row])[0]


def ingest_events(
    db: Client,
    org_id: str,
    events: list[TelemetryEvent],
    extra_pii_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Ingest a batch. Returns received/stored/sampled accounting."""
    if not events:
        raise TelemetryError("cannot ingest an empty batch")

    budget = get_or_create_budget(db, org_id)
    cap = budget.get("monthly_cap")
    used = int(budget.get("used") or 0)
    decision = budgets.ingest_decision(used, cap)
    sampled = decision["action"] == "sample"
    rate = decision["rate"]

    rows: list[dict[str, Any]] = []
    for ev in events:
        props = scrub.scrub_properties(ev.properties, org_id, extra_pii_keys)
        dh = scrub.hash_distinct_id(ev.distinct_id, org_id)
        key = f"{org_id}|{dh}|{ev.event}|{ev.occurred_at.isoformat()}"
        if sampled and not budgets.should_keep(key, rate):
            continue
        rows.append(
            {
                "org_id": org_id,
                "event_name": ev.event,
                "distinct_id_hash": dh,
                "properties": props,
                "occurred_at": ev.occurred_at.isoformat(),
                "ingested_at": _utcnow().isoformat(),
                "sampled": sampled,
                "sample_rate": rate,
            }
        )

    if rows:
        db.table("tlm_events").insert(rows).execute()

    # Honest metering: the tier counts RECEIVED events, not stored ones.
    # Sampling never hides usage from the customer.
    warn_now = bool(decision["warn"] and not budget.get("warn_sent"))
    _bump_budget(
        db,
        org_id,
        len(events),
        cap,
        bool(budget.get("warn_sent") or decision["warn"]),
        fallback_used=used,
        fallback_period=str(budget.get("period_start") or ""),
    )

    return {
        "received": len(events),
        "stored": len(rows),
        "sampled": sampled,
        "sample_rate": rate,
        "warn": decision["warn"],
        "warn_now": warn_now,
    }


def fetch_events(
    db: Client,
    org_id: str,
    event_names: list[str],
    start: datetime,
    end: datetime,
    limit: int = FETCH_LIMIT,
) -> dict[str, Any]:
    """Bounded event fetch for the query engine. Shapes rows for query.py.

    Returns {"events", "fetched", "truncated"} — the truncation flag is
    honest uncertainty: callers must surface it instead of presenting
    silently incomplete counts as complete.
    """
    capped = min(limit, FETCH_LIMIT)
    res = (
        db.table("tlm_events")
        .select("event_name,distinct_id_hash,properties,occurred_at")
        .eq("org_id", org_id)
        .in_("event_name", event_names)
        .gte("occurred_at", start.isoformat())
        .lt("occurred_at", end.isoformat())
        .order("occurred_at")
        .limit(capped)
        .execute()
    )
    out = []
    for r in res.data or []:
        try:
            ts = r["occurred_at"]
            ts = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
        except (ValueError, TypeError):
            continue
        out.append(
            {
                "event": r["event_name"],
                "distinct_id_hash": r["distinct_id_hash"],
                "occurred_at": ts,
                "properties": r.get("properties") or {},
            }
        )
    return {"events": out, "fetched": len(out), "truncated": len(out) >= capped}


def create_insight(
    db: Client,
    org_id: str,
    key: str,
    kind: str,
    definition: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    try:
        validated = InsightDefinition(key=key, kind=kind, definition=definition)
    except ValidationError as exc:
        raise TelemetryError(f"invalid insight: {exc}") from exc
    if get_insight(db, org_id, validated.key) is not None:
        raise InsightExists(f"insight '{validated.key}' already exists")
    row = {
        "org_id": org_id,
        "key": validated.key,
        "kind": validated.kind,
        "definition": validated.definition,
        "version": 1,
        "created_by": actor,
        "updated_by": actor,
        "created_at": _utcnow().isoformat(),
        "updated_at": _utcnow().isoformat(),
    }
    try:
        res = db.table("tlm_insights").insert(row).execute()
    except Exception as exc:
        # Race with another writer between the check above and the insert.
        if "23505" in str(exc) or "duplicate" in str(exc).lower():
            raise InsightExists(f"insight '{validated.key}' already exists") from exc
        raise
    return (res.data or [row])[0]


def list_insights(db: Client, org_id: str) -> list[dict[str, Any]]:
    res = (
        db.table("tlm_insights").select("*").eq("org_id", org_id).order("key").execute()
    )
    return res.data or []


def get_insight(db: Client, org_id: str, key: str) -> dict[str, Any] | None:
    res = (
        db.table("tlm_insights")
        .select("*")
        .eq("org_id", org_id)
        .eq("key", key)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def update_insight(
    db: Client,
    org_id: str,
    key: str,
    definition: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    existing = get_insight(db, org_id, key)
    if existing is None:
        raise InsightNotFound(f"insight not found: {key}")
    try:
        validated = InsightDefinition(
            key=key, kind=existing["kind"], definition=definition
        )
    except ValidationError as exc:
        raise TelemetryError(f"invalid insight: {exc}") from exc
    row = {
        "definition": validated.definition,
        "version": int(existing.get("version") or 1) + 1,
        "updated_by": actor,
        "updated_at": _utcnow().isoformat(),
    }
    res = (
        db.table("tlm_insights")
        .update(row)
        .eq("org_id", org_id)
        .eq("key", key)
        .execute()
    )
    updated = (res.data or [{}])[0]
    return {**existing, **row, **updated}


def delete_insight(db: Client, org_id: str, key: str) -> None:
    existing = get_insight(db, org_id, key)
    if existing is None:
        raise InsightNotFound(f"insight not found: {key}")
    db.table("tlm_insights").delete().eq("org_id", org_id).eq("key", key).execute()
