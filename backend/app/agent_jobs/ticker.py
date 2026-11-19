"""TW-952 (TW-948-B4): the job ticker.

One tick scans active job definitions across orgs (the flows
tick pattern), and for each org whose enable flag is ON, fires
the jobs that are due. Failure posture (Rex's gate): a ticker
that cannot scan fails LOUD with JobTickerError, never silently
dead; every other failure is contained PER JOB: the whole
per-job body (slot parse, catch-up window, next-slot
computation, fire record, continuity reads/writes) runs inside
a try/except that logs the failure, records it in the tick
summary ("errors" and "failed"), and continues to the next
job. A single poisoned job never wedges the tick for the
others (Rex F1).

Due semantics: the job row's next_fire_at is the slot. A slot
inside the job's catch-up window fires once; a slot older than
the window is skipped (counted as missed_skipped) and the
schedule advances.

Ordering and duplicate protection (Rex F3/F4): the fire row is
recorded BEFORE next_fire_at advances (fire-first). A crash
between the two leaves a durable fire row for the slot, so the
next tick sees the slot as already fired (counted
already_fired) and heals the advance instead of stranding the
slot silently with no trace. The advance itself is a
compare-and-swap on the slot it was computed from: it lands
only while next_fire_at still equals the scanned value, so a
concurrent tick's advance is never overwritten. Fire-first
plus the per-job namespaced fire_key UNIQUE (whose Postgres
violation record_fire translates to DuplicateFireError) plus
the CAS advance together make a second fire for the same slot
impossible and a stranded slot recoverable; that is why no
additional locking is needed here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from ..persistence.models import DuplicateFireError, workspace_id_for
from . import service
from .execution import RunControl
from .models import AgentJobError

log = logging.getLogger(__name__)


class JobTickerError(RuntimeError):
    """The tick itself failed. Loud by design: a silently dead
    ticker is the failure Section 8.5 calls out by name."""


def tick(
    db: Any,
    *,
    now: datetime | None = None,
    executor_factory: Callable[..., list],
    toll_sink: Callable[..., dict] | None = None,
    control: RunControl | None = None,
    enabled_orgs: set[str] | None = None,
) -> dict:
    """Fire every due job once. Returns a summary dict with the
    fired outcomes, failures, and skip counts."""
    if executor_factory is None:
        raise AgentJobError("ticker requires an executor_factory")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    summary: dict[str, Any] = {
        "fired": [],
        "failed": [],
        "errors": [],
        "missed_skipped": 0,
        "skipped_flag_off": 0,
        "already_fired": 0,
    }
    try:
        query = db.table("agt_jobs").select("*").eq("status", "active")
        if hasattr(query, "order"):
            query = query.order("next_fire_at")
        rows = query.execute().data or []
    except Exception as exc:
        # Rex F7: the scan failure is logged before it is raised;
        # a tick caller seeing only a bare 500 was the old gap.
        log.error("agent_jobs: job scan failed: %s", exc)
        raise JobTickerError(f"job scan failed: {exc}") from exc
    # Deterministic scan order regardless of store return order
    # (Rex F1): earliest slot first, job id breaks ties.
    rows = sorted(
        rows,
        key=lambda r: (str(r.get("next_fire_at") or ""), str(r.get("id") or "")),
    )

    for job in rows:
        job_id = job.get("id")
        try:
            org_id = job.get("org_id")
            agent_id = job.get("agent_identity_id")
            slot = service._parse_dt(job.get("next_fire_at"))
            if slot is None or slot > now:
                continue
            if not service.jobs_enabled(org_id, enabled_orgs):
                summary["skipped_flag_off"] += 1
                continue
            if now - slot > service.catch_up_window(job):
                summary["missed_skipped"] += 1
                _advance(db, job, now)
                continue
            # Compute the next slot BEFORE firing (a bad cron or
            # timezone fails here, contained per job, and the job
            # does not fire on a schedule that cannot advance),
            # but PERSIST the advance only after the fire row
            # exists: fire-first ordering (Rex F3).
            nxt = service.next_fire_after_run(job, now)
            try:
                outcome = service.fire_job(
                    db,
                    org_id,
                    agent_id,
                    job,
                    slot,
                    executor_factory=executor_factory,
                    toll_sink=toll_sink,
                    control=control,
                )
            except DuplicateFireError:
                summary["already_fired"] += 1
                # Heal an advance stranded by an earlier crash:
                # the fire row exists, so move past the slot.
                _advance(db, job, now, nxt=nxt)
                continue
            if outcome["status"] == "failed":
                summary["failed"].append(outcome)
            else:
                summary["fired"].append(outcome)
            _advance(db, job, now, nxt=nxt)
        except DuplicateFireError:
            # A duplicate surfacing outside fire_job's own guard
            # (e.g. a translated insert race on a later write) is
            # still already-fired, never a tick failure.
            summary["already_fired"] += 1
            continue
        except Exception as exc:
            log.error("agent_jobs: tick failed for job %s: %s", job_id, exc)
            entry = {
                "job_id": job_id,
                "status": "failed",
                "error": str(exc),
            }
            summary["failed"].append(entry)
            summary["errors"].append(entry)
            continue
    return summary


def _advance(db: Any, job: dict, now: datetime, *, nxt: datetime | None = None) -> None:
    """Advance next_fire_at with a compare-and-swap (Rex F4).

    The update lands only while next_fire_at still equals the
    value this tick scanned; if a concurrent tick already
    advanced the job, this write matches nothing and is logged
    instead of overwriting the newer slot.
    """
    if nxt is None:
        nxt = service.next_fire_after_run(job, now)
    payload = {
        "next_fire_at": service._iso(nxt),
        "updated_at": service._iso(now),
    }
    updated = (
        db.table("agt_jobs")
        .update(payload)
        .eq("id", job["id"])
        .eq("org_id", job.get("org_id"))
        .eq("agent_identity_id", job.get("agent_identity_id"))
        .eq(
            "workspace_id",
            workspace_id_for(job.get("org_id"), job.get("agent_identity_id")),
        )
        .eq("next_fire_at", job.get("next_fire_at"))
        .execute()
        .data
    )
    if not updated:
        log.warning(
            "agent_jobs: advance CAS missed for job %s (next_fire_at already moved)",
            job.get("id"),
        )
