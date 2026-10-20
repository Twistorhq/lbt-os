"""TW-650 Twistor Social — HTTP API (Phase 3 skeleton).

Routes are live and authenticated; every feature endpoint delegates
to the service layer, which raises until its build phase lands, and
answers an honest 501. A skeleton must never pretend to work.

Auth: Twistor Identity JWT (RS256), verified through the identity
service exactly as Telemetry does. The member's user_id is the token
subject; it is never taken from a request body. Ghost-town write
scopes (no passed T2 verdict, no write scopes — spec Section 5.11)
are enforced at token minting and land with tasks.md item 11/17;
this skeleton authenticates identity only and implements no writes.

Rate limits (spec Section 3): buckets on every endpoint, strictest
on location reveal and report filing.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client

from ..database import get_db
from ..limiter import limiter
from . import models, service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/social", tags=["social"])
_bearer_scheme = HTTPBearer(auto_error=False)


def _verified_claims(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    if not creds:
        raise HTTPException(status_code=401, detail="missing credentials")
    # Local import: identity is the auth authority; social must not
    # import it at module load (keeps the package importable in tests),
    # and an unauthenticated request never pays for the import.
    from ..identity import service as idn_service
    from ..identity import tokens as idn_tokens

    try:
        claims = idn_tokens.verify_access_token(
            idn_service.get_verification_keypairs(), creds.credentials
        )
    except idn_tokens.TokenError:
        raise HTTPException(status_code=401, detail="invalid token") from None
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="token has no subject")
    return claims


def _call(fn, *args, **kwargs) -> Any:
    """Run a service call, converting the skeleton's honest
    NotImplementedError into a 501 with the phase named."""
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from None


@router.get("/profiles/me")
@limiter.limit("300/minute")
def get_my_profile(
    request: Request,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.get_my_profile, db, UUID(claims["sub"]))


@router.put("/profiles/me")
@limiter.limit("60/minute")
def upsert_my_profile(
    request: Request,
    payload: models.ProfileIn,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.upsert_my_profile, db, UUID(claims["sub"]), payload)


@router.post("/groups", status_code=201)
@limiter.limit("60/minute")
def create_group(
    request: Request,
    payload: models.GroupCreate,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.create_group, db, UUID(claims["sub"]), payload)


@router.get("/groups/{group_id}")
@limiter.limit("300/minute")
def get_group(
    request: Request,
    group_id: UUID,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.get_group, db, group_id)


@router.post("/events", status_code=201)
@limiter.limit("60/minute")
def create_event(
    request: Request,
    payload: models.EventCreate,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.create_event, db, UUID(claims["sub"]), payload)


@router.get("/events/{event_id}")
@limiter.limit("300/minute")
def get_event(
    request: Request,
    event_id: UUID,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.get_event, db, event_id)


@router.post("/events/{event_id}/rsvps", status_code=201)
@limiter.limit("60/minute")
def request_rsvp(
    request: Request,
    event_id: UUID,
    payload: models.RsvpCreate,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    if payload.event_id != event_id:
        raise HTTPException(
            status_code=422, detail="event_id in body must match the path"
        )
    return _call(service.request_rsvp, db, UUID(claims["sub"]), payload)


@router.post("/rsvps/{rsvp_id}/decision")
@limiter.limit("60/minute")
def decide_rsvp(
    request: Request,
    rsvp_id: UUID,
    payload: models.RsvpDecision,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.decide_rsvp, db, UUID(claims["sub"]), rsvp_id, payload)


@router.post("/events/{event_id}/reveal")
@limiter.limit("5/minute")
def reveal_event_location(
    request: Request,
    event_id: UUID,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    # Fail-closed by doctrine: until Phase 4 implements the full
    # condition set, this returns nothing, to anyone, ever.
    return _call(service.reveal_event_location, db, UUID(claims["sub"]), event_id)


@router.post("/events/{event_id}/checkins", status_code=201)
@limiter.limit("60/minute")
def create_checkin(
    request: Request,
    event_id: UUID,
    payload: models.CheckinCreate,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    if payload.event_id != event_id:
        raise HTTPException(
            status_code=422, detail="event_id in body must match the path"
        )
    return _call(service.create_checkin, db, UUID(claims["sub"]), payload)


@router.post("/reports", status_code=201)
@limiter.limit("10/hour")
def create_report(
    request: Request,
    payload: models.ReportCreate,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.create_report, db, UUID(claims["sub"]), payload)
