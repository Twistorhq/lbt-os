"""TW-625 Twistor Flows — synchronous execution engine.

One entry point per step; the run ledger (flw_runs / flw_run_steps) is the
receipt. Delay steps suspend; the tick endpoint resumes them (scheduler.py).

TW-208: a toll-ledger failure never fails the run. PII doctrine (TW-552):
step outputs are recorded, but raw recipient addresses are hashed.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import re
import urllib.parse
import urllib.request

from supabase import Client

from ..notify import service as notify_service
from . import budgets, models

log = logging.getLogger(__name__)

STEP_TOLL_CENTS = 1  # $0.01 per executed step (TOLL_RATES: flows.step_executed)


class ConditionError(ValueError):
    """A branch condition that cannot be evaluated. Fails loud."""


# ---------------------------------------------------------------------------
# context helpers: {{dotted.path}} rendering + lookup
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


def _lookup(ctx: dict, path: str):
    cur: object = ctx
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ConditionError(f"unknown context path {{{{{path}}}}}")
        cur = cur[part]
    return cur


def render(ctx: dict, template: str) -> str:
    """Render {{dotted.path}} placeholders. Missing paths fail loud."""

    def _replace(m: re.Match) -> str:
        return str(_lookup(ctx, m.group(1)))

    return _VAR_RE.sub(_replace, template)


_COND_RE = re.compile(
    r"^\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}\s*(==|!=|>=|<=|>|<|in)\s*(.+?)\s*$"
)


def _coerce(raw: str):
    low = raw.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("none", "null"):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw.strip("\"'")


def eval_condition(ctx: dict, condition: str) -> bool:
    """Evaluate '{{path}} OP value'. OP: == != > < >= <= in. Fails loud."""
    m = _COND_RE.match(condition or "")
    if not m:
        raise ConditionError(f"cannot parse condition: {condition!r}")
    path, op, raw_value = m.group(1), m.group(2), m.group(3)
    left = _lookup(ctx, path)
    right = _coerce(raw_value)
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "in":
        return left in right if isinstance(right, (list, str)) else False
    try:
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
    except TypeError as exc:
        raise ConditionError(f"incomparable: {condition!r}") from exc
    raise ConditionError(f"unknown operator {op!r}")


def _hash_recipient(to: str) -> str:
    return "sha256:" + hashlib.sha256(to.strip().lower().encode()).hexdigest()[:12]


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _record_step(
    db: Client,
    org_id: str,
    run_id: str,
    step_id: str,
    status: str,
    output: dict | None = None,
    error: str | None = None,
) -> None:
    try:
        db.table("flw_run_steps").insert(
            {
                "org_id": org_id,
                "run_id": run_id,
                "step_id": step_id,
                "status": status,
                "output": output or {},
                "error": error,
                "finished_at": _utcnow(),
            }
        ).execute()
    except Exception as exc:
        log.warning("flows: step ledger write failed: %s", exc)


# ---------------------------------------------------------------------------
# step handlers
# ---------------------------------------------------------------------------


def _step_notify(db, org_id, step: models.Step, ctx: dict) -> dict:
    to = render(ctx, step.to or "")
    if not to.strip():
        raise ConditionError(f"notify step {step.id!r} has no recipient")
    variables = {
        k: render(ctx, v) if isinstance(v, str) else v
        for k, v in (step.variables or {}).items()
    }
    result = notify_service.send(
        db,
        org_id,
        channel=step.channel or "email",
        to=to,
        subject=render(ctx, step.subject) if step.subject else None,
        body=render(ctx, step.body) if step.body else "",
        variables=variables,
        template_name=step.template,
        workflow="flows",
    )
    return {"to_hash": _hash_recipient(to), "message_id": result.get("id")}


def _step_delay(step: models.Step) -> dict:
    seconds = models.parse_duration(step.duration or "0s")
    resume_at = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=seconds)
    ).isoformat()
    return {"resume_at": resume_at}


def _step_webhook(step: models.Step, ctx: dict) -> dict:
    url = render(ctx, step.url or "")
    if not url.startswith(("https://", "http://")):
        raise ConditionError(f"webhook url refused: {url!r}")
    body = (step.variables or {}).get("body", {})
    req = urllib.request.Request(
        url,
        data=str(body).encode(),
        method=(step.method or "POST").upper(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {
                "status_code": resp.status,
                "url_host": urllib.parse.urlparse(url).hostname,
            }
    except Exception as exc:
        raise ConditionError(f"webhook failed: {exc}") from exc


def _step_set(step: models.Step, ctx: dict) -> dict:
    value = step.value
    if isinstance(value, str):
        value = render(ctx, value)
    cur = ctx
    parts = (step.var or "").split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value
    return {"var": step.var}


def _step_loop(
    db,
    org_id,
    workflow_id: str | None,
    run_id,
    wf: models.WorkflowDefinition,
    step: models.Step,
    ctx: dict,
    ledger: list,
) -> dict:
    items = _lookup(ctx, step.over.strip("{} ") if step.over else "")
    if not isinstance(items, list):
        raise ConditionError(f"loop over {step.over!r} is not a list")
    cap = min(step.max_iterations, 1000)  # hard ceiling, never unbounded
    iterations = 0
    errors = 0
    item_var = step.as_ or "item"
    for item in items[:cap]:
        iterations += 1
        ctx[item_var] = item
        try:
            for sub in step.steps:
                status, _ = execute_step(
                    db, org_id, wf, run_id, sub, ctx, workflow_id=workflow_id
                )
                ledger.append(sub.id)
                if status == "halt":
                    raise ConditionError("sub-step halted")
                if status == "suspend":
                    raise ConditionError("delay inside loop is not supported in V1")
        except Exception:
            errors += 1
        # circuit breaker: over half the iterations erroring -> kill the loop
        if iterations >= 4 and errors * 2 > iterations:
            _record_step(
                db,
                org_id,
                run_id,
                step.id,
                "circuit_broken",
                {"iterations": iterations, "errors": errors},
            )
            if workflow_id:
                _pause_for_cap(db, org_id, workflow_id, "loop circuit breaker tripped")
            return {
                "iterations": iterations,
                "errors": errors,
                "circuit_broken": True,
                "pause_workflow": True,
            }
    return {"iterations": iterations, "errors": errors}


def execute_step(
    db: Client,
    org_id: str,
    wf: models.WorkflowDefinition,
    run_id: str,
    step: models.Step,
    ctx: dict,
    workflow_id: str | None = None,
) -> tuple[str, dict]:
    """Run one step. Returns (status, info).
    status: continue | suspend (delay) | halt (budget/circuit breaker)."""
    try:
        if step.type == "notify":
            info = _step_notify(db, org_id, step, ctx)
        elif step.type == "delay":
            return "suspend", _step_delay(step)
        elif step.type == "branch":
            branch_steps = (
                step.then if eval_condition(ctx, step.condition or "") else step.else_
            )
            info = {"took": "then" if branch_steps == step.then else "else"}
            for sub in branch_steps:
                status, _ = execute_step(
                    db, org_id, wf, run_id, sub, ctx, workflow_id=workflow_id
                )
                if status != "continue":
                    return status, {"branch": info["took"]}
        elif step.type == "webhook":
            info = _step_webhook(step, ctx)
        elif step.type == "set":
            info = _step_set(step, ctx)
        elif step.type == "loop":
            info = _step_loop(db, org_id, workflow_id, run_id, wf, step, ctx, [])
            if info.get("circuit_broken"):
                _record_step(db, org_id, run_id, step.id, "halted", info)
                return "halt", info
        else:
            raise ConditionError(f"unknown step type {step.type!r}")
    except Exception as exc:
        _record_step(db, org_id, run_id, step.id, "error", {}, error=str(exc)[:500])
        raise
    _record_step(db, org_id, run_id, step.id, "ok", info)
    budgets.toll_step(db, org_id, run_id, step.id)
    return "continue", info


# ---------------------------------------------------------------------------
# run driver
# ---------------------------------------------------------------------------


def preview_cost(wf: models.WorkflowDefinition) -> dict:
    """Walk the definition and price the run BEFORE it executes."""

    def _count(steps: list[models.Step]) -> int:
        n = 0
        for s in steps:
            n += 1
            # branch: price the expensive path; loop: price one iteration
            # plus the cap note. Honest upper bound, labeled as such.
            n += _count(s.then) + _count(s.else_)
            n += _count(s.steps)
        return n

    steps = _count(wf.steps)
    return {
        "steps_upper_bound": steps,
        "cost_cents_upper_bound": steps * STEP_TOLL_CENTS,
        "note": "upper bound: branches price both paths, loops price all "
        "sub-steps once; the budget cap is the real ceiling",
    }


def run_workflow(
    db: Client,
    org_id: str,
    workflow_row: dict,
    trigger_input: dict | None = None,
    run_id: str | None = None,
) -> dict:
    """Execute a workflow synchronously. Budget-checked before and during."""
    import uuid

    wf = models.validate_definition(workflow_row["definition"])
    workflow_id = workflow_row["id"]
    alert_to = workflow_row.get("alert_to")
    run_id = run_id or str(uuid.uuid4())
    ctx: dict = dict(trigger_input or {})

    verdict, binfo = budgets.check_and_consume(
        db,
        org_id,
        workflow_id,
        wf.name,
        wf.budget,
        cost_cents=0,
        steps=0,
        count_run=True,
        alert_to=alert_to,
    )
    if verdict == "blocked":
        _save_run(
            db,
            org_id,
            run_id,
            workflow_id,
            "blocked",
            0,
            binfo.get("reason", "budget 100%"),
        )
        return {"run_id": run_id, "status": "blocked", "reason": binfo.get("reason")}

    _save_run(db, org_id, run_id, workflow_id, "running", 0)
    executed = 0
    spent = 0
    for step in wf.steps:
        if executed >= wf.budget.max_steps_per_run:
            budgets.check_and_consume(
                db,
                org_id,
                workflow_id,
                wf.name,
                wf.budget,
                cost_cents=0,
                steps=0,
                alert_to=alert_to,
            )
            _pause_for_cap(db, org_id, workflow_id, "max_steps_per_run hit")
            _save_run(
                db,
                org_id,
                run_id,
                workflow_id,
                "halted",
                executed,
                "per-run step cap hit; workflow auto-paused",
            )
            return {
                "run_id": run_id,
                "status": "halted",
                "steps_executed": executed,
                "budget_paused": True,
                "reason": "max_steps_per_run",
            }
        try:
            status, info = execute_step(
                db, org_id, wf, run_id, step, ctx, workflow_id=workflow_id
            )
        except Exception as exc:
            _save_run(
                db, org_id, run_id, workflow_id, "errored", executed, str(exc)[:500]
            )
            return {
                "run_id": run_id,
                "status": "errored",
                "steps_executed": executed,
                "error": str(exc)[:500],
            }
        executed += 1
        spent += STEP_TOLL_CENTS
        if status == "suspend":
            _save_run(
                db,
                org_id,
                run_id,
                workflow_id,
                "suspended",
                executed,
                None,
                resume_at=info.get("resume_at"),
                resume_step=step.id,
            )
            return {
                "run_id": run_id,
                "status": "suspended",
                "steps_executed": executed,
                "resume_at": info.get("resume_at"),
            }
        if status == "halt":
            _save_run(
                db, org_id, run_id, workflow_id, "halted", executed, "circuit breaker"
            )
            return {
                "run_id": run_id,
                "status": "halted",
                "steps_executed": executed,
                "budget_paused": True,
            }

    budgets.check_and_consume(
        db,
        org_id,
        workflow_id,
        wf.name,
        wf.budget,
        cost_cents=spent,
        steps=executed,
        alert_to=alert_to,
    )
    _save_run(db, org_id, run_id, workflow_id, "completed", executed)
    return {
        "run_id": run_id,
        "status": "completed",
        "steps_executed": executed,
        "cost_cents": spent,
    }


def _pause_for_cap(db: Client, org_id: str, workflow_id: str, reason: str) -> None:
    try:
        db.table("flw_workflows").update(
            {"status": "paused", "paused_reason": reason}
        ).eq("id", workflow_id).eq("org_id", org_id).execute()
    except Exception as exc:
        log.warning("flows: cap pause failed: %s", exc)


def _save_run(
    db: Client,
    org_id: str,
    run_id: str,
    workflow_id: str,
    status: str,
    steps_executed: int,
    error: str | None = None,
    resume_at: str | None = None,
    resume_step: str | None = None,
) -> None:
    try:
        db.table("flw_runs").upsert(
            {
                "id": run_id,
                "org_id": org_id,
                "workflow_id": workflow_id,
                "status": status,
                "steps_executed": steps_executed,
                "error": error,
                "resume_at": resume_at,
                "resume_step_id": resume_step,
                "finished_at": _utcnow()
                if status not in ("running", "suspended")
                else None,
            },
            on_conflict="id",
        ).execute()
    except Exception as exc:
        log.warning("flows: run ledger write failed: %s", exc)
