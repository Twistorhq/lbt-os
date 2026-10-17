"""
Micro-toll ledger (TW-550, Twistor staple TW-549).

Every metered action in the system emits a toll record via record_toll_event.
M1 ships the ledger; M2 (missed-call) and M3 (BI) emit into it.

Idempotency: idempotency_key is UNIQUE — a retried emission returns the
existing row instead of double-tolling. Unknown event types fail loud
(ValueError), never silent $0.
"""

from __future__ import annotations

import logging

from supabase import Client

from ..config import TOLL_RATES

log = logging.getLogger(__name__)


def record_toll_event(
    db: Client,
    org_id: str,
    event_type: str,
    quantity: float = 1,
    idempotency_key: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    amount_cents: int | None = None,
    applied_tier: str | None = None,
    plan_id: str | None = None,
) -> dict:
    """
    Record one metered action as a toll.

    - Fixed-rate events: amount derives from TOLL_RATES[event_type].
    - Percent-of-value events (e.g. booking.recovered): pass amount_cents
      computed by the emitter; unit_rate_cents is recorded as 0 with the
      amount intact.
    - Data API tolls: pass amount_cents computed by the toll worker from
      the key's plan; applied_tier stamps the volume tier in force
      (DATA LAYER DOCTRINE, dynamic pricing LAW); plan_id references
      the data_api_plans row that priced it.
    - Unknown event_type raises ValueError — never a silent $0.
    - Duplicate idempotency_key returns the existing row (no double-toll).
    """
    if event_type in TOLL_RATES:
        unit_rate_cents = TOLL_RATES[event_type]
        total = (
            int(quantity * unit_rate_cents) if amount_cents is None else amount_cents
        )
    elif amount_cents is not None:
        unit_rate_cents = 0
        total = amount_cents
    else:
        raise ValueError(f"Unknown toll event type: {event_type}")

    if not idempotency_key:
        raise ValueError("idempotency_key is required — tolls must be deduplicable.")

    row = {
        "org_id": org_id,
        "event_type": event_type,
        "quantity": quantity,
        "unit_rate_cents": unit_rate_cents,
        "amount_cents": total,
        "idempotency_key": idempotency_key,
        "source_type": source_type,
        "source_id": source_id,
        "applied_tier": applied_tier,
        "plan_id": plan_id,
    }
    try:
        result = db.table("toll_events").insert(row).execute()
    except Exception as exc:
        error_text = str(exc).lower()
        if "duplicate key" in error_text or "unique constraint" in error_text:
            existing = (
                db.table("toll_events")
                .select("*")
                .eq("idempotency_key", idempotency_key)
                .maybe_single()
                .execute()
            )
            data = existing.data if existing and existing.data else None
            log.info("toll_event_duplicate", extra={"idempotency_key": idempotency_key})
            return data or row
        raise

    data = result.data if result and result.data else None
    inserted = data[0] if isinstance(data, list) and data else (data or row)
    log.info(
        "toll_event_recorded",
        extra={"org_id": org_id, "event_type": event_type, "amount_cents": total},
    )
    return inserted


def get_toll_summary(db: Client, org_id: str, since: str) -> dict:
    """
    Aggregate toll spend per event_type for an org since an ISO timestamp.
    Powers the dashboard spend view ("every dollar shows its source").
    """
    result = (
        db.table("toll_events")
        .select("event_type, quantity, amount_cents")
        .eq("org_id", org_id)
        .gte("occurred_at", since)
        .execute()
    )
    rows = result.data if result and result.data else []
    summary: dict[str, dict] = {}
    for r in rows:
        et = r["event_type"]
        entry = summary.setdefault(et, {"count": 0, "amount_cents": 0})
        entry["count"] += r.get("quantity", 0)
        entry["amount_cents"] += r.get("amount_cents", 0)
    return summary
