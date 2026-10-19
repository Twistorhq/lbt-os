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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_budget(db: Client, org_id: str) -> dict[str, Any]:
    res = db.table("tlm_budgets").select("*").eq("org_id", org_id).execute()
    rows = res.data or []
    if rows:
        return rows[0]
    row = {
        "org_id": org_id,
        "monthly_cap": DEFAULT_MONTHLY_CAP,
        "used": 0,
        "period_start": date.today().isoformat(),
        "warn_sent": False,
    }
    created = db.table("tlm_budgets").upsert(row, on_conflict="org_id").execute()
    return (created.data or [row])[0]


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
    db.table("tlm_budgets").upsert(
        {
            "org_id": org_id,
            "monthly_cap": cap,
            "used": used + len(events),
            "period_start": budget.get("period_start"),
            "warn_sent": bool(budget.get("warn_sent") or decision["warn"]),
        },
        on_conflict="org_id",
    ).execute()

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
) -> list[dict[str, Any]]:
    """Bounded event fetch for the query engine. Shapes rows for query.py."""
    res = (
        db.table("tlm_events")
        .select("event_name,distinct_id_hash,properties,occurred_at")
        .eq("org_id", org_id)
        .in_("event_name", event_names)
        .gte("occurred_at", start.isoformat())
        .lt("occurred_at", end.isoformat())
        .order("occurred_at")
        .limit(min(limit, FETCH_LIMIT))
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
    return out


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
    res = db.table("tlm_insights").insert(row).execute()
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
