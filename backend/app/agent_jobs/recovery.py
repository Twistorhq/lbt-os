"""TW-953 (TW-948-B5): interruption recovery.

A worker that dies mid-fire leaves the fire row in 'running'
forever (Rex TW-952 F3 tail, confirmed by his retest probe P4:
a poisoned job's fire row is left 'running'). Nothing in B4
ever looks at it again: the slot is already fired, so the
ticker moves on, and the run's external steps hang in an
unknown state.

The reaper closes that hole with the spec's interruption
contract (Section 8.5): a fire stranded in 'running' past the
staleness threshold is parked as 'interrupted' (a conditional
transition, so a fire that finished in the meantime is never
touched) and its job is paused for review (status 'review':
the ticker scans 'active' only, so a job in review NEVER
auto-resumes and never fires again on its own). Parked fires
get exactly one owner alert, keyed to the durable transition:
reaping twice parks nothing the second time and alerts nobody.

Resumption is an operator action with a gate:
resume_after_review runs reconciliation (apply=True) over the
job's interrupted fires first. Any step classified unknown
refuses the resume and names the step: an unknown outcome is
surfaced to a human, never auto-retried. Only when every
planned step is classified done / not done / reconciled does
the job return to active, with its next slot recomputed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import reconciliation, service
from .models import AgentJobError

log = logging.getLogger(__name__)

__all__ = ["reap_stranded_fires", "resume_after_review"]

DEFAULT_STALE_AFTER = timedelta(minutes=30)


def reap_stranded_fires(
    db: Any,
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    alerter: Callable[..., str] | None = None,
) -> dict:
    """Park every fire stranded in 'running' past the threshold.

    Scans across orgs (the ticker's posture); each fire is
    handled in its own (org, agent) scope. Fresh running fires
    (a live worker may still own them) are counted, not touched.
    Returns {"reaped": [...], "skipped_fresh": int}.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = (
        db.table("agt_job_fires").select("*").eq("status", "running").execute().data
        or []
    )
    reaped: list[dict] = []
    skipped_fresh = 0
    for fire in rows:
        started = service._parse_dt(fire.get("started_at")) or service._parse_dt(
            fire.get("created_at")
        )
        if started is None or now - started < stale_after:
            skipped_fresh += 1
            continue
        org_id = fire.get("org_id")
        agent_id = fire.get("agent_identity_id")
        fire_id = fire.get("id")
        updated = service.update_fire(
            db,
            org_id,
            agent_id,
            fire_id,
            from_statuses=("running",),
            status="interrupted",
            error=(
                "worker lost mid-run; fire parked for operator review "
                "(never auto-resumed)"
            ),
            finished_at=now.isoformat(),
        )
        if updated is None or updated.get("status") != "interrupted":
            continue  # lost the race with a live finish; not ours to park
        job = service.get_job(db, org_id, agent_id, fire.get("job_id"))
        if job is not None and job.get("status") in ("active", "paused"):
            service._update_job_row(
                db,
                org_id,
                agent_id,
                job["id"],
                {
                    "status": "review",
                    "review_reason": (
                        f"fire {fire_id} interrupted mid-run; "
                        "reconciliation required before resume"
                    ),
                    "review_fire_id": fire_id,
                    "updated_at": now.isoformat(),
                },
            )
        if alerter is not None and job is not None:
            try:
                alerter(
                    db,
                    org_id,
                    job,
                    f"fire {fire_id} was interrupted mid-run and is parked "
                    "for review; the job is paused and will not resume "
                    "until reconciliation clears it",
                )
            except Exception as exc:
                log.error(
                    "agent_jobs: interruption alert failed for fire %s: %s",
                    fire_id,
                    exc,
                )
        log.error(
            "agent_jobs: fire %s (job %s) stranded in running; "
            "parked as interrupted, job paused for review",
            fire_id,
            fire.get("job_id"),
        )
        reaped.append(
            {
                "fire_id": fire_id,
                "fire_key": fire.get("fire_key"),
                "job_id": fire.get("job_id"),
                "status": "interrupted",
            }
        )
    return {"reaped": reaped, "skipped_fresh": skipped_fresh}


def resume_after_review(
    db: Any,
    org_id: str,
    agent_id: str,
    job_id: str,
    *,
    executor_factory: Callable[..., list],
    toll_sink: Callable[..., dict] | None = None,
    toll_reader: Callable[..., list] | None = None,
    now: datetime | None = None,
) -> dict:
    """The operator resume gate for a job paused for review.

    Reconciles every interrupted fire of the job (apply=True:
    executed-but-untolled steps are tolled exactly once). Any
    unknown step refuses the resume, by name. On success the
    job returns to active via the normal resume path (next slot
    recomputed; review markers cleared). The interrupted fire
    rows stay interrupted: they are history, not work to redo.
    """
    job = service.get_job(db, org_id, agent_id, job_id)
    if job is None:
        raise AgentJobError(f"job {job_id!r} not found in scope")
    if job.get("status") != "review":
        raise AgentJobError(
            f"job {job_id!r} is {job.get('status')!r}; "
            "only a job paused for review resumes through reconciliation"
        )
    fires = service.list_fires_for_job(db, org_id, agent_id, job_id)
    reports = []
    for fire in fires:
        if fire.get("status") != "interrupted":
            continue
        report = reconciliation.reconcile_fire(
            db,
            org_id,
            agent_id,
            fire["id"],
            executor_factory=executor_factory,
            toll_sink=toll_sink,
            toll_reader=toll_reader,
            apply=True,
            now=now,
        )
        if report["unknown_steps"]:
            raise AgentJobError(
                "resume refused: fire "
                f"{fire['fire_key']!r} has steps with unknown outcomes "
                f"({', '.join(report['unknown_steps'])}); a human must "
                "resolve them before this job resumes"
            )
        reports.append(report)
    resumed = service.resume_job(db, org_id, agent_id, job_id, now=now)
    return {"job": resumed, "reports": reports}
