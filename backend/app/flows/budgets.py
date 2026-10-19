"""TW-625 Twistor Flows — task budgets: 80% warn, 100% shutoff.

The TW-630 wedge: Zapier's runaway loops bill AFTER the damage. Flows
throttles BEFORE. Every workflow carries a budget; the throttle is
automatic, not a setting the user has to remember to turn on.
"""

from __future__ import annotations

import datetime
import logging

from supabase import Client

from ..notify import service as notify_service
from ..services import toll_service
from . import models

log = logging.getLogger(__name__)

WARN_RATIO = 0.8

# (verdict, info)
# verdict: "ok" | "warned" (80% crossed, warning sent) | "blocked" (100% hit)


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _get_counter(db: Client, org_id: str, workflow_id: str) -> dict:
    try:
        res = (
            db.table("flw_budget_counters")
            .select("*")
            .eq("org_id", org_id)
            .eq("workflow_id", workflow_id)
            .eq("day", _today())
            .execute()
        )
        rows = res.data or []
        if rows:
            return rows[0]
    except Exception as exc:
        log.warning("flows: budget counter read failed: %s", exc)
    return {"runs": 0, "steps": 0, "cost_cents": 0, "warned_80": False}


def _save_counter(db: Client, org_id: str, workflow_id: str, counter: dict) -> None:
    try:
        db.table("flw_budget_counters").upsert(
            {
                "org_id": org_id,
                "workflow_id": workflow_id,
                "day": _today(),
                "runs": counter["runs"],
                "steps": counter["steps"],
                "cost_cents": counter["cost_cents"],
                "warned_80": counter["warned_80"],
            },
            on_conflict="org_id,workflow_id,day",
        ).execute()
    except Exception as exc:
        # Fail-open per TW-208: a stuck counter must not brick automation,
        # but it must be loud.
        log.warning("flows: budget counter write failed: %s", exc)


def _pause_workflow(db: Client, org_id: str, workflow_id: str, reason: str) -> None:
    try:
        db.table("flw_workflows").update(
            {"status": "paused", "paused_reason": reason}
        ).eq("id", workflow_id).eq("org_id", org_id).execute()
    except Exception as exc:
        log.warning("flows: workflow pause failed: %s", exc)


def _warn_owner(
    db: Client,
    org_id: str,
    workflow_id: str,
    workflow_name: str,
    usage_pct: int,
    alert_to: str | None,
) -> bool:
    """Send the 80% warning. Returns True if a warning went out.

    The alert address is explicit on the workflow (alert_to) — V1 has no
    org-owner directory, so an unset address is a loud log, not a silent
    drop. The budget endpoint always surfaces the warning state.
    """
    try:
        db.table("flw_workflows").update(
            {"last_warning": f"budget at {usage_pct}% of daily cap"}
        ).eq("id", workflow_id).eq("org_id", org_id).execute()
    except Exception as exc:
        log.warning("flows: warning state write failed: %s", exc)
    if not alert_to:
        log.warning(
            "flows: budget 80%% crossed for %r with no alert_to set — "
            "owner was NOT emailed",
            workflow_name,
        )
        return False
    try:
        notify_service.send(
            db,
            org_id,
            channel="email",
            to=alert_to,
            template_name="flows_budget_warning",
            variables={"workflow": workflow_name, "usage_pct": str(usage_pct)},
            priority="system",
            workflow="flows-budget",
        )
        return True
    except Exception as exc:
        log.warning("flows: budget warning send failed: %s", exc)
        return False


def check_and_consume(
    db: Client,
    org_id: str,
    workflow_id: str,
    workflow_name: str,
    budget: models.Budget,
    cost_cents: int,
    steps: int,
    count_run: bool = False,
    alert_to: str | None = None,
) -> tuple[str, dict]:
    """Check the daily budget, consume `cost_cents`/`steps`, enforce 80/100.

    Returns (verdict, info). "blocked" means the caller must NOT run.
    """
    counter = _get_counter(db, org_id, workflow_id)
    used_runs = counter["runs"]
    runs = used_runs + (1 if count_run else 0)
    new_cost = counter["cost_cents"] + cost_cents
    new_steps = counter["steps"] + steps

    # Run-count shutoff: at trigger time the new run must fit (projected >
    # max blocks); mid-run, an already-full counter blocks further burn.
    if count_run:
        over_runs = runs > budget.max_runs_per_day
    else:
        over_runs = used_runs >= budget.max_runs_per_day
    over_cost = new_cost > budget.max_cost_per_day_cents
    if over_runs or over_cost:
        _pause_workflow(db, org_id, workflow_id, "task budget exhausted (100%)")
        try:
            db.table("flw_workflows").update(
                {"last_warning": "budget hit 100% — workflow auto-paused"}
            ).eq("id", workflow_id).eq("org_id", org_id).execute()
        except Exception as exc:
            log.warning("flows: warning state write failed: %s", exc)
        return "blocked", {"workflow_paused": True, "reason": "budget 100%"}

    # 80% on any dimension -> warn once per day.
    ratios = [
        runs / budget.max_runs_per_day if budget.max_runs_per_day else 0,
        new_cost / budget.max_cost_per_day_cents
        if budget.max_cost_per_day_cents
        else 0,
    ]
    peak = max(ratios)
    warned = False
    emailed = False
    if peak >= WARN_RATIO and not counter.get("warned_80"):
        emailed = _warn_owner(
            db, org_id, workflow_id, workflow_name, int(peak * 100), alert_to
        )
        counter["warned_80"] = True
        warned = True

    counter.update({"runs": runs, "steps": new_steps, "cost_cents": new_cost})
    _save_counter(db, org_id, workflow_id, counter)
    return ("warned" if warned else "ok"), {
        "warned": warned,
        "emailed": emailed,
        "usage_pct": int(peak * 100),
    }


def toll_step(
    db: Client, org_id: str, run_id: str, step_id: str, key_suffix: str = ""
) -> None:
    """Emit the $0.01 step toll. TW-208: ledger failure never fails the run.

    key_suffix distinguishes loop iterations (":3") so the idempotency
    key stays unique per executed step — iterations 2+ are tolled, not
    deduped away.
    """
    try:
        toll_service.record_toll_event(
            db,
            org_id,
            "flows.step_executed",
            quantity=1,
            idempotency_key=f"flows:{run_id}:{step_id}{key_suffix}",
            source_type="flw_run_steps",
            source_id=f"{run_id}:{step_id}{key_suffix}",
        )
    except Exception as exc:
        log.warning("flows: step toll failed (run unaffected): %s", exc)


def budget_reset_for_today(
    db: Client, org_id: str, workflow_id: str, budget: models.Budget
) -> bool:
    """True when today's counters are back under every cap.

    Used to auto-resume workflows that were paused on a 100% budget hit
    once the UTC day rolls over — a pause must not outlive the budget
    period that caused it.
    """
    counter = _get_counter(db, org_id, workflow_id)
    return (
        counter["runs"] < budget.max_runs_per_day
        and counter["cost_cents"] < budget.max_cost_per_day_cents
    )
