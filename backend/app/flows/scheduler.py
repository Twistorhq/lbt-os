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

    Supports '*' and plain integers — enough for outreach schedules
    ("0 9 * * 1-5" style ranges are NOT supported in V1; the validator
    rejects them loudly at definition time... except it doesn't yet.
    V1 honesty: only '*' and ints; anything else never matches, and the
    schedule simply never fires. Documented, not silent: tick reports
    the skip in its result.)
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
            return False  # unsupported syntax -> never fires (documented)
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
            .select("id,definition,status")
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
                # detect unsupported syntax so it isn't a silent never-fire
                if any(c not in "0123456789* " for c in cron):
                    skipped.append(row["id"])
                continue
            last = (row.get("definition") or {}).get("_last_tick")
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
                definition = dict(full.get("definition") or {})
                definition["_last_tick"] = now.strftime("%Y-%m-%d %H:%M")
                db.table("flw_workflows").update({"definition": definition}).eq(
                    "id", row["id"]
                ).execute()
            except Exception as exc:
                log.warning("flows: _last_tick write failed: %s", exc)
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
    """Continue a suspended run after its delay step."""
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
    ctx: dict = {}  # V1: context does not persist across suspension
    # (documented gap — the delay-resume context store is a follow-up)
    executed = run_row.get("steps_executed", 0)
    for step in steps[idx + 1 :]:
        if executed >= wf.budget.max_steps_per_run:
            executor._pause_for_cap(
                db, org_id, workflow_row["id"], "max_steps_per_run hit on resume"
            )
            executor._save_run(
                db,
                org_id,
                run_row["id"],
                workflow_row["id"],
                "halted",
                executed,
                "per-run step cap hit on resume",
            )
            return {"run_id": run_row["id"], "status": "halted", "budget_paused": True}
        try:
            status, _ = executor.execute_step(
                db, org_id, wf, run_row["id"], step, ctx, workflow_id=workflow_row["id"]
            )
        except Exception as exc:
            executor._save_run(
                db,
                org_id,
                run_row["id"],
                workflow_row["id"],
                "errored",
                executed,
                str(exc)[:500],
            )
            return {
                "run_id": run_row["id"],
                "status": "errored",
                "error": str(exc)[:500],
            }
        executed += 1
        if status == "suspend":
            return {"run_id": run_row["id"], "status": "suspended"}
        if status == "halt":
            return {"run_id": run_row["id"], "status": "halted", "budget_paused": True}
    executor._save_run(
        db, org_id, run_row["id"], workflow_row["id"], "completed", executed
    )
    return {"run_id": run_row["id"], "status": "completed", "steps_executed": executed}
