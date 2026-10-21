"""TW-650/TW-660 Twistor Social — HTTP API.

Routes are live and authenticated. TW-660 implemented the core loop
(groups, events, series, RSVPs, location reveal, gold listings);
check-ins and reports still delegate to service stubs that answer an
honest 501 until their phases land. A stub must never pretend to
work.

Auth: Twistor Identity JWT (RS256), verified through the identity
service exactly as Telemetry does. The member's user_id is the token
subject; it is never taken from a request body. Ghost-town scopes
(no passed T2 verdict, no voice — spec Section 5.11) are enforced in
the service layer, which re-reads verification state per request;
token minting keeps its own scope discipline Identity-side.

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
    """Run a service call, converting service refusals into HTTP:
    NotImplementedError is the honest 501 (a phase that has not
    landed), NotFoundError is the Section 5.9 restricted shell (an
    invisible target answers exactly like a nonexistent one),
    ForbiddenError an entitlement refusal, ConflictError a state
    conflict. SQLAlchemy/Postgres errors are never mapped here."""
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from None
    except service.NotFoundError:
        raise HTTPException(status_code=404, detail="not found") from None
    except service.ForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except service.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


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


@router.get("/groups")
@limiter.limit("300/minute")
def list_groups(
    request: Request,
    metro: str | None = None,
    category: str | None = None,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(
        service.list_groups, db, UUID(claims["sub"]), metro=metro, category=category
    )


@router.get("/groups/{group_id}")
@limiter.limit("300/minute")
def get_group(
    request: Request,
    group_id: UUID,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.get_group, db, UUID(claims["sub"]), group_id)


@router.post("/groups/{group_id}/join", status_code=201)
@limiter.limit("60/minute")
def join_group(
    request: Request,
    group_id: UUID,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.join_group, db, UUID(claims["sub"]), group_id)


@router.post("/groups/{group_id}/memberships/{user_id}/decision")
@limiter.limit("60/minute")
def decide_membership(
    request: Request,
    group_id: UUID,
    user_id: UUID,
    payload: models.MembershipDecision,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(
        service.decide_membership,
        db,
        UUID(claims["sub"]),
        group_id,
        user_id,
        payload,
    )


@router.post("/groups/{group_id}/memberships/{user_id}/role")
@limiter.limit("60/minute")
def set_member_role(
    request: Request,
    group_id: UUID,
    user_id: UUID,
    payload: models.MemberRoleUpdate,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(
        service.set_member_role,
        db,
        UUID(claims["sub"]),
        group_id,
        user_id,
        payload,
    )


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
    return _call(service.get_event, db, UUID(claims["sub"]), event_id)


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
    # The service re-evaluates the full Section 8 condition set at
    # request time and logs every successful reveal; the 5/minute
    # bucket is the strictest on the router by design (Section 3).
    return _call(service.reveal_event_location, db, UUID(claims["sub"]), event_id)


@router.post("/events/{event_id}/location")
@limiter.limit("60/minute")
def set_event_location(
    request: Request,
    event_id: UUID,
    payload: models.EventLocationIn,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.set_event_location, db, UUID(claims["sub"]), event_id, payload)


@router.post("/rsvps/{rsvp_id}/withdraw")
@limiter.limit("60/minute")
def withdraw_rsvp(
    request: Request,
    rsvp_id: UUID,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.withdraw_rsvp, db, UUID(claims["sub"]), rsvp_id)


@router.post("/series", status_code=201)
@limiter.limit("60/minute")
def create_series(
    request: Request,
    payload: models.SeriesCreate,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.create_series, db, UUID(claims["sub"]), payload)


@router.get("/series/{series_id}")
@limiter.limit("300/minute")
def get_series(
    request: Request,
    series_id: UUID,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.get_series, db, UUID(claims["sub"]), series_id)


@router.post("/series/{series_id}/location")
@limiter.limit("60/minute")
def set_series_location(
    request: Request,
    series_id: UUID,
    payload: models.EventLocationIn,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(
        service.set_series_location, db, UUID(claims["sub"]), series_id, payload
    )


@router.get("/listings")
@limiter.limit("300/minute")
def list_listings(
    request: Request,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.list_listings, db, UUID(claims["sub"]))


@router.get("/listings/{listing_id}")
@limiter.limit("300/minute")
def get_listing(
    request: Request,
    listing_id: str,
    claims: dict = Depends(_verified_claims),
    db: Client = Depends(get_db),
):
    return _call(service.get_listing, db, UUID(claims["sub"]), listing_id)


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
