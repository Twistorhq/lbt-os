"""TW-625 Twistor Flows — synchronous execution engine.

One entry point per step; the run ledger (flw_runs / flw_run_steps) is the
receipt. Delay steps suspend; the tick endpoint resumes them (scheduler.py).

TW-208: a toll-ledger failure never fails the run. PII doctrine (TW-552):
step outputs are recorded, but raw recipient addresses are hashed.
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import logging
import re
import socket
import urllib.parse
import urllib.request
import uuid

from supabase import Client

from ..notify import service as notify_service
from . import budgets, models

log = logging.getLogger(__name__)

STEP_TOLL_CENTS = 1  # $0.01 per executed step (TOLL_RATES: flows.step_executed)


class ConditionError(ValueError):
    """A branch condition that cannot be evaluated. Fails loud."""


class _SuspendInLoop(ConditionError):
    """A delay step nested in a loop tried to suspend — definition-time
    validation rejects this, so reaching here is a backstop, not a path."""


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
    host = _safe_webhook_host(url)  # raises ConditionError; never logs URL
    raw_body = (step.variables or {}).get("body", {})

    def _render_deep(value):
        if isinstance(value, str):
            return render(ctx, value)
        if isinstance(value, dict):
            return {k: _render_deep(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_render_deep(v) for v in value]
        return value

    body = _render_deep(raw_body)
    headers = {
        k: render(ctx, v) if isinstance(v, str) else str(v)
        for k, v in (step.headers or {}).items()
    }
    headers.setdefault("Content-Type", "application/json")

    # Block redirects: a 302 to an internal host is the classic SSRF hop.
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ConditionError(
                f"webhook refused redirect to {urllib.parse.urlparse(newurl).hostname}"
            )

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method=(step.method or "POST").upper(),
        headers=headers,
    )
    try:
        with opener.open(req, timeout=15) as resp:
            return {"status_code": resp.status, "url_host": host}
    except ConditionError:
        raise
    except Exception as exc:
        # Host only in the error — full URLs often carry API keys.
        raise ConditionError(f"webhook to {host} failed: {type(exc).__name__}") from exc


def _safe_webhook_host(url: str) -> str:
    """SSRF guard: https only, hostname must not resolve to a private IP.

    Returns the hostname. Raises ConditionError on anything suspicious.
    """
    try:
        parts = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ConditionError(f"webhook url unparsable: {exc}") from exc
    if parts.scheme != "https":
        raise ConditionError(
            f"webhook refused: scheme must be https, got {parts.scheme!r}"
        )
    host = parts.hostname or ""
    if not host:
        raise ConditionError("webhook refused: no hostname")
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConditionError(f"webhook host unresolvable: {host}") from exc
    for info in infos:
        ip = info[4][0]
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
        ):
            raise ConditionError(f"webhook refused: {host} resolves to non-public IP")
    return host


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


class _RunState:
    """Shared per-run execution state: the step counter that enforces the
    per-run cap EVERYWHERE, including inside loops (the TW-630 wedge)."""

    def __init__(self, cap: int, workflow_id: str | None):
        self.cap = cap
        self.workflow_id = workflow_id
        self.count = 0


def _step_loop(
    db,
    org_id,
    workflow_id: str | None,
    run_id,
    wf: models.WorkflowDefinition,
    step: models.Step,
    ctx: dict,
    state: _RunState,
    toll_suffix: str,
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
                    db,
                    org_id,
                    wf,
                    run_id,
                    sub,
                    ctx,
                    workflow_id=workflow_id,
                    _state=state,
                    _toll_suffix=f"{toll_suffix}:{iterations}",
                )
                if status == "halt":
                    # cap or breaker tripped inside — propagate, don't swallow
                    return {
                        "iterations": iterations,
                        "errors": errors,
                        "halted": True,
                    }
                if status == "suspend":
                    raise _SuspendInLoop("delay inside loop is not supported in V1")
        except _SuspendInLoop:
            raise
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
    _state: _RunState | None = None,
    _toll_suffix: str = "",
) -> tuple[str, dict]:
    """Run one step. Returns (status, info).
    status: continue | suspend (delay) | halt (budget/circuit breaker).

    The shared _state counter enforces max_steps_per_run across the whole
    run — top-level steps, branch arms, and loop iterations alike. There
    is no path that executes a step without counting it.
    """
    if _state is None:
        _state = _RunState(wf.budget.max_steps_per_run, workflow_id)
    if _state.count >= _state.cap:
        _pause_for_cap(db, org_id, workflow_id or "", "max_steps_per_run hit (in step)")
        _record_step(db, org_id, run_id, step.id, "halted", {"reason": "step cap"})
        return "halt", {"reason": "max_steps_per_run", "budget_paused": True}
    _state.count += 1
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
                    db,
                    org_id,
                    wf,
                    run_id,
                    sub,
                    ctx,
                    workflow_id=workflow_id,
                    _state=_state,
                    _toll_suffix=_toll_suffix,
                )
                if status != "continue":
                    return status, {"branch": info["took"]}
        elif step.type == "webhook":
            info = _step_webhook(step, ctx)
        elif step.type == "set":
            info = _step_set(step, ctx)
        elif step.type == "loop":
            info = _step_loop(
                db,
                org_id,
                workflow_id,
                run_id,
                wf,
                step,
                ctx,
                _state,
                _toll_suffix,
            )
            if info.get("circuit_broken"):
                _record_step(db, org_id, run_id, step.id, "halted", info)
                return "halt", info
            if info.get("halted"):
                return "halt", {"reason": "max_steps_per_run", "budget_paused": True}
        else:
            raise ConditionError(f"unknown step type {step.type!r}")
    except Exception as exc:
        _record_step(db, org_id, run_id, step.id, "error", {}, error=str(exc)[:500])
        raise
    _record_step(db, org_id, run_id, step.id, "ok", info)
    budgets.toll_step(db, org_id, run_id, step.id, key_suffix=_toll_suffix)
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
            # branch: price the expensive path (both arms — honest ceiling).
            n += _count(s.then) + _count(s.else_)
            # loop: price sub-steps x the iteration cap (honest ceiling).
            if s.type == "loop":
                n += _count(s.steps) * min(s.max_iterations, 1000)
        return n

    steps = _count(wf.steps)
    return {
        "steps_upper_bound": steps,
        "cost_cents_upper_bound": steps * STEP_TOLL_CENTS,
        "note": "upper bound: branches price both arms, loops price "
        "sub-steps x the iteration cap; the task budget is the real ceiling",
    }


def run_workflow(
    db: Client,
    org_id: str,
    workflow_row: dict,
    trigger_input: dict | None = None,
    run_id: str | None = None,
) -> dict:
    """Execute a workflow synchronously. Budget-checked before and during."""
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
    state = _RunState(wf.budget.max_steps_per_run, workflow_id)
    # The per-run step cap is enforced inside execute_step via the shared
    # state counter — every nested step counts, no bypass.
    for step in wf.steps:
        try:
            status, info = execute_step(
                db,
                org_id,
                wf,
                run_id,
                step,
                ctx,
                workflow_id=workflow_id,
                _state=state,
            )
        except Exception as exc:
            _save_run(
                db,
                org_id,
                run_id,
                workflow_id,
                "errored",
                state.count,
                str(exc)[:500],
            )
            return {
                "run_id": run_id,
                "status": "errored",
                "steps_executed": state.count,
                "error": str(exc)[:500],
            }
        if status == "suspend":
            _save_run(
                db,
                org_id,
                run_id,
                workflow_id,
                "suspended",
                state.count,
                None,
                resume_at=info.get("resume_at"),
                resume_step=step.id,
                context=ctx,
            )
            return {
                "run_id": run_id,
                "status": "suspended",
                "steps_executed": state.count,
                "resume_at": info.get("resume_at"),
            }
        if status == "halt":
            _save_run(
                db,
                org_id,
                run_id,
                workflow_id,
                "halted",
                state.count,
                "circuit breaker",
            )
            return {
                "run_id": run_id,
                "status": "halted",
                "steps_executed": state.count,
                "budget_paused": True,
            }

    spent = state.count * STEP_TOLL_CENTS
    budgets.check_and_consume(
        db,
        org_id,
        workflow_id,
        wf.name,
        wf.budget,
        cost_cents=spent,
        steps=state.count,
        alert_to=alert_to,
    )
    _save_run(db, org_id, run_id, workflow_id, "completed", state.count)
    return {
        "run_id": run_id,
        "status": "completed",
        "steps_executed": state.count,
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
    context: dict | None = None,
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
                # Step context persists across suspension so a resumed run
                # sees the same variables the delay step saw.
                "context": context or {},
                "finished_at": _utcnow()
                if status not in ("running", "suspended")
                else None,
            },
            on_conflict="id",
        ).execute()
    except Exception as exc:
        log.warning("flows: run ledger write failed: %s", exc)
