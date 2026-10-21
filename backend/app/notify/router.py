"""TW-620 Twistor Notify — HTTP API.

Org scoping: the caller's active org comes from the Twistor Identity JWT
(claims["org"]). Every endpoint 403s without an active org.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..database import get_db
from ..limiter import limiter
from . import analytics, service, templates

router = APIRouter(prefix="/notify", tags=["notify"])
bearer = HTTPBearer(auto_error=False)


def _require_org(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    # Local import: identity is the auth authority; notify must not import
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


class SendIn(BaseModel):
    channel: str  # "email" | "sms" | "push"
    to: str
    subject: str | None = None
    body: str = ""
    variables: dict = {}
    template_name: str | None = None
    workflow: str | None = None


class PreviewIn(BaseModel):
    channel: str


@router.post("/preview-cost")
@limiter.limit("60/minute")
def preview_cost(
    request: Request,
    body: PreviewIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """Cost shown BEFORE anything goes out — the TW-630 wedge."""
    try:
        configs = service.load_provider_configs(db, org_id)
        return service.preview_cost(configs, body.channel)
    except service.routing.NoProviderAvailable as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/send")
@limiter.limit("120/minute")
def send_message(
    request: Request,
    body: SendIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    if body.channel not in ("email", "sms", "push"):
        raise HTTPException(status_code=422, detail="unknown channel")
    try:
        msg = service.send(
            db,
            org_id,
            channel=body.channel,
            to=body.to,
            subject=body.subject,
            body=body.body,
            variables=body.variables,
            template_name=body.template_name,
            workflow=body.workflow,
        )
    except templates.TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except service.routing.NoProviderAvailable as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # The debugger's safe handle is the hash; the raw recipient never
    # leaves the service boundary except to the owning org's admins.
    return {
        "id": msg.get("id"),
        "status": msg["status"],
        "provider_used": msg["provider_used"],
        "cost_preview_cents": msg["cost_preview_cents"],
        "cost_actual_cents": msg["cost_actual_cents"],
        "recipient_hash": msg["recipient_hash"],
    }


@router.get("/analytics/delivery")
@limiter.limit("60/minute")
def delivery_analytics(
    request: Request,
    workflow: str | None = None,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """Delivery rates per channel — the dashboard a PM can read."""
    return analytics.delivery_rates(db, org_id, workflow=workflow)


@router.get("/messages/{message_id}/debug")
@limiter.limit("60/minute")
def debug_message(
    request: Request,
    message_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """Silent-failure debugger: message + full attempt trail."""
    result = analytics.debug_message(db, org_id, message_id)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="message not found")
    return result


class TemplateIn(BaseModel):
    name: str
    channel: str
    subject_template: str | None = None
    body_template: str = ""
    variables: dict = {}


@router.post("/templates")
@limiter.limit("30/minute")
def create_template(
    request: Request,
    body: TemplateIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    if body.channel not in ("email", "sms", "push"):
        raise HTTPException(status_code=422, detail="unknown channel")
    row = {
        "org_id": org_id,
        "name": body.name,
        "channel": body.channel,
        "subject_template": body.subject_template,
        "body_template": body.body_template,
        "variables": body.variables,
        "is_active": True,
    }
    saved = service._insert(db, "ntf_templates", row)
    return {"id": saved.get("id"), "name": body.name}


@router.get("/templates")
@limiter.limit("60/minute")
def list_templates(
    request: Request,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    rows = db.table("ntf_templates").select("*").eq("org_id", org_id).execute()
    return {"templates": rows.data if rows and rows.data else []}
