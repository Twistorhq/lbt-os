"""TW-767 Twistor Agent Identity — HTTP API.

Two surfaces:
- Registry (JWT): the caller's org comes from the Twistor Identity JWT
  (claims["org"]); every endpoint 403s without an active org. Sofia's
  Spec 6 surfaces (Agent Card, Permission Center) consume these; Spec 3's
  runtime reads the channel configs.
- Inbound webhook (Svix-signed, NO JWT): Resend delivers mail addressed
  to an agent here. The signature is the credential. Unconfigured secret
  refuses (503, fail-closed, the flows_tick_key pattern); a bad
  signature is a 401; an unknown recipient queues nothing and still
  returns 200 so the endpoint never leaks which addresses exist.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..config import settings
from ..database import get_db
from . import service
from .models import IdentityError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-identity", tags=["agent-identity"])
_bearer_scheme = HTTPBearer(auto_error=False)


def _verified_claims(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    # Local import: identity is the auth authority; importing it at
    # module load would keep this package unimportable in tests.
    from ..identity import service as idn_service
    from ..identity import tokens as idn_tokens

    if not creds:
        raise HTTPException(status_code=401, detail="missing credentials")
    try:
        claims = idn_tokens.verify_access_token(
            idn_service.get_verification_keypairs(), creds.credentials
        )
    except idn_tokens.TokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    return claims


def _require_org(claims: dict = Depends(_verified_claims)) -> str:
    org_id = claims.get("org")
    if not org_id:
        raise HTTPException(status_code=403, detail="no active org on token")
    return org_id


def _require_actor(claims: dict = Depends(_verified_claims)) -> str:
    return str(claims.get("sub") or claims.get("email") or "unknown")


class ProvisionIn(BaseModel):
    agent_name: str


class HireIn(BaseModel):
    agent_name: str
    role: str = "general"
    archetype_key: str | None = None


class RoleIn(BaseModel):
    role: str


class ArchetypeIn(BaseModel):
    archetype_key: str | None = None


class PersonaPatchIn(BaseModel):
    patch: dict[str, Any]


class CoverageIn(BaseModel):
    coverage: dict[str, Any]


class PhoneBindIn(BaseModel):
    phone_number: str


class StatusIn(BaseModel):
    status: str


def _handle(fn):
    try:
        return fn()
    except service.IdentityNotFound:
        raise HTTPException(
            status_code=404, detail="agent identity not provisioned"
        ) from None
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/me")
def get_my_identity(
    org_id: str = Depends(_require_org), db: Client = Depends(get_db)
) -> dict[str, Any]:
    """The org's primary (included) agent record, or the fail-closed
    default view."""
    return service.identity_view(db, org_id)


@router.get("/agents")
def list_agents(
    org_id: str = Depends(_require_org), db: Client = Depends(get_db)
) -> list[dict[str, Any]]:
    """The org's roster (TW-787): included agent first, then hires."""
    return service.roster_view(db, org_id)


@router.get("/agents/{agent_id}")
def get_agent(
    agent_id: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    return _handle(lambda: service.identity_view(db, org_id, agent_id))


@router.post("/agents")
def hire_agent(
    body: HireIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    """Hire an additional agent (TW-787). Posts the one-time
    agent.hire toll as part of the hire."""
    identity = _handle(
        lambda: service.hire_agent(
            db,
            org_id,
            body.agent_name,
            actor,
            role=body.role,
            archetype_key=body.archetype_key,
        )
    )
    return identity.model_dump(mode="json")


@router.post("/agents/{agent_id}/role")
def assign_role(
    agent_id: str,
    body: RoleIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    identity = _handle(
        lambda: service.assign_role(db, org_id, agent_id, body.role, actor)
    )
    return identity.model_dump(mode="json")


@router.post("/agents/{agent_id}/archetype")
def assign_archetype(
    agent_id: str,
    body: ArchetypeIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    identity = _handle(
        lambda: service.assign_archetype(
            db, org_id, agent_id, body.archetype_key, actor
        )
    )
    return identity.model_dump(mode="json")


@router.get("/channels/{channel}")
def get_channel_config(
    channel: str,
    agent_id: str | None = None,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    """One channel's read of one agent's identity (phone | email |
    sms). The identity block is byte-identical across channels, per
    agent, by construction."""
    return _handle(lambda: service.channel_config(db, org_id, channel, agent_id))


@router.post("")
def provision(
    body: ProvisionIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    identity = _handle(
        lambda: service.provision_identity(db, org_id, body.agent_name, actor)
    )
    return identity.model_dump(mode="json")


@router.patch("/persona")
def patch_persona(
    body: PersonaPatchIn,
    agent_id: str | None = None,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    identity = _handle(
        lambda: service.update_persona(db, org_id, body.patch, actor, agent_id)
    )
    return identity.model_dump(mode="json")


@router.patch("/coverage")
def patch_coverage(
    body: CoverageIn,
    agent_id: str | None = None,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    identity = _handle(
        lambda: service.update_coverage(db, org_id, body.coverage, actor, agent_id)
    )
    return identity.model_dump(mode="json")


@router.post("/phone")
def bind_phone(
    body: PhoneBindIn,
    agent_id: str | None = None,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    identity = _handle(
        lambda: service.bind_phone_number(
            db, org_id, body.phone_number, actor, agent_id
        )
    )
    return identity.model_dump(mode="json")


@router.post("/status")
def change_status(
    body: StatusIn,
    agent_id: str | None = None,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
) -> dict[str, Any]:
    identity = _handle(
        lambda: service.set_status(db, org_id, body.status, actor, agent_id)
    )
    return identity.model_dump(mode="json")


@router.get("/inbound")
def list_inbound(
    status: str | None = None,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
) -> list[dict[str, Any]]:
    """Queued inbound work for the org — metadata only. Raw sender,
    subject, and body are bronze and never leave the intake table."""
    return service.list_inbound(db, org_id, status=status)


@router.post("/inbound/email")
async def inbound_email_webhook(
    request: Request, db: Client = Depends(get_db)
) -> dict[str, Any]:
    """Resend inbound webhook: mail to an agent becomes queued work.

    Resend's email.received event carries metadata (id, from, to,
    subject), not the body — the body is fetched by the task runtime
    (Spec 3) via the Resend API using the provider message id, so
    body_text queues as None here by design.
    """
    secret = settings.agent_inbound_webhook_secret
    if not secret:
        # Fail-closed: no signing secret configured, no inbound path.
        raise HTTPException(status_code=503, detail="inbound email not configured")
    body = await request.body()
    verified = service.verify_inbound_signature(
        secret,
        request.headers.get("svix-id"),
        request.headers.get("svix-timestamp"),
        request.headers.get("svix-signature"),
        body,
    )
    if not verified:
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON body") from None
    if event.get("type") != "email.received":
        return {"queued": False, "ignored": True}

    data = event.get("data") or {}
    provider_message_id = str(data.get("email_id") or "")
    recipients = data.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]
    queued = 0
    for recipient in recipients:
        row = service.enqueue_inbound_email(
            db,
            to_address=str(recipient),
            from_address=str(data.get("from") or ""),
            subject=data.get("subject"),
            body_text=None,
            provider_message_id=provider_message_id,
        )
        if row is not None:
            queued += 1
    # Always 200 once the signature verifies: unknown recipients queue
    # nothing (fail-closed) and the response must not reveal which
    # agent addresses exist.
    return {"queued": queued > 0, "queued_count": queued}
