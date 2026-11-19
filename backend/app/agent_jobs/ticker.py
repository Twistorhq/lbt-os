"""TW-952 (TW-948-B4) + TW-953 (TW-948-B5): the job ticker.

One tick scans active job definitions across orgs (the flows
tick pattern), and for each org whose enable flag is ON, fires
the jobs that are due. Failure posture (Rex's gate): a ticker
that cannot scan fails LOUD with JobTickerError, never silently
dead; every other failure is contained PER JOB: the whole
per-job body runs inside a try/except that logs the failure,
records it in the tick summary, and continues to the next job.
A single poisoned job never wedges the tick for the others
(Rex F1).

TW-953 additions:

- Preflight before any fire: definition validation always runs
  (bad cron, bad timezone, corrupt next_fire_at, empty prompt
  are quarantined as BLOCKED with one owner alert, not retried
  as a fresh failure every tick), and when a PreflightChecker
  is wired its external checks (credentials, skills, delivery,
  ledger) block the same way. A blocked job creates no task
  and spends nothing.
- The stranded-fire reaper runs first when reap_stale_after is
  set: fires stranded in 'running' are parked as interrupted
  and their jobs paused for review (recovery.py). They never
  auto-resume.
- Catch-up accounting counts slots, not events (Rex F8): when
  a catch-up fire collapses several scheduled slots into one
  run, the collapsed count lands in summary["missed_collapsed"];
  when a stale slot is skipped, every skipped slot counts in
  summary["missed_skipped"].
- Summary accuracy (Rex P14 / Rosa nit): a job whose fire
  succeeded but whose post-fire advance write failed is listed
  in "fired" and in "advance_failed" ONLY, never also in
  "failed"/"errors". The fire is durable; the advance heals on
  the next tick through the already_fired path.
- A durable heartbeat records every tick's outcome (and the
  scan failure's) in agt_job_tick_heartbeats, so a dead
  scheduler is visible in-system (heartbeat.py, Rex F7 tail).
- Fix round (Rosa MF3): the tick WIRES the two passes that
  used to be operator-only. preflight_scan runs at the head of
  every tick, so a poisoned definition is quarantined when it
  lands, even if the job is not yet due; and when a
  health_alerter is wired, check_scheduler_health evaluates
  the PREVIOUS heartbeat at the head of the tick, so a
  scheduler that died between ticks alerts through the tick
  path itself (GET /tick-health runs the same check). Both
  passes are contained: a failure in either is logged and
  listed in the summary, and never wedges the tick.

Due semantics and ordering are B4's: next_fire_at is the slot;
fire-first ordering with the namespaced fire_key UNIQUE and a
CAS advance make a second fire for the same slot impossible
and a stranded slot recoverable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..persistence.models import DuplicateFireError, workspace_id_for
from . import heartbeat, service
from . import preflight as preflight_mod
from .cron import CronSchedule
from .execution import RunControl
from .models import AgentJobError

log = logging.getLogger(__name__)

# Slot counting is bounded: a minutely job stale for months
# would otherwise iterate forever. The cap is far above any
# catch-up window the definition allows (7 days of minutes is
# 10080); a count that hits it is reported as the cap.
_MAX_SLOT_COUNT = 50_000


class JobTickerError(RuntimeError):
    """The tick itself failed. Loud by design: a silently dead
    ticker is the failure Section 8.5 calls out by name."""


def _count_slots(
    job: dict, first: datetime, last: datetime, *, include_first: bool
) -> int:
    """Scheduled instants t for this job with first </<= t < last.

    One-shot jobs have exactly one instant. Cron iteration jumps
    by schedule boundaries (CronSchedule.next_at_or_after), and
    the count is capped at _MAX_SLOT_COUNT.
    """
    if not job.get("cron_expr"):
        return 1 if include_first else 0
    sched = CronSchedule(job["cron_expr"])
    tz_name = job.get("timezone") or "UTC"
    cursor = first if include_first else first + timedelta(minutes=1)
    count = 0
    while count < _MAX_SLOT_COUNT:
        t = sched.next_at_or_after(cursor, tz_name)
        if t >= last:
            break
        count += 1
        cursor = t + timedelta(minutes=1)
    return count


def _safe_advance(
    db: Any, job: dict, now: datetime, nxt: datetime | None, summary: dict
) -> None:
    """Advance, but never let an advance failure reclassify the
    job's outcome (Rex P14): the failure is logged and listed
    under advance_failed, and the next tick heals it."""
    try:
        _advance(db, job, now, nxt=nxt)
    except Exception as exc:
        log.error(
            "agent_jobs: advance failed for job %s (heals next tick): %s",
            job.get("id"),
            exc,
        )
        summary["advance_failed"].append(
            {"job_id": job.get("id"), "status": "advance_failed", "error": str(exc)}
        )


def _block(
    db: Any,
    job: dict,
    reason: str,
    *,
    alerter: Callable[..., str] | None,
    now: datetime,
    summary: dict,
) -> None:
    """Quarantine one job at preflight: blocked record, one
    alert, and a summary entry under blocked + errors (never
    under failed: the job did not run and fail, it was refused).
    """
    service.block_job(
        db,
        job.get("org_id"),
        job.get("agent_identity_id"),
        job,
        reason,
        alerter=alerter,
        now=now,
    )
    entry = {"job_id": job.get("id"), "status": "blocked", "error": reason}
    summary["blocked"].append(entry)
    summary["errors"].append(entry)
    log.error("agent_jobs: job %s blocked at preflight: %s", job.get("id"), reason)


def tick(
    db: Any,
    *,
    now: datetime | None = None,
    executor_factory: Callable[..., list],
    toll_sink: Callable[..., dict] | None = None,
    control: RunControl | None = None,
    enabled_orgs: set[str] | None = None,
    preflight: Any = None,
    alerter: Callable[..., str] | None = None,
    reap_stale_after: timedelta | None = None,
    record_heartbeat: bool = True,
    health_alerter: Callable[[dict], str] | None = None,
    run_preflight_scan: bool = True,
) -> dict:
    """Fire every due job once. Returns a summary dict with the
    fired outcomes, blocked jobs, failures, reaped fires, and
    skip/collapse counts."""
    if executor_factory is None:
        raise AgentJobError("ticker requires an executor_factory")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    summary: dict[str, Any] = {
        "fired": [],
        "blocked": [],
        "failed": [],
        "errors": [],
        "reaped": [],
        "advance_failed": [],
        "missed_skipped": 0,
        "missed_collapsed": 0,
        "skipped_flag_off": 0,
        "already_fired": 0,
    }
    if reap_stale_after is not None:
        try:
            from .recovery import reap_stranded_fires

            reaped = reap_stranded_fires(
                db, now=now, stale_after=reap_stale_after, alerter=alerter
            )
            summary["reaped"] = reaped["reaped"]
        except Exception as exc:
            log.error("agent_jobs: reaper failed: %s", exc)
            summary["reaper_error"] = str(exc)
    if health_alerter is not None:
        # Rosa MF3: the dead-scheduler alert has a production
        # caller. Evaluated against the PREVIOUS tick's
        # heartbeat, before this tick records a fresh one, and
        # contained: a health-check failure never wedges a tick.
        try:
            summary["scheduler_health"] = heartbeat.check_scheduler_health(
                db, now=now, health_alerter=health_alerter
            )
        except Exception as exc:
            log.error("agent_jobs: scheduler health check failed: %s", exc)
            summary["scheduler_health_error"] = str(exc)
    if run_preflight_scan:
        # Rosa MF3: the definition quarantine has a production
        # caller. It runs before the due scan so jobs it blocks
        # leave the active set this same tick, due or not.
        # Contained per the B4 posture: block_job contains each
        # job, and a scan-level failure is logged and listed,
        # never raised into the tick.
        try:
            scan = preflight_mod.preflight_scan(db, alerter=alerter)
            for entry in scan["blocked"]:
                blocked_entry = {
                    "job_id": entry["job_id"],
                    "status": "blocked",
                    "error": entry["error"],
                }
                summary["blocked"].append(blocked_entry)
                summary["errors"].append(blocked_entry)
            summary["preflight_scan_ok"] = scan["ok"]
        except Exception as exc:
            log.error("agent_jobs: preflight scan failed: %s", exc)
            summary["preflight_scan_error"] = str(exc)
    try:
        query = db.table("agt_jobs").select("*").eq("status", "active")
        if hasattr(query, "order"):
            query = query.order("next_fire_at")
        rows = query.execute().data or []
    except Exception as exc:
        # Rex F7: the scan failure is logged before it is raised,
        # and TW-953 records it durably before raising too.
        log.error("agent_jobs: job scan failed: %s", exc)
        if record_heartbeat:
            heartbeat.record_tick_heartbeat(
                db, now=now, status="scan_failed", error=str(exc)
            )
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
            try:
                slot = service._parse_dt(job.get("next_fire_at"))
            except Exception as exc:
                _block(
                    db,
                    job,
                    f"corrupt next_fire_at: {exc}",
                    alerter=alerter,
                    now=now,
                    summary=summary,
                )
                continue
            if slot is None or slot > now:
                continue
            if not service.jobs_enabled(org_id, enabled_orgs):
                summary["skipped_flag_off"] += 1
                continue
            # TW-953 preflight: quarantine poisoned definitions
            # and failed external checks BEFORE any fire, task,
            # or spend. The job blocks once and leaves the scan.
            failures = preflight_mod.validate_definition(job)
            if preflight is not None:
                failures.extend(preflight.check(db, org_id, agent_id, job).failures)
            if failures:
                _block(
                    db,
                    job,
                    "; ".join(failures),
                    alerter=alerter,
                    now=now,
                    summary=summary,
                )
                continue
            if now - slot > service.catch_up_window(job):
                nxt = service.next_fire_after_run(job, now)
                if nxt is not None:
                    summary["missed_skipped"] += _count_slots(
                        job, slot, nxt, include_first=True
                    )
                else:
                    summary["missed_skipped"] += 1
                _safe_advance(db, job, now, nxt, summary)
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
                _safe_advance(db, job, now, nxt, summary)
                continue
            if outcome["status"] == "failed":
                summary["failed"].append(outcome)
            else:
                summary["fired"].append(outcome)
            if nxt is not None:
                summary["missed_collapsed"] += _count_slots(
                    job, slot, nxt, include_first=False
                )
            _safe_advance(db, job, now, nxt, summary)
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
    if record_heartbeat:
        heartbeat.record_tick_heartbeat(db, now=now, status="ok", summary=summary)
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
