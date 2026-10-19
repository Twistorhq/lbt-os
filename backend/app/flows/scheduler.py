"""TW-625 Twistor Flows — schedule evaluation + delay resumption.

V1 has no background worker (Dre's lane). Instead, an external cron hits
POST /flows/tick, which fires due schedules and resumes suspended runs.
The tick endpoint is guarded by FLOWS_TICK_KEY and fails closed when unset.
"""

from __future__ import annotations

import datetime
import logging

from supabase import Client

from . import executor

log = logging.getLogger(__name__)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _cron_due(cron: str, now: datetime.datetime) -> bool:
    """Minimal 5-field cron matcher (minute hour dom month dow).

    Only '*' and plain integers are supported in V1 — anything else is
    rejected at definition time by models.validate_cron, so reaching
    here with exotic syntax means the definition predates the check.
    """
    fields = cron.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    checks = [
        (minute, now.minute),
        (hour, now.hour),
        (dom, now.day),
        (month, now.month),
        # cron dow: 0 and 7 both mean Sunday; python: Monday=0
        (dow, (now.weekday() + 1) % 7),
    ]
    for field, value in checks:
        if field == "*":
            continue
        try:
            if int(field) != value:
                return False
        except ValueError:
            return False
    return True


def tick(db: Client, now: datetime.datetime | None = None) -> dict:
    """Fire due schedules, resume due delays. Returns a result summary."""
    now = now or _utcnow()
    fired: list[str] = []
    resumed: list[str] = []
    skipped: list[str] = []

    # --- due schedules ---------------------------------------------------
    try:
        rows = (
            db.table("flw_workflows")
            .select("id,definition,status,last_tick_at")
            .eq("status", "active")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        log.warning("flows: tick schedule scan failed: %s", exc)
        return {
            "fired": fired,
            "resumed": resumed,
            "skipped": skipped,
            "error": "schedule scan failed",
        }

    for row in rows:
        try:
            trig = (row.get("definition") or {}).get("trigger") or {}
            if trig.get("type") != "schedule":
                continue
            cron = trig.get("cron") or ""
            if not _cron_due(cron, now):
                continue
            last = row.get("last_tick_at")
            if last and last >= now.strftime("%Y-%m-%d %H:%M"):
                continue  # already fired this minute
            try:
                full = (
                    db.table("flw_workflows")
                    .select("*")
                    .eq("id", row["id"])
                    .execute()
                    .data[0]
                )
            except Exception:
                continue
            org_id = full.get("org_id")
            result = executor.run_workflow(db, org_id, full, {})
            fired.append(f"{row['id']}:{result['status']}")
            try:
                db.table("flw_workflows").update(
                    {"last_tick_at": now.strftime("%Y-%m-%d %H:%M")}
                ).eq("id", row["id"]).execute()
            except Exception as exc:
                log.warning("flows: last_tick_at write failed: %s", exc)
        except Exception as exc:
            log.warning("flows: tick fire failed for %s: %s", row.get("id"), exc)

    # --- due delay resumptions -------------------------------------------
    try:
        suspended = (
            db.table("flw_runs")
            .select("id,org_id,workflow_id,resume_at,resume_step_id")
            .eq("status", "suspended")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        log.warning("flows: tick resume scan failed: %s", exc)
        return {"fired": fired, "resumed": resumed, "skipped": skipped}

    for run in suspended:
        try:
            resume_at = run.get("resume_at")
            if not resume_at or resume_at > now.isoformat():
                continue
            full = (
                db.table("flw_workflows")
                .select("*")
                .eq("id", run["workflow_id"])
                .execute()
                .data[0]
            )
            if full.get("status") != "active":
                continue
            result = resume_run(db, run["org_id"], full, run)
            resumed.append(f"{run['id']}:{result['status']}")
        except Exception as exc:
            log.warning("flows: tick resume failed for %s: %s", run.get("id"), exc)

    return {"fired": fired, "resumed": resumed, "skipped": skipped}


def resume_run(db: Client, org_id: str, workflow_row: dict, run_row: dict) -> dict:
    """Continue a suspended run after its delay step.

    Restores the persisted step context (so post-delay steps see the same
    variables the delay step saw) and persists again on a second
    suspension — multi-delay sequences resume forward, never re-send.
    """
    from . import models

    wf = models.validate_definition(workflow_row["definition"])
    steps = wf.steps
    # find the delay step that suspended us, continue after it
    idx = next(
        (i for i, s in enumerate(steps) if s.id == run_row.get("resume_step_id")), None
    )
    if idx is None:
        return {
            "run_id": run_row["id"],
            "status": "errored",
            "error": "resume step not found",
        }
    ctx: dict = dict(run_row.get("context") or {})
    state = executor._RunState(wf.budget.max_steps_per_run, workflow_row["id"])
    state.count = run_row.get("steps_executed", 0)
    for step in steps[idx + 1 :]:
        try:
            status, info = executor.execute_step(
                db,
                org_id,
                wf,
                run_row["id"],
                step,
                ctx,
                workflow_id=workflow_row["id"],
                _state=state,
            )
        except Exception as exc:
            executor._save_run(
                db,
                org_id,
                run_row["id"],
                workflow_row["id"],
                "errored",
                state.count,
                str(exc)[:500],
            )
            return {
                "run_id": run_row["id"],
                "status": "errored",
                "error": str(exc)[:500],
            }
        if status == "suspend":
            # Persist the NEW resume point + context: the next tick
            # continues forward from here, never re-runs what already sent.
            executor._save_run(
                db,
                org_id,
                run_row["id"],
                workflow_row["id"],
                "suspended",
                state.count,
                None,
                resume_at=info.get("resume_at"),
                resume_step=step.id,
                context=ctx,
            )
            return {
                "run_id": run_row["id"],
                "status": "suspended",
                "steps_executed": state.count,
                "resume_at": info.get("resume_at"),
            }
        if status == "halt":
            executor._save_run(
                db,
                org_id,
                run_row["id"],
                workflow_row["id"],
                "halted",
                state.count,
                "cap or breaker on resume",
            )
            return {
                "run_id": run_row["id"],
                "status": "halted",
                "budget_paused": True,
            }
    executor._save_run(
        db, org_id, run_row["id"], workflow_row["id"], "completed", state.count
    )
    return {
        "run_id": run_row["id"],
        "status": "completed",
        "steps_executed": state.count,
    }
