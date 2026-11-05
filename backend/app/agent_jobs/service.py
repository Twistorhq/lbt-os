"""TW-952 (TW-948-B4): persistent job service (management + firing).

Management surface: create, list, pause, resume, edit, trigger
now, remove, cancel run. Definitions live on the B1 tables
(agt_jobs, agt_job_fires, agt_job_continuity) through the B1
persistence service primitives; this module adds the fields B1
did not manage (next_fire_at maintenance, catch-up window,
one-shot instant) and the fire writer.

Carried requirements from the B1 gates (binding):
(a) fire keys are namespaced per job: fire_key stays globally
    UNIQUE per the toll-ledger precedent, so mint_fire_key
    prefixes every key with the job id.
(b) the fire writer masks summary and error with the B1
    persistence masking pipeline (mask_text) on write, closing
    the TW-958 fire-writer item at the source. B1's record_fire
    wrote only empty values; every later write goes through
    update_fire here.

The whole service ships behind a per-org enable flag defaulting
OFF (settings.agent_jobs_enabled_orgs, comma-separated org ids;
empty means no org is enabled). The flag gates firing (ticker
and trigger now); management reads and writes stay available so
an org can stage definitions before enablement.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..persistence import models as pmodels
from ..persistence import service as psvc
from ..persistence.models import DuplicateFireError, mask_text, workspace_id_for
from .cron import CronSchedule
from .execution import RunControl, build_execution_context, run_steps
from .models import DEFAULT_CATCH_UP_WINDOW_MINUTES, AgentJobError, JobEdit, JobSpec

log = logging.getLogger(__name__)

__all__ = [
    "cancel_run",
    "create_job",
    "edit_job",
    "fire_job",
    "get_fire",
    "get_job",
    "jobs_enabled",
    "list_jobs",
    "mint_fire_key",
    "pause_job",
    "remove_job",
    "resume_job",
    "trigger_now",
    "update_fire",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# -- enable flag ---------------------------------------------------


def jobs_enabled(org_id: str, enabled_orgs: set[str] | None = None) -> bool:
    """Per-org enable flag, default OFF for every org."""
    if enabled_orgs is not None:
        return org_id in enabled_orgs
    from ..config import settings

    raw = getattr(settings, "agent_jobs_enabled_orgs", "") or ""
    return org_id in {part.strip() for part in raw.split(",") if part.strip()}


# -- scoped row helpers ---------------------------------------------


def _update_job_row(db: Any, org_id: str, agent_id: str, job_id: str, payload: dict):
    updated = (
        db.table("agt_jobs")
        .update(payload)
        .eq("id", job_id)
        .eq("org_id", org_id)
        .eq("agent_identity_id", agent_id)
        .eq("workspace_id", workspace_id_for(org_id, agent_id))
        .execute()
        .data
    )
    return updated[0] if updated else None


def _require_job(db: Any, org_id: str, agent_id: str, job_id: str) -> dict:
    job = psvc.get_job(db, org_id, agent_id, job_id)
    if job is None:
        raise AgentJobError(f"job {job_id!r} not found in scope")
    return job


def _next_fire_for(job_like: dict, now: datetime) -> datetime | None:
    """The next scheduled instant for a definition, or None.

    One-shot jobs fire at their stored instant (past instants are
    returned as-is; the ticker's catch-up window decides whether
    a late one-shot still fires). Cron jobs resolve to the first
    matching minute at or after `now` in the job's timezone.
    """
    one_shot = _parse_dt(job_like.get("one_shot_at"))
    if one_shot is not None:
        return one_shot
    cron_expr = job_like.get("cron_expr")
    if not cron_expr:
        return None
    return CronSchedule(cron_expr).next_at_or_after(now, job_like["timezone"])


def next_fire_after_run(job: dict, now: datetime) -> datetime | None:
    """Where next_fire_at moves after a fire or a skipped slot."""
    if _parse_dt(job.get("one_shot_at")) is not None:
        return None
    if not job.get("cron_expr"):
        return None
    anchor = max(now, _parse_dt(job.get("next_fire_at")) or now)
    return CronSchedule(job["cron_expr"]).next_at_or_after(
        anchor + timedelta(minutes=1), job["timezone"]
    )


# -- management operations -------------------------------------------


def create_job(
    db: Any,
    org_id: str,
    agent_id: str,
    spec: JobSpec,
    *,
    created_by: str = "",
    now: datetime | None = None,
) -> dict:
    now = now or _now()
    row = psvc.create_job(
        db,
        org_id,
        agent_id,
        pmodels.JobCreate(
            name=spec.name,
            prompt=spec.prompt,
            skill_keys=list(spec.skill_keys),
            cron_expr=spec.cron_expr,
            timezone=spec.timezone,
            status="active",
        ),
        created_by=created_by,
    )
    job_like = {
        "cron_expr": spec.cron_expr,
        "one_shot_at": _iso(spec.one_shot_at),
        "timezone": spec.timezone,
    }
    updated = _update_job_row(
        db,
        org_id,
        agent_id,
        row["id"],
        {
            "next_fire_at": _iso(_next_fire_for(job_like, now)),
            "catch_up_window_minutes": spec.catch_up_window_minutes,
            "one_shot_at": _iso(spec.one_shot_at),
            "updated_at": _iso(now),
        },
    )
    return updated or _require_job(db, org_id, agent_id, row["id"])


def get_job(db: Any, org_id: str, agent_id: str, job_id: str) -> dict | None:
    return psvc.get_job(db, org_id, agent_id, job_id)


def list_jobs(
    db: Any, org_id: str, agent_id: str, *, include_archived: bool = False
) -> list[dict]:
    rows = psvc.list_jobs(db, org_id, agent_id)
    if include_archived:
        return rows
    return [r for r in rows if r.get("status") != "archived"]


def pause_job(db: Any, org_id: str, agent_id: str, job_id: str) -> dict:
    _require_job(db, org_id, agent_id, job_id)
    updated = _update_job_row(
        db,
        org_id,
        agent_id,
        job_id,
        {"status": "paused", "updated_at": _iso(_now())},
    )
    return updated or _require_job(db, org_id, agent_id, job_id)


def resume_job(
    db: Any,
    org_id: str,
    agent_id: str,
    job_id: str,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or _now()
    job = _require_job(db, org_id, agent_id, job_id)
    nxt = _next_fire_for(job, now)
    if nxt is not None and job.get("cron_expr") is None and nxt <= now:
        nxt = None  # a one-shot whose instant passed while paused never fires
    updated = _update_job_row(
        db,
        org_id,
        agent_id,
        job_id,
        {"status": "active", "next_fire_at": _iso(nxt), "updated_at": _iso(now)},
    )
    return updated or _require_job(db, org_id, agent_id, job_id)


def edit_job(
    db: Any,
    org_id: str,
    agent_id: str,
    job_id: str,
    patch: JobEdit,
    *,
    now: datetime | None = None,
) -> dict:
    """Edit definition fields only. Fire history and continuity
    are separate tables and are never touched here, so an edit
    cannot rewrite a job's history."""
    now = now or _now()
    job = _require_job(db, org_id, agent_id, job_id)
    payload: dict[str, Any] = {"updated_at": _iso(now)}
    fields = patch.model_fields_set
    if "name" in fields and patch.name is not None:
        payload["name"] = patch.name
    if "prompt" in fields and patch.prompt is not None:
        payload["prompt"] = patch.prompt
    if "skill_keys" in fields and patch.skill_keys is not None:
        payload["skill_keys"] = list(patch.skill_keys)
    if "timezone" in fields and patch.timezone is not None:
        payload["timezone"] = patch.timezone
    if (
        "catch_up_window_minutes" in fields
        and patch.catch_up_window_minutes is not None
    ):
        payload["catch_up_window_minutes"] = patch.catch_up_window_minutes
    schedule_changed = False
    if "cron_expr" in fields and patch.cron_expr is not None:
        payload["cron_expr"] = patch.cron_expr
        payload["one_shot_at"] = None
        schedule_changed = True
    if "one_shot_at" in fields and patch.one_shot_at is not None:
        payload["one_shot_at"] = _iso(patch.one_shot_at)
        payload["cron_expr"] = None
        schedule_changed = True
    if "timezone" in fields:
        schedule_changed = True
    if schedule_changed and job.get("status") == "active":
        merged = {**job, **payload}
        payload["next_fire_at"] = _iso(_next_fire_for(merged, now))
    updated = _update_job_row(db, org_id, agent_id, job_id, payload)
    return updated or _require_job(db, org_id, agent_id, job_id)


def remove_job(db: Any, org_id: str, agent_id: str, job_id: str) -> dict:
    """Archive the definition. History (fires, continuity) stays
    as data; an archived job never fires."""
    _require_job(db, org_id, agent_id, job_id)
    updated = _update_job_row(
        db,
        org_id,
        agent_id,
        job_id,
        {"status": "archived", "next_fire_at": None, "updated_at": _iso(_now())},
    )
    return updated or _require_job(db, org_id, agent_id, job_id)


# -- fire writer (carried requirements a and b) -----------------------


def mint_fire_key(job_id: str, slot: datetime) -> str:
    """The durable fire identity, namespaced per job (req a)."""
    return f"{job_id}:{_parse_dt(slot).isoformat()}"


def get_fire(db: Any, org_id: str, agent_id: str, fire_id: str) -> dict | None:
    rows = (
        db.table("agt_job_fires")
        .select("*")
        .eq("id", fire_id)
        .eq("org_id", org_id)
        .eq("agent_identity_id", agent_id)
        .eq("workspace_id", workspace_id_for(org_id, agent_id))
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def update_fire(
    db: Any,
    org_id: str,
    agent_id: str,
    fire_id: str,
    *,
    from_statuses: tuple[str, ...] | None = None,
    **fields: Any,
) -> dict | None:
    """The one fire writer. summary and error pass through the
    persistence masking pipeline on write (req b), always, so no
    caller can store a raw identifier on a fire row.

    When ``from_statuses`` is given the write is a CONDITIONAL
    transition: it lands only while the fire is still in one of
    those statuses (an atomic ``in_`` filter on the real client;
    a checked fallback on builders without ``in_``). Terminal
    and cancel writes pass ("pending", "running") so neither can
    overwrite a fire that already reached a terminal state, in
    particular a cancelled fire (Rosa MF2 / Rex F2). A refused
    transition returns the current row unchanged.
    """
    payload = dict(fields)
    if isinstance(payload.get("summary"), str):
        payload["summary"] = mask_text(payload["summary"])
    if isinstance(payload.get("error"), str):
        payload["error"] = mask_text(payload["error"])
    query = (
        db.table("agt_job_fires")
        .update(payload)
        .eq("id", fire_id)
        .eq("org_id", org_id)
        .eq("agent_identity_id", agent_id)
        .eq("workspace_id", workspace_id_for(org_id, agent_id))
    )
    if from_statuses is not None:
        if hasattr(query, "in_"):
            query = query.in_("status", list(from_statuses))
        else:
            current = get_fire(db, org_id, agent_id, fire_id)
            if current is None or current.get("status") not in from_statuses:
                return current
    updated = query.execute().data
    if updated:
        return updated[0]
    if from_statuses is not None:
        return get_fire(db, org_id, agent_id, fire_id)
    return None


def cancel_run(
    db: Any,
    org_id: str,
    agent_id: str,
    job_id: str,
    fire_id: str,
    *,
    control: RunControl | None = None,
) -> dict:
    """Cancel one in-flight run: fence the durable turn.

    Writes the durable fence (fire status 'cancelled') and the
    in-process fence. run_steps checks both before every step,
    so completed steps stay completed (and tolled exactly once)
    and nothing further runs.
    """
    fire = get_fire(db, org_id, agent_id, fire_id)
    if fire is None or fire.get("job_id") != job_id:
        raise AgentJobError(f"fire {fire_id!r} not found for job {job_id!r}")
    if fire.get("status") not in ("pending", "running"):
        raise AgentJobError(
            f"fire {fire_id!r} is {fire.get('status')!r}; only an in-flight "
            "run can be cancelled"
        )
    if control is not None:
        control.fence(fire_id)
    updated = update_fire(
        db,
        org_id,
        agent_id,
        fire_id,
        from_statuses=("pending", "running"),
        status="cancelled",
        finished_at=_iso(_now()),
    )
    return updated or fire


# -- firing ------------------------------------------------------------


def fire_job(
    db: Any,
    org_id: str,
    agent_id: str,
    job: dict,
    slot: datetime,
    *,
    executor_factory: Callable[..., list],
    toll_sink: Callable[..., dict] | None = None,
    control: RunControl | None = None,
    trigger: str = "schedule",
) -> dict:
    """Fire one job once for one slot. The full fire lifecycle:
    durable fire record first, continuity read before the run,
    masked writes throughout, continuity update after."""
    job_id = job["id"]
    fire_key = mint_fire_key(job_id, slot)
    try:
        fire = psvc.record_fire(
            db,
            org_id,
            agent_id,
            job_id,
            pmodels.FireCreate(fire_key=fire_key, scheduled_for=_iso(slot)),
        )
    except DuplicateFireError:
        raise
    fire_id = fire["id"]
    update_fire(
        db,
        org_id,
        agent_id,
        fire_id,
        from_statuses=("pending",),
        status="running",
        started_at=_iso(_now()),
    )
    continuity = psvc.get_continuity(db, org_id, agent_id, job_id) or {}
    ctx = build_execution_context(
        org_id=org_id,
        agent_id=agent_id,
        job_id=job_id,
        fire_id=fire_id,
        fire_key=fire_key,
        prompt=job.get("prompt", ""),
        skill_keys=job.get("skill_keys") or [],
        scheduled_for=slot,
        timezone_name=job.get("timezone", "UTC"),
        continuity_last_summary=continuity.get("last_run_summary", ""),
        continuity_surfaced_ids=continuity.get("surfaced_item_ids") or [],
    )

    def fence_check(fid: str) -> bool:
        if control is not None and control.is_fenced(fid):
            return True
        row = get_fire(db, org_id, agent_id, fid)
        return bool(row and row.get("status") == "cancelled")

    try:
        steps = executor_factory(ctx)
        outcome = run_steps(
            ctx, steps, toll_sink=toll_sink, db=db, fence_check=fence_check
        )
    except Exception as exc:  # masked at the writer; reported, not hidden
        # A cancel that landed while the failing step ran wins
        # over the failure write, same fence rule as the success
        # path below.
        if fence_check(fire_id):
            return {
                "fire_id": fire_id,
                "fire_key": fire_key,
                "job_id": job_id,
                "status": "cancelled",
                "summary": "",
                "error": None,
                "surfaced_item_ids": [],
                "task_id": None,
                "completed_steps": [],
                "trigger": trigger,
            }
        update_fire(
            db,
            org_id,
            agent_id,
            fire_id,
            from_statuses=("pending", "running"),
            status="failed",
            error=str(exc),
            finished_at=_iso(_now()),
        )
        log.warning("agent_jobs: fire %s failed: %s", fire_key, exc)
        return {
            "fire_id": fire_id,
            "fire_key": fire_key,
            "job_id": job_id,
            "status": "failed",
            "summary": "",
            "error": mask_text(str(exc)),
            "surfaced_item_ids": [],
            "task_id": None,
            "completed_steps": [],
            "trigger": trigger,
        }
    # Re-check the fence after run_steps, before ANY terminal
    # write: run_steps checks before each step, so a cancel
    # landing inside the FINAL step is only visible here (Rosa
    # MF2 / Rex F2). A fenced fire stays cancelled in the record,
    # its terminal write is a conditional no-op against the
    # cancelled row, and continuity is NOT updated from it.
    if fence_check(fire_id):
        outcome.status = "cancelled"
        terminal = update_fire(
            db,
            org_id,
            agent_id,
            fire_id,
            from_statuses=("pending", "running"),
            status="cancelled",
            summary=outcome.summary,
            finished_at=_iso(_now()),
        )
        # When cancel_run already wrote the row, the conditional
        # write above returns that cancelled row untouched; when
        # only the in-process fence was set, it lands cancelled.
        _ = terminal
        return {
            "fire_id": fire_id,
            "fire_key": fire_key,
            "job_id": job_id,
            "status": "cancelled",
            "summary": outcome.summary,
            "surfaced_item_ids": outcome.surfaced_item_ids,
            "task_id": outcome.task_id,
            "completed_steps": outcome.completed_steps,
            "trigger": trigger,
        }
    if outcome.task_id:
        update_fire(
            db,
            org_id,
            agent_id,
            fire_id,
            from_statuses=("pending", "running"),
            task_id=outcome.task_id,
        )
    terminal = update_fire(
        db,
        org_id,
        agent_id,
        fire_id,
        from_statuses=("pending", "running"),
        status=outcome.status,
        summary=outcome.summary,
        finished_at=_iso(_now()),
    )
    if terminal is not None and terminal.get("status") == "cancelled":
        # Lost a race with cancel_run between the fence check
        # and the terminal write: the conditional transition was
        # refused, the record stays cancelled, and continuity
        # must not be updated from a cancelled run.
        return {
            "fire_id": fire_id,
            "fire_key": fire_key,
            "job_id": job_id,
            "status": "cancelled",
            "summary": outcome.summary,
            "surfaced_item_ids": outcome.surfaced_item_ids,
            "task_id": outcome.task_id,
            "completed_steps": outcome.completed_steps,
            "trigger": trigger,
        }
    psvc.update_continuity(
        db,
        org_id,
        agent_id,
        job_id,
        last_fire_id=fire_id,
        summary=outcome.summary,
        surfaced_item_ids=outcome.surfaced_item_ids,
    )
    return {
        "fire_id": fire_id,
        "fire_key": fire_key,
        "job_id": job_id,
        "status": outcome.status,
        "summary": outcome.summary,
        "surfaced_item_ids": outcome.surfaced_item_ids,
        "task_id": outcome.task_id,
        "completed_steps": outcome.completed_steps,
        "trigger": trigger,
    }


def trigger_now(
    db: Any,
    org_id: str,
    agent_id: str,
    job_id: str,
    *,
    executor_factory: Callable[..., list],
    toll_sink: Callable[..., dict] | None = None,
    control: RunControl | None = None,
    now: datetime | None = None,
    enabled_orgs: set[str] | None = None,
) -> dict:
    """Fire a job immediately, outside its schedule. An explicit
    owner action: allowed on a paused job, refused when the org
    flag is off, on an archived or blocked job, or twice in the
    same minute (the durable fire identity refuses the second)."""
    if not jobs_enabled(org_id, enabled_orgs):
        raise AgentJobError(f"agent jobs are not enabled for org {org_id!r}")
    job = _require_job(db, org_id, agent_id, job_id)
    if job.get("status") in ("archived", "blocked"):
        raise AgentJobError(f"job {job_id!r} is {job.get('status')!r}; cannot fire")
    now = now or _now()
    slot = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    try:
        return fire_job(
            db,
            org_id,
            agent_id,
            job,
            slot,
            executor_factory=executor_factory,
            toll_sink=toll_sink,
            control=control,
            trigger="manual",
        )
    except DuplicateFireError as exc:
        raise AgentJobError(
            f"job {job_id!r} already fired for minute {slot.isoformat()}"
        ) from exc


def catch_up_window(job: dict) -> timedelta:
    minutes = job.get("catch_up_window_minutes")
    if minutes is None:
        minutes = DEFAULT_CATCH_UP_WINDOW_MINUTES
    return timedelta(minutes=int(minutes))
