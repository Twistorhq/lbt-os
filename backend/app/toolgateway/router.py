"""TW-800 Twistor Trades agent tool gateway - HTTP API.

The runtime surface: invoke one MCP-style tool call through the
gate, list the calls parked for approval, decide a parked call, and
read the per-call audit feed. The caller's active org scopes
everything; every invoke and decision is attributed to the caller
from the Identity JWT.

Deciding an approval is a mutation and is role-gated exactly like
the TW-768 ledger mutations (TW-779): the token must carry an owner
or admin role claim. Approving a parked call is the owner saying
"yes, this one" - a staff token must not be able to say it. Invokes
stay member-visible: the agent acts under the caller's identity and
the ledger + policy do the gating.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..database import get_db
from . import service
from .models import GatewayError

router = APIRouter(prefix="/agent-tools", tags=["agent-tools"])
_bearer_scheme = HTTPBearer(auto_error=False)


def _verified_claims(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    # Local import: identity is the auth authority; the gateway must
    # not import it at module load (keeps the package importable in
    # tests). Same pattern as the agentperms router (TW-768).
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


# Roles the Identity service issues that may decide approvals
# (idn_org_memberships carries owner/admin/member). A missing role
# claim fails closed, like every other check in this stack.
MUTATION_ROLES = ("owner", "admin")


def _require_mutation_role(claims: dict = Depends(_verified_claims)) -> None:
    """TW-779 shape: deciding a parked call is an owner act."""
    if claims.get("role") not in MUTATION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="deciding an approval requires an owner or admin role",
        )


def _require_actor(claims: dict = Depends(_verified_claims)) -> str:
    return str(claims.get("sub") or claims.get("email") or "unknown")


class CallBody(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    amount_cents: int | None = None


class DecideBody(BaseModel):
    approve: bool


@router.post("/call")
def post_call(
    body: CallBody,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    outcome = service.invoke(
        db,
        org_id,
        body.tool,
        body.args,
        actor=actor,
        amount_cents=body.amount_cents,
    )
    return outcome.model_dump()


@router.get("/approvals")
def get_approvals(org_id: str = Depends(_require_org), db: Client = Depends(get_db)):
    return {
        "org_id": org_id,
        "approvals": service.list_approvals(db, org_id, status="pending"),
    }


@router.post("/approvals/{approval_id}/decide")
def post_decide(
    approval_id: str,
    body: DecideBody,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    _role_gate: None = Depends(_require_mutation_role),
    db: Client = Depends(get_db),
):
    try:
        outcome = service.decide(
            db, org_id, approval_id, actor=actor, approve=body.approve
        )
    except GatewayError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail else 409
        raise HTTPException(status_code=status, detail=detail) from exc
    return outcome.model_dump()


@router.get("/calls")
def get_calls(org_id: str = Depends(_require_org), db: Client = Depends(get_db)):
    return {"org_id": org_id, "calls": service.list_calls(db, org_id)}


@router.get("/chain")
def get_chain(org_id: str = Depends(_require_org), db: Client = Depends(get_db)):
    return {"org_id": org_id, **service.verify_chain(db, org_id)}
