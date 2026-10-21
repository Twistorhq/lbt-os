"""TW-625 Twistor Flows — CRUD + trigger orchestration.

Thin over the executor: validation, budget pre-checks, and the run ledger
live here; step semantics live in executor.py.
"""

from __future__ import annotations

import logging

from supabase import Client

from . import executor, models

log = logging.getLogger(__name__)


def create_workflow(
    db: Client, org_id: str, name: str, definition: dict, alert_to: str | None = None
) -> dict:
    wf = models.validate_definition({**definition, "name": name})
    row = {
        "org_id": org_id,
        "name": name,
        "definition": wf.model_dump(by_alias=True),
        "status": "active",
        "alert_to": alert_to,
    }
    res = db.table("flw_workflows").insert(row).execute()
    return (res.data or [row])[0]


def get_workflow(db: Client, org_id: str, workflow_id: str) -> dict | None:
    res = (
        db.table("flw_workflows")
        .select("*")
        .eq("id", workflow_id)
        .eq("org_id", org_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def list_workflows(db: Client, org_id: str) -> list[dict]:
    res = (
        db.table("flw_workflows")
        .select("id,name,status,alert_to,created_at")
        .eq("org_id", org_id)
        .execute()
    )
    return res.data or []


def update_workflow(
    db: Client, org_id: str, workflow_id: str, patch: dict
) -> dict | None:
    updates: dict = {}
    if "name" in patch:
        updates["name"] = patch["name"]
    if "definition" in patch:
        current = get_workflow(db, org_id, workflow_id) or {}
        name = patch.get("name", current.get("name", "flow"))
        wf = models.validate_definition({**patch["definition"], "name": name})
        updates["definition"] = wf.model_dump(by_alias=True)
    if "alert_to" in patch:
        updates["alert_to"] = patch["alert_to"]
    if not updates:
        return get_workflow(db, org_id, workflow_id)
    res = (
        db.table("flw_workflows")
        .update(updates)
        .eq("id", workflow_id)
        .eq("org_id", org_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def set_status(
    db: Client, org_id: str, workflow_id: str, status: str, reason: str | None = None
) -> dict | None:
    if status not in ("active", "paused"):
        raise models.DefinitionError(f"bad status {status!r}")
    updates = {"status": status}
    if reason:
        updates["paused_reason"] = reason
    res = (
        db.table("flw_workflows")
        .update(updates)
        .eq("id", workflow_id)
        .eq("org_id", org_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def maybe_auto_resume(db: Client, org_id: str, row: dict) -> dict | None:
    """Resume a budget-paused workflow when the UTC day has rolled over.

    A 100%-budget pause must not outlive the budget period that caused
    it. Returns the refreshed (active) row, the already-active row
    unchanged, or None when no resume applies. Used by both the manual
    trigger path and the scheduler tick.
    """
    if row.get("status") == "active":
        return row
    if not (row.get("paused_reason") or "").startswith("task budget"):
        return None
    from . import budgets

    wf = models.validate_definition(row["definition"])
    if budgets.budget_reset_for_today(db, org_id, row["id"], wf.budget):
        set_status(db, org_id, row["id"], "active")
        return get_workflow(db, org_id, row["id"])
    return None


def trigger(
    db: Client, org_id: str, workflow_id: str, trigger_input: dict | None = None
) -> dict:
    row = get_workflow(db, org_id, workflow_id)
    if not row:
        raise KeyError(f"workflow {workflow_id} not found")
    paused_reason = (row.get("paused_reason") or "").strip()
    row = maybe_auto_resume(db, org_id, row)
    if not row:
        detail = f" ({paused_reason})" if paused_reason else ""
        raise PermissionError(f"workflow {workflow_id} is paused{detail}")
    return executor.run_workflow(db, org_id, row, trigger_input or {})


def preview(db: Client, org_id: str, workflow_id: str) -> dict:
    row = get_workflow(db, org_id, workflow_id)
    if not row:
        raise KeyError(f"workflow {workflow_id} not found")
    wf = models.validate_definition(row["definition"])
    cost = executor.preview_cost(wf)
    return {
        "workflow_id": workflow_id,
        "name": wf.name,
        **cost,
        "budget": wf.budget.model_dump(),
    }


def find_by_webhook_key(db: Client, key: str) -> dict | None:
    # Webhook keys live inside the definition JSON; scan active workflows.
    # V1 trades an index for simplicity — the key space is small per org.
    res = db.table("flw_workflows").select("*").eq("status", "active").execute()
    for row in res.data or []:
        trig = (row.get("definition") or {}).get("trigger") or {}
        if trig.get("type") == "webhook" and trig.get("key") == key:
            return row
    return None


def list_runs(db: Client, org_id: str, workflow_id: str, limit: int = 50) -> list[dict]:
    res = (
        db.table("flw_runs")
        .select("id,status,steps_executed,error,created_at,finished_at")
        .eq("org_id", org_id)
        .eq("workflow_id", workflow_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def get_run(db: Client, org_id: str, run_id: str) -> dict | None:
    res = (
        db.table("flw_runs").select("*").eq("id", run_id).eq("org_id", org_id).execute()
    )
    rows = res.data or []
    if not rows:
        return None
    run = rows[0]
    steps = (
        db.table("flw_run_steps")
        .select("step_id,status,output,error,finished_at")
        .eq("run_id", run_id)
        .execute()
        .data
        or []
    )
    run["steps"] = steps
    return run


def budget_status(db: Client, org_id: str, workflow_id: str) -> dict:
    from . import budgets

    row = get_workflow(db, org_id, workflow_id)
    if not row:
        raise KeyError(f"workflow {workflow_id} not found")
    wf = models.validate_definition(row["definition"])
    counter = budgets._get_counter(db, org_id, workflow_id)
    return {
        "workflow_id": workflow_id,
        "status": row.get("status"),
        "paused_reason": row.get("paused_reason"),
        "last_warning": row.get("last_warning"),
        "budget": wf.budget.model_dump(),
        "today": {
            "runs": counter["runs"],
            "steps": counter["steps"],
            "cost_cents": counter["cost_cents"],
            "warned_80": counter["warned_80"],
        },
    }
