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

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..config import settings
from ..database import get_db
from . import service, ticker
from .execution import RuntimeTaskExecutor
from .models import AgentJobError, JobEdit, JobSpec

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

        return [JobStep(name="run_task", handler=lambda _ctx: adapter(_ctx))]

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
            db, org_id, agent_id, job_id, executor_factory=factory
        )
    )


@router.post("/jobs/{job_id}/cancel-run")
def cancel_run(
    job_id: str,
    body: CancelIn,
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return _guard(
        lambda: service.cancel_run(db, org_id, agent_id, job_id, body.fire_id)
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
    return ticker.tick(
        db,
        now=datetime.now(timezone.utc),
        executor_factory=factory,
    )
