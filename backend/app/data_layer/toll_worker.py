"""TW-569: out-of-band toll worker for data API calls.

The data API serves the read FIRST, then enqueues a toll job. This worker
drains toll_emit_queue with retry + backoff (TW-208). A failed toll write
never fails a data read (it was already served); the queue retries.

Dynamic volume-tiered pricing (DATA LAYER DOCTRINE, Justynn 2026-10-16,
LAW): the worker reads aggregate platform call volume at emit time,
resolves the plan's volume tier, computes the charge, and STAMPS the
applied tier on the toll_events row. Tiers are declared in-plan: no
surprise repricing, and every charge is auditable to its tier.

"Never double-toll" as mechanism:
  1. toll_emit_queue has UNIQUE (key_id, idempotency_key) — the queue itself
     cannot double-enqueue within a client's namespace.
  2. The worker checks data_toll_dedupe before emitting.
  3. record_toll_event's own idempotency_key UNIQUE is the final backstop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..services import toll_service

log = logging.getLogger("twistor.data_layer.toll_worker")

MAX_ATTEMPTS = 8
BASE_RETRY_SECONDS = 30


def resolve_tier(volume: int, tiers: list[dict]) -> dict:
    """Pure: pick the volume tier in force. Tiers sorted by upto_calls.

    Each tier: {"upto_calls": int | None, "per_call_cents": int, "tier": str}.
    upto_calls null = the top (unbounded) tier.
    """
    ordered = sorted(
        tiers, key=lambda t: (t.get("upto_calls") is None, t.get("upto_calls") or 0)
    )
    for t in ordered:
        upto = t.get("upto_calls")
        if upto is None or volume < upto:
            return t
    return ordered[-1]


def get_platform_volume(db, window_days: int = 30) -> int:
    """Aggregate data-API call volume across the platform in-window.

    Source of truth: data_api_usage reconciliation rows. Falls back to 0
    (tier_0) on read failure. tier_0 is the plan's DECLARED base rate
    (per_call_cents), not a penalty: volume tiers are discounts for proven
    volume, so no proven volume means no discount. Documented, not silent.
    """
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        rows = (
            db.table("data_api_usage")
            .select("count")
            .gte("window_start", since)
            .execute()
            .data
            or []
        )
        return sum(int(r.get("count") or 0) for r in rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("toll worker: platform volume read failed: %s", exc)
        return 0


def compute_charge(plan: dict, volume: int, quantity: float = 1) -> dict[str, Any]:
    """Pure: compute the toll for one queued call from the plan.

    Pay-per-use (derivative_type null): per_call_cents x quantity, with the
    volume tier's per_call_cents overriding the plan base when tiers exist.
    """
    params = plan.get("derivative_params") or {}
    tiers = params.get("volume_tiers") or []
    tier = resolve_tier(volume, tiers) if tiers else None
    per_call = (
        int(tier["per_call_cents"]) if tier else int(plan.get("per_call_cents", 0))
    )
    return {
        "amount_cents": int(per_call * quantity),
        "applied_tier": tier["tier"] if tier else None,
        "per_call_cents": per_call,
    }


def _retry_at(attempts: int) -> str:
    delay = BASE_RETRY_SECONDS * (2 ** min(attempts, 6))
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()


def drain_queue(db, batch_size: int = 100) -> dict[str, Any]:
    """Drain due toll jobs. Returns counts. Never raises."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        jobs = (
            db.table("toll_emit_queue")
            .select("*")
            .eq("status", "queued")
            .or_(f"next_retry_at.is.null,next_retry_at.lte.{now}")
            .order("created_at")
            .limit(batch_size)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.error("toll worker: queue read failed: %s", exc)
        return {"status": "failed", "reason": "db_error"}

    done = skipped = dead = 0
    volume = get_platform_volume(db)
    for job in jobs:
        try:
            outcome = _process_job(db, job, volume)
            done += outcome == "done"
            skipped += outcome == "skipped"
            dead += outcome == "dead"
        except Exception as exc:  # noqa: BLE001 - per-job isolation
            log.error("toll worker: job %s failed: %s", job.get("id"), exc)
            _fail_job(db, job)
            dead += 1
    return {"status": "ok", "done": done, "skipped": skipped, "dead": dead}


def _process_job(db, job: dict, volume: int) -> str:
    key = job["idempotency_key"]
    key_id = job["key_id"]
    # Dedupe first, namespaced per API key (TW-569 MAJOR 2): two clients
    # reusing the same key value must never collide. Never double-toll.
    seen = (
        db.table("data_toll_dedupe")
        .select("key_id")
        .eq("key_id", key_id)
        .eq("idempotency_key", key)
        .limit(1)
        .execute()
        .data
        or []
    )
    if seen:
        _mark(db, job["id"], "done")
        return "skipped"

    plan_rows = (
        (
            db.table("data_api_plans")
            .select("id, per_call_cents, derivative_type, derivative_params")
            .eq("id", job["plan_id"])
            .limit(1)
            .execute()
            .data
            or []
        )
        if job.get("plan_id")
        else []
    )
    plan = plan_rows[0] if plan_rows else {"per_call_cents": 0, "derivative_params": {}}
    charge = compute_charge(plan, volume)

    toll_service.record_toll_event(
        db,
        org_id=job["billing_org_id"],
        event_type="data.benchmark.query",
        quantity=1,
        idempotency_key=f"toll:{key_id}:{key}",
        source_type="data_api",
        source_id=key_id,
        amount_cents=charge["amount_cents"],
        applied_tier=charge["applied_tier"],
        plan_id=job.get("plan_id"),
    )
    db.table("data_toll_dedupe").insert(
        {
            "key_id": key_id,
            "idempotency_key": key,
            "endpoint": job["endpoint"],
        }
    ).execute()
    _mark(db, job["id"], "done")
    return "done"


def _mark(db, job_id: str, status: str) -> None:
    db.table("toll_emit_queue").update({"status": status}).eq("id", job_id).execute()


def _fail_job(db, job: dict) -> None:
    attempts = int(job.get("attempts") or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        _mark(db, job["id"], "dead")
        log.error("toll worker: job %s dead after %d attempts", job.get("id"), attempts)
    else:
        db.table("toll_emit_queue").update(
            {
                "attempts": attempts,
                "next_retry_at": _retry_at(attempts),
            }
        ).eq("id", job["id"]).execute()
