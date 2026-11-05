"""TW-952 (TW-948-B4): the job ticker.

One tick scans active job definitions across orgs (the flows
tick pattern), and for each org whose enable flag is ON, fires
the jobs that are due. Failure posture (Rex's gate): a ticker
that cannot scan fails LOUD with JobTickerError, never silently
dead; a single job's fire failure is recorded on its fire row
(masked) and counted in the summary, and never wedges the tick
for the other jobs.

Due semantics: the job row's next_fire_at is the slot. A slot
inside the job's catch-up window fires once; a slot older than
the window is skipped (counted as missed_skipped) and the
schedule advances. Duplicate protection is layered: next_fire_at
advances before the run starts, and the durable fire identity
(per-job namespaced fire_key, plus the (job_id, scheduled_for)
UNIQUE) refuses a second record for the same slot even if two
ticks overlap.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from ..persistence.models import DuplicateFireError
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
        "missed_skipped": 0,
        "skipped_flag_off": 0,
        "already_fired": 0,
    }
    try:
        rows = (
            db.table("agt_jobs").select("*").eq("status", "active").execute().data or []
        )
    except Exception as exc:
        raise JobTickerError(f"job scan failed: {exc}") from exc

    for job in rows:
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
        # Advance BEFORE running: an overlapping tick then sees a
        # future slot, and the durable fire identity is the second
        # layer if two ticks race the same slot anyway.
        _advance(db, job, now)
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
            continue
        if outcome["status"] == "failed":
            summary["failed"].append(outcome)
        else:
            summary["fired"].append(outcome)
    return summary


def _advance(db: Any, job: dict, now: datetime) -> None:
    nxt = service.next_fire_after_run(job, now)
    service._update_job_row(
        db,
        job.get("org_id"),
        job.get("agent_identity_id"),
        job["id"],
        {"next_fire_at": service._iso(nxt), "updated_at": service._iso(now)},
    )
