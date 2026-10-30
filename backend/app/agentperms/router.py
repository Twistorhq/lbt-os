"""TW-768 Twistor Trades agent permission ledger — HTTP API.

The owner surface contract for Spec 6's Permission Center: list the
ledger, grant or deny a class, revoke in three strengths, and read
the append-only history (the audit feed's permission source) and
revocation receipts. Every mutation is attributed to the caller
from the Identity JWT; the caller's active org scopes everything.

Mutations are additionally role-gated (TW-779 / TH-010): the token
must carry an owner or admin role claim. Org membership alone once
authorized grants and revoke-all — any staff token could arm the
money classes or stop the shop's agent — so a non-owner token gets
a clean 403 and nothing is written. Reads stay member-visible:
Spec 6 renders the ledger to the org; consent is the owner's act.

The execution-time check is deliberately NOT an HTTP endpoint: it
is an in-process service call (service.check_permission) made by
the agent runtime at the moment of action. A network hop at that
point would invite caching, and a cached grant is a revoked grant
waiting to be used.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..database import get_db
from . import service
from .models import LedgerError, RevokeStatement

router = APIRouter(prefix="/agent-permissions", tags=["agent-permissions"])
_bearer_scheme = HTTPBearer(auto_error=False)


def _verified_claims(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    # Local import: identity is the auth authority; agentperms must
    # not import it at module load (keeps the package importable in
    # tests). Same pattern as the flags router (TW-621).
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


# Roles the Identity service issues that may mutate the ledger
# (idn_org_memberships carries owner/admin/member). A missing role
# claim fails closed, like every other check in this package.
MUTATION_ROLES = ("owner", "admin")


def _require_mutation_role(claims: dict = Depends(_verified_claims)) -> None:
    """TW-779 (TR-008): grant, deny, and revoke are owner acts.

    A staff/member token — a shared login, a phished credential, an
    unoffboarded ex-employee — must not be able to arm the agent's
    money classes at an arbitrary cap or revoke-all in one call.
    """
    if claims.get("role") not in MUTATION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="grant, deny, and revoke require an owner or admin role",
        )


def _require_actor(claims: dict = Depends(_verified_claims)) -> str:
    return str(claims.get("sub") or claims.get("email") or "unknown")


class GrantBody(BaseModel):
    action_class: str
    scope: dict[str, Any] = {}
    cap_cents: int | None = None
    # TW-787: omit for an org-wide grant (every agent); set to scope
    # the grant to one agent in the roster.
    agent_identity_id: str | None = None


class DenyBody(BaseModel):
    action_class: str
    agent_identity_id: str | None = None


class RevokeBody(BaseModel):
    strength: str
    target: str | None = None
    # TW-787: omit to revoke org-wide (every agent halts); set to
    # revoke one agent only.
    agent_identity_id: str | None = None


@router.get("/grants")
def get_grants(org_id: str = Depends(_require_org), db: Client = Depends(get_db)):
    return {
        "org_id": org_id,
        "agent_status": service.agent_status(db, org_id),
        "grants": service.list_grants(db, org_id),
    }


@router.post("/grants")
def post_grant(
    body: GrantBody,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    _role_gate: None = Depends(_require_mutation_role),
    db: Client = Depends(get_db),
):
    try:
        return service.grant_permission(
            db,
            org_id,
            body.action_class,
            actor=actor,
            scope=body.scope,
            cap_cents=body.cap_cents,
            agent_identity_id=body.agent_identity_id,
        )
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/denies")
def post_deny(
    body: DenyBody,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    _role_gate: None = Depends(_require_mutation_role),
    db: Client = Depends(get_db),
):
    try:
        return service.deny_permission(
            db,
            org_id,
            body.action_class,
            actor=actor,
            agent_identity_id=body.agent_identity_id,
        )
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/revoke")
def post_revoke(
    body: RevokeBody,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    _role_gate: None = Depends(_require_mutation_role),
    db: Client = Depends(get_db),
):
    try:
        statement = RevokeStatement(strength=body.strength, target=body.target)
        revocation = service.revoke(
            db,
            org_id,
            statement,
            actor=actor,
            agent_identity_id=body.agent_identity_id,
        )
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "revocation": {k: v for k, v in revocation.items() if k != "receipt"},
        "receipt": revocation["receipt"],
    }


@router.get("/history")
def get_history(org_id: str = Depends(_require_org), db: Client = Depends(get_db)):
    return {"org_id": org_id, "events": service.list_history(db, org_id)}


@router.get("/revocations")
def get_revocations(org_id: str = Depends(_require_org), db: Client = Depends(get_db)):
    return {"org_id": org_id, "revocations": service.list_revocations(db, org_id)}
