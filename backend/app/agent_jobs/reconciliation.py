"""TW-953 (TW-948-B5): the step journal and exactly-once
reconciliation.

The durability problem (spec Section 8.5, Rex TW-952 F6): a
step's external effect and its toll are two writes. A worker
can die, or the toll sink can throw, between them, leaving a
step that EXECUTED but was never tolled, or worse, a step whose
execution itself is uncertain. Retrying blindly double-sends;
never retrying silently drops work. The way out is evidence,
written in the right order:

- run_steps journals 'started' BEFORE the handler runs and
  'executed' AFTER it returns, BEFORE the toll posts (the
  journal seam in execution.py, wired by fire_job to the
  durable agt_job_fire_steps table through make_db_step_journal).
- The toll posts with the fire-key-rooted idempotency key
  f"{fire_key}:step:{step name}" (B4), so the toll ledger
  itself dedupes.

Reconciliation reads that evidence (journal rows + toll rows)
and classifies every planned step of a fire:

- done: a toll row carries the step's idempotency key (tolled
  implies executed: the toll posts only after the handler
  returns), or the journal says executed and the step has no
  toll event to post.
- executed_untolled: the journal says executed, the step has a
  toll event, and no toll row exists. With apply=True the
  missing toll is posted with the SAME idempotency key, so the
  ledger dedupes it: reconciling twice posts once, and the
  step itself is never re-run (its effect already happened).
- not_done: no journal row, no toll row. The step never ran;
  a retry may run it.
- unknown: the journal says started but never executed, and
  the step is not idempotent. The worker died somewhere inside
  the handler (the kill-between-send-and-journal case): the
  external effect may or may not have happened. Unknown is
  SURFACED in the report, never auto-retried, never auto-tolled.
  A started-but-unexecuted IDEMPOTENT step classifies not_done:
  its own idempotency contract makes a retry safe.

Reconciliation never re-runs a step handler. It posts tolls
and reports; resumption is the operator's call (recovery.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from ..persistence.models import workspace_id_for
from . import service
from .execution import JobStep, build_execution_context
from .models import AgentJobError

log = logging.getLogger(__name__)

STEPS_TABLE = "agt_job_fire_steps"

__all__ = [
    "STEPS_TABLE",
    "default_toll_reader",
    "list_step_journal",
    "make_db_step_journal",
    "reconcile_fire",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- the durable step journal --------------------------------------


def make_db_step_journal(
    db: Any, org_id: str, agent_id: str, fire_id: str, fire_key: str
) -> Callable[[str, JobStep], None]:
    """A run_steps journal seam backed by agt_job_fire_steps.

    One row per (fire, step): 'started' inserts it, later phases
    update it. Journal writes fail loud: a step whose journal
    cannot be written must not run unjournaled, because the
    journal is the evidence reconciliation stands on.
    """
    ws = workspace_id_for(org_id, agent_id)

    def _rows(step_name: str) -> list[dict]:
        return (
            db.table(STEPS_TABLE)
            .select("*")
            .eq("org_id", org_id)
            .eq("agent_identity_id", agent_id)
            .eq("workspace_id", ws)
            .eq("fire_id", fire_id)
            .eq("step_name", step_name)
            .execute()
            .data
            or []
        )

    def _update(step_name: str, payload: dict) -> None:
        (
            db.table(STEPS_TABLE)
            .update({**payload, "updated_at": _now_iso()})
            .eq("org_id", org_id)
            .eq("agent_identity_id", agent_id)
            .eq("workspace_id", ws)
            .eq("fire_id", fire_id)
            .eq("step_name", step_name)
            .execute()
        )

    def journal(phase: str, step: JobStep) -> None:
        if phase == "started":
            if _rows(step.name):
                return
            now = _now_iso()
            db.table(STEPS_TABLE).insert(
                {
                    "org_id": org_id,
                    "agent_identity_id": agent_id,
                    "workspace_id": ws,
                    "fire_id": fire_id,
                    "fire_key": fire_key,
                    "step_name": step.name,
                    "status": "started",
                    "toll_status": "pending" if step.toll_event else "none",
                    "created_at": now,
                    "updated_at": now,
                }
            ).execute()
        elif phase == "executed":
            _update(step.name, {"status": "executed"})
        elif phase == "tolled":
            _update(step.name, {"toll_status": "tolled"})
        elif phase == "toll_failed":
            _update(step.name, {"toll_status": "toll_failed"})
        else:  # pragma: no cover - internal seam, phases are ours
            raise AgentJobError(f"unknown journal phase {phase!r}")

    return journal


def list_step_journal(db: Any, org_id: str, agent_id: str, fire_id: str) -> list[dict]:
    """The fire's journal rows, in write order."""
    return (
        db.table(STEPS_TABLE)
        .select("*")
        .eq("org_id", org_id)
        .eq("agent_identity_id", agent_id)
        .eq("workspace_id", workspace_id_for(org_id, agent_id))
        .eq("fire_id", fire_id)
        .order("created_at")
        .execute()
        .data
        or []
    )


# -- toll evidence ---------------------------------------------------


def default_toll_reader(db: Any, org_id: str, fire_id: str) -> list[dict]:
    """The fire's toll rows from the production toll ledger:
    every toll a job fire posts carries source_type
    'agt_job_fire' and source_id = the fire id (B4 run_steps)."""
    rows = (
        db.table("toll_events").select("*").eq("source_id", fire_id).execute().data
        or []
    )
    return [
        r
        for r in rows
        if r.get("org_id") == org_id and r.get("source_type") == "agt_job_fire"
    ]


# -- reconciliation ----------------------------------------------------


def reconcile_fire(
    db: Any,
    org_id: str,
    agent_id: str,
    fire_id: str,
    *,
    executor_factory: Callable[..., list],
    toll_sink: Callable[..., dict] | None = None,
    toll_reader: Callable[..., list] | None = None,
    apply: bool = False,
    now: datetime | None = None,
) -> dict:
    """Classify every planned step of one fire against its
    journal and toll evidence. With apply=True, executed-but-
    untolled steps are tolled exactly once (same fire-key-rooted
    idempotency key the original run used). Step handlers are
    NEVER re-run here. Returns the classification report; any
    unknown step is named in 'unknown_steps' for the operator.
    """
    fire = service.get_fire(db, org_id, agent_id, fire_id)
    if fire is None:
        raise AgentJobError(f"fire {fire_id!r} not found in scope")
    job = service.get_job(db, org_id, agent_id, fire.get("job_id"))
    if job is None:
        raise AgentJobError(f"job for fire {fire_id!r} not found in scope")
    ctx = build_execution_context(
        org_id=org_id,
        agent_id=agent_id,
        job_id=job["id"],
        fire_id=fire_id,
        fire_key=fire["fire_key"],
        prompt=job.get("prompt", ""),
        skill_keys=job.get("skill_keys") or [],
        scheduled_for=service._parse_dt(fire.get("scheduled_for")),
        timezone_name=job.get("timezone", "UTC"),
    )
    planned = executor_factory(ctx)
    journal = {
        row["step_name"]: row
        for row in list_step_journal(db, org_id, agent_id, fire_id)
    }
    reader = toll_reader or default_toll_reader
    toll_rows = reader(db, org_id, fire_id)
    toll_keys = {r.get("idempotency_key") for r in toll_rows}

    steps_report: list[dict] = []
    unknown_steps: list[str] = []
    untolled: list[str] = []
    reconciled: list[str] = []
    for step in planned:
        key = f"{fire['fire_key']}:step:{step.name}"
        row = journal.get(step.name)
        entry: dict[str, Any] = {
            "name": step.name,
            "toll_event": step.toll_event,
            "idempotency_key": key,
        }
        if key in toll_keys:
            entry["classification"] = "done"
        elif row is not None and row.get("status") == "executed":
            if not step.toll_event:
                entry["classification"] = "done"
            elif apply and toll_sink is not None:
                try:
                    toll_sink(
                        db,
                        org_id,
                        step.toll_event,
                        idempotency_key=key,
                        source_type="agt_job_fire",
                        source_id=fire_id,
                    )
                except Exception as exc:
                    entry["classification"] = "executed_untolled"
                    entry["error"] = str(exc)
                    untolled.append(step.name)
                else:
                    entry["classification"] = "reconciled"
                    reconciled.append(step.name)
                    # Bring the journal's toll status current so
                    # the next reconciliation reads done-evidence.
                    try:
                        make_db_step_journal(
                            db, org_id, agent_id, fire_id, fire["fire_key"]
                        )("tolled", step)
                    except Exception as exc:  # evidence lag, not a failure
                        log.warning(
                            "agent_jobs: journal toll update failed for %s: %s",
                            key,
                            exc,
                        )
            else:
                entry["classification"] = "executed_untolled"
                untolled.append(step.name)
        elif row is not None and row.get("status") == "started":
            if step.idempotent:
                entry["classification"] = "not_done"
            else:
                entry["classification"] = "unknown"
                unknown_steps.append(step.name)
        else:
            entry["classification"] = "not_done"
        steps_report.append(entry)

    report = {
        "fire_id": fire_id,
        "fire_key": fire["fire_key"],
        "job_id": job["id"],
        "steps": steps_report,
        "unknown_steps": unknown_steps,
        "executed_untolled": untolled,
        "reconciled": reconciled,
    }
    if reconciled:
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        try:
            service.update_fire(db, org_id, agent_id, fire_id, reconciled_at=stamp)
        except Exception as exc:  # the tolls landed; the stamp is a note
            log.warning(
                "agent_jobs: reconciled_at stamp failed for fire %s: %s",
                fire_id,
                exc,
            )
    return report
