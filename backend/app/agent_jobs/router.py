"""TW-952 (TW-948-B4): HTTP API for the persistent job service.

Beside flows, same contracts: the caller's active org comes from
the Twistor Identity JWT (claims["org"]) and every endpoint 403s
without one, except the tick endpoint, guarded by the
AGENT_JOBS_TICK_KEY setting and fail-closed when unset (the
FLOWS_TICK_KEY pattern). The agent is named per request: jobs
are scoped to (org, agent, workspace) like every B1 table.

Execution wiring: firing needs an agent_runtime, which is
deployment wiring (journal path, credential resolver) owned by
the deploy lane. The router reads a runtime factory from
app.state.agent_runtime_factory when the deploy sets one;
without it, fire and tick refuse with 503 rather than faking a
run. Management operations need no runtime and always work.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..config import settings
from ..database import get_db
from ..services import toll_service
from . import heartbeat, reconciliation, recovery, service, ticker
from .execution import RunControl, RuntimeTaskExecutor
from .models import AgentJobError, JobEdit, JobSpec
from .preflight import PreflightChecker, log_alerter

router = APIRouter(prefix="/agent-jobs", tags=["agent-jobs"])
bearer = HTTPBearer(auto_error=False)


def _require_org(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    from ..identity import service as idn_service
    from ..identity import tokens as idn_tokens

    if not creds:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        claims = idn_tokens.verify_access_token(
            idn_service.get_verification_keypairs(), creds.credentials
        )
    except idn_tokens.TokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    org_id = claims.get("org")
    if not org_id:
        raise HTTPException(status_code=403, detail="no active org on token")
    return org_id


def _guard(fn):
    try:
        return fn()
    except AgentJobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CancelIn(BaseModel):
    fire_id: str


class ReconcileIn(BaseModel):
    apply: bool = False


def _shared_control(request: Request) -> RunControl:
    """TW-953 (Rex F5): ONE RunControl per app, shared by the
    tick, trigger, and cancel endpoints. B4's wiring created no
    shared control at all, so an HTTP cancel fenced nothing in
    this process and an HTTP fire's tolls went nowhere. The
    control lives on app.state, created on first use."""
    control = getattr(request.app.state, "agent_jobs_run_control", None)
    if control is None:
        control = RunControl()
        request.app.state.agent_jobs_run_control = control
    return control


def _alerter(request: Request):
    """The blocked/interruption alerter: deployment-wired via
    app.state.agent_jobs_alerter (a Notify-backed alerter from
    preflight.make_notify_alerter), falling back to the loud
    log alerter so a block is never silent."""
    return getattr(request.app.state, "agent_jobs_alerter", None) or log_alerter


def _health_alerter(request: Request):
    """The dead-scheduler alerter (Rosa MF3): adapts the
    job-shaped alerter seam to heartbeat.check_scheduler_health's
    health-dict shape. A deployment may wire a dedicated
    app.state.agent_jobs_health_alerter; otherwise the shared
    alerter fires with a synthetic scheduler job row, falling
    back to the loud log alerter, so a dead scheduler is never
    silent either."""
    dedicated = getattr(request.app.state, "agent_jobs_health_alerter", None)
    if dedicated is not None:
        return dedicated
    base = _alerter(request)

    def health_alerter(health: dict) -> str:
        reason = (
            "agent-jobs scheduler heartbeat is stale: status "
            f"{health.get('status')!r}, last tick {health.get('last_tick_at')!r}"
        )
        return base(
            None,
            "",
            {"id": "agent-jobs-ticker", "name": "agent-jobs scheduler"},
            reason,
        )

    return health_alerter


def _preflight_checker(request: Request) -> PreflightChecker:
    """TW-953: the tick/trigger preflight. Delivery and ledger
    checks use their db-backed defaults (Notify providers, the
    TW-768 ledger); credential resolution and the skill registry
    are deployment wiring on app.state, failing closed when a
    job needs them and they are absent."""
    state = request.app.state
    return PreflightChecker(
        credential_resolver=getattr(state, "agent_credential_resolver", None),
        skill_checker=getattr(state, "agent_skill_checker", None),
        phone_hook=getattr(state, "agent_jobs_phone_hook", None),
    )


def _executor_factory(request: Request, db: Client, org_id: str):
    factory = getattr(request.app.state, "agent_runtime_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=503,
            detail="agent runtime wiring not configured on this deployment",
        )
    adapter = RuntimeTaskExecutor(factory(db, org_id))

    def make_steps(ctx):
        from .execution import JobStep

        # TW-953 (Rex F5): the step carries its toll event, so an
        # HTTP-path fire tolls agent.task_completed (B3
        # vocabulary; on a base without B3 the toll sink fails
        # loud, never silently untolled) with the fire-key-rooted
        # idempotency key run_steps builds.
        # Fix round (Rex F5 / P9): the step is explicitly
        # NON-idempotent. Enqueueing a runtime task is an
        # external effect with no dedupe contract, so a worker
        # killed inside this handler leaves an outcome that is
        # genuinely unknown; with the JobStep default of
        # idempotent=True reconciliation classified that
        # not_done and the resume gate never tripped on the only
        # production step shape there is.
        return [
            JobStep(
                name="run_task",
                handler=lambda _ctx: adapter(_ctx),
                toll_event="agent.task_completed",
                idempotent=False,
            )
        ]

    return make_steps


@router.post("/jobs")
def create_job(
    body: JobSpec,
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return _guard(
        lambda: service.create_job(db, org_id, agent_id, body, created_by=org_id)
    )


@router.get("/jobs")
def list_jobs(
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return service.list_jobs(db, org_id, agent_id)


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    job = service.get_job(db, org_id, agent_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.patch("/jobs/{job_id}")
def edit_job(
    job_id: str,
    body: JobEdit,
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return _guard(lambda: service.edit_job(db, org_id, agent_id, job_id, body))


@router.post("/jobs/{job_id}/pause")
def pause_job(
    job_id: str,
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return _guard(lambda: service.pause_job(db, org_id, agent_id, job_id))


@router.post("/jobs/{job_id}/resume")
def resume_job(
    job_id: str,
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return _guard(lambda: service.resume_job(db, org_id, agent_id, job_id))


@router.post("/jobs/{job_id}/remove")
def remove_job(
    job_id: str,
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return _guard(lambda: service.remove_job(db, org_id, agent_id, job_id))


@router.post("/jobs/{job_id}/trigger")
def trigger_job(
    job_id: str,
    agent_id: str,
    request: Request,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    factory = _executor_factory(request, db, org_id)
    return _guard(
        lambda: service.trigger_now(
            db,
            org_id,
            agent_id,
            job_id,
            executor_factory=factory,
            toll_sink=toll_service.record_toll_event,
            control=_shared_control(request),
            preflight=_preflight_checker(request),
            alerter=_alerter(request),
        )
    )


@router.post("/jobs/{job_id}/cancel-run")
def cancel_run(
    job_id: str,
    body: CancelIn,
    agent_id: str,
    request: Request,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    # TW-953 (Rex F5): the cancel fences through the SAME
    # RunControl the tick and trigger paths run under, so an
    # HTTP cancel actually stops an HTTP-started in-flight run.
    return _guard(
        lambda: service.cancel_run(
            db,
            org_id,
            agent_id,
            job_id,
            body.fire_id,
            control=_shared_control(request),
        )
    )


@router.post("/jobs/{job_id}/fires/{fire_id}/reconcile")
def reconcile_fire(
    job_id: str,
    fire_id: str,
    body: ReconcileIn,
    agent_id: str,
    request: Request,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """TW-953: classify one fire's planned steps against its
    journal and toll evidence; apply=True posts executed-but-
    untolled tolls exactly once. Unknown steps are reported,
    never retried."""
    factory = _executor_factory(request, db, org_id)
    return _guard(
        lambda: reconciliation.reconcile_fire(
            db,
            org_id,
            agent_id,
            fire_id,
            executor_factory=factory,
            toll_sink=toll_service.record_toll_event if body.apply else None,
            apply=body.apply,
        )
    )


@router.post("/jobs/{job_id}/resume-after-review")
def resume_after_review(
    job_id: str,
    agent_id: str,
    request: Request,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """TW-953: the operator resume for a job paused for review.
    Reconciliation runs first; an unknown step refuses the
    resume and names itself."""
    factory = _executor_factory(request, db, org_id)
    return _guard(
        lambda: recovery.resume_after_review(
            db,
            org_id,
            agent_id,
            job_id,
            executor_factory=factory,
            toll_sink=toll_service.record_toll_event,
        )
    )


@router.post("/tick")
def tick_jobs(
    request: Request,
    x_tick_key: str | None = Header(default=None),
    db: Client = Depends(get_db),
):
    configured = settings.agent_jobs_tick_key
    if not configured or x_tick_key != configured:
        raise HTTPException(status_code=403, detail="tick key required")
    factory = _executor_factory(request, db, "")
    # TW-953 (Rex F5/F7): the HTTP tick runs the full B5 posture:
    # the production toll sink, the shared RunControl, preflight
    # with the one-alert alerter, the stranded-fire reaper, and
    # the durable heartbeat the tick leaves behind either way.
    return ticker.tick(
        db,
        now=datetime.now(timezone.utc),
        executor_factory=factory,
        toll_sink=toll_service.record_toll_event,
        control=_shared_control(request),
        preflight=_preflight_checker(request),
        alerter=_alerter(request),
        reap_stale_after=timedelta(minutes=30),
        # Rosa MF3: the tick path runs the scheduler health
        # check (dead-scheduler alert) and, inside ticker.tick,
        # the preflight definition scan, every tick.
        health_alerter=_health_alerter(request),
    )


@router.get("/tick-health")
def tick_health(
    request: Request,
    x_tick_key: str | None = Header(default=None),
    db: Client = Depends(get_db),
):
    """TW-953: the durable tick heartbeat, readable in-system so
    a dead scheduler is visible without calling the tick first.
    Same tick-key guard as the tick endpoint.

    Fix round (Rosa MF3): this endpoint runs the full health
    check, not just the read: a stale heartbeat fires the
    dead-scheduler alert here too (exactly once per episode),
    so the alert has a production caller even between ticks."""
    configured = settings.agent_jobs_tick_key
    if not configured or x_tick_key != configured:
        raise HTTPException(status_code=403, detail="tick key required")
    return heartbeat.check_scheduler_health(
        db,
        now=datetime.now(timezone.utc),
        health_alerter=_health_alerter(request),
    )
