"""TW-768 Twistor Trades agent permission ledger — HTTP API.

The owner surface contract for Spec 6's Permission Center: list the
ledger, grant or deny a class, revoke in three strengths, and read
the append-only history (the audit feed's permission source) and
revocation receipts. Every mutation is attributed to the caller
from the Identity JWT; the caller's active org scopes everything.

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


def _require_actor(claims: dict = Depends(_verified_claims)) -> str:
    return str(claims.get("sub") or claims.get("email") or "unknown")


class GrantBody(BaseModel):
    action_class: str
    scope: dict[str, Any] = {}
    cap_cents: int | None = None


class DenyBody(BaseModel):
    action_class: str


class RevokeBody(BaseModel):
    strength: str
    target: str | None = None


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
        )
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/denies")
def post_deny(
    body: DenyBody,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        return service.deny_permission(db, org_id, body.action_class, actor=actor)
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/revoke")
def post_revoke(
    body: RevokeBody,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        statement = RevokeStatement(strength=body.strength, target=body.target)
        revocation = service.revoke(db, org_id, statement, actor=actor)
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
