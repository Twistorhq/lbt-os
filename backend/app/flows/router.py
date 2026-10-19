"""TW-625 Twistor Flows — HTTP API.

Org scoping: the caller's active org comes from the Twistor Identity JWT
(claims["org"]), same contract as Notify. Every endpoint 403s without an
active org — except the webhook trigger, where the trigger key IS the
secret (rate-limited), and the tick endpoint, guarded by FLOWS_TICK_KEY.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..config import settings
from ..database import get_db
from ..limiter import limiter
from . import executor, mcp, migration, models, scheduler, service

router = APIRouter(prefix="/flows", tags=["flows"])
bearer = HTTPBearer(auto_error=False)


def _require_org(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    # Local import: identity is the auth authority; flows must not import
    # it at module load (keeps the package importable in tests).
    from ..identity import service as idn_service
    from ..identity import tokens as idn_tokens

    if not creds:
        raise HTTPException(status_code=401, detail="missing bearer <redacted>")
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


class CreateIn(BaseModel):
    name: str
    definition: dict
    alert_to: str | None = None


class UpdateIn(BaseModel):
    name: str | None = None
    definition: dict | None = None
    alert_to: str | None = None


class TriggerIn(BaseModel):
    input: dict = {}


class McpIn(BaseModel):
    tool: str
    args: dict = {}


class ZapierIn(BaseModel):
    zap: dict


@router.post("")
@limiter.limit("60/minute")
def create_flow(
    request: Request,
    body: CreateIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    try:
        return service.create_workflow(
            db, org_id, body.name, body.definition, body.alert_to
        )
    except models.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("")
@limiter.limit("120/minute")
def list_flows(
    request: Request, org_id: str = Depends(_require_org), db: Client = Depends(get_db)
):
    return {"workflows": service.list_workflows(db, org_id)}


@router.get("/{workflow_id}")
@limiter.limit("120/minute")
def get_flow(
    request: Request,
    workflow_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    row = service.get_workflow(db, org_id, workflow_id)
    if not row:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


@router.patch("/{workflow_id}")
@limiter.limit("60/minute")
def update_flow(
    request: Request,
    workflow_id: str,
    body: UpdateIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    try:
        row = service.update_workflow(
            db,
            org_id,
            workflow_id,
            {
                k: v
                for k, v in {
                    "name": body.name,
                    "definition": body.definition,
                    "alert_to": body.alert_to,
                }.items()
                if v is not None
            },
        )
    except models.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not row:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


@router.post("/{workflow_id}/trigger")
@limiter.limit("30/minute")
def trigger_flow(
    request: Request,
    workflow_id: str,
    body: TriggerIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    try:
        return service.trigger(db, org_id, workflow_id, body.input)
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{workflow_id}/preview")
@limiter.limit("60/minute")
def preview_flow(
    request: Request,
    workflow_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    try:
        return service.preview(db, org_id, workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found")


@router.post("/{workflow_id}/pause")
@limiter.limit("30/minute")
def pause_flow(
    request: Request,
    workflow_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    row = service.set_status(db, org_id, workflow_id, "paused", "paused by owner")
    if not row:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


@router.post("/{workflow_id}/resume")
@limiter.limit("30/minute")
def resume_flow(
    request: Request,
    workflow_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    row = service.set_status(db, org_id, workflow_id, "active")
    if not row:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


@router.get("/{workflow_id}/runs")
@limiter.limit("120/minute")
def list_runs(
    request: Request,
    workflow_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return {"runs": service.list_runs(db, org_id, workflow_id)}


@router.get("/{workflow_id}/budget")
@limiter.limit("120/minute")
def budget(
    request: Request,
    workflow_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    try:
        return service.budget_status(db, org_id, workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found")


@router.get("/runs/{run_id}")
@limiter.limit("120/minute")
def get_run(
    request: Request,
    run_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    run = service.get_run(db, org_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.post("/webhook/{key}")
@limiter.limit("60/minute")
def webhook_trigger(
    request: Request, key: str, body: dict = {}, db: Client = Depends(get_db)
):
    # No JWT here: the trigger key is the secret. Rate-limited.
    row = service.find_by_webhook_key(db, key)
    if not row:
        raise HTTPException(status_code=404, detail="unknown trigger")
    return executor.run_workflow(db, row["org_id"], row, body or {})


@router.post("/tick")
@limiter.limit("10/minute")
def tick(
    request: Request,
    x_flows_tick_key: str | None = Header(default=None),
    db: Client = Depends(get_db),
):
    expected = getattr(settings, "flows_tick_key", None)
    if not expected or x_flows_tick_key != expected:
        # Fail-closed: no key configured, or wrong key. No schedule fires.
        raise HTTPException(status_code=403, detail="bad or missing tick key")
    return scheduler.tick(db)


@router.post("/mcp")
@limiter.limit("60/minute")
def mcp_call(
    request: Request,
    body: McpIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    try:
        return {
            "tool": body.tool,
            "result": mcp.call_tool(db, org_id, body.tool, body.args),
        }
    except models.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/import/zapier")
@limiter.limit("30/minute")
def import_zapier(
    request: Request,
    body: ZapierIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    try:
        definition = migration.from_zapier(body.zap)
        models.validate_definition({**definition, "name": "import"})
    except migration.UnsupportedStepError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except models.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "definition": definition,
        "note": "validate + name it, then POST /flows to create",
    }
