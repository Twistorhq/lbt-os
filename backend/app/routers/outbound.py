"""Outbound sales agent router (TW-884, epic TW-883).

Org-scoped via get_auth; responses carry masked numbers only (PII
doctrine). The dial endpoint is deliberately NOT a live endpoint in
slice 1: the conversation runs on the responder port (the voice
agent's live bridge), which is go-live wiring, so POST /dial enforces
the kill switches and then fails closed with 503 until the bridge
lands. Everything else — settings, revocation, import, scrub,
opt-out intake, rollup — is real.
"""

import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..services import outbound_calls as oc
from ..services import outbound_lookups as lk

log = logging.getLogger(__name__)

router = APIRouter(prefix="/outbound")


class SettingsBody(BaseModel):
    enabled: bool | None = None
    persona_name: str | None = None
    timezone: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    lunch_start: str | None = None
    lunch_end: str | None = None
    call_days: list[int] | None = None
    daily_cap: int | None = None
    demo_duration_min: int | None = None
    demo_buffer_min: int | None = None
    demo_window_start: str | None = None
    demo_window_end: str | None = None
    calendar_id: str | None = None


class ImportBody(BaseModel):
    rows: list[dict]


class ScrubBody(BaseModel):
    batch_id: str
    registry_version: str


class OptOutBody(BaseModel):
    phone: str
    source: str = "web_optout"
    reason: str | None = None


def _masked(prospect: dict) -> dict:
    out = dict(prospect)
    out.pop("phone", None)  # bronze only; masked value stays
    return out


@router.get("/settings")
def get_settings(db=Depends(get_db), auth: AuthContext = Depends(get_auth)):
    agent = oc.get_agent_settings(db, auth.org_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not configured")
    agent.pop("org_id", None)
    return agent


@router.post("/settings")
def post_settings(
    body: SettingsBody,
    db=Depends(get_db),
    auth: AuthContext = Depends(get_auth),
):
    fields = body.model_dump(exclude_none=True)
    agent = oc.upsert_agent_settings(db, auth.org_id, **fields)
    agent.pop("org_id", None)
    return agent


@router.post("/revoke")
def post_revoke(db=Depends(get_db), auth: AuthContext = Depends(get_auth)):
    """TW-766 revocation: instant; in-flight work stops at the next
    utterance check."""
    agent = oc.revoke_agent(db, auth.org_id, now=datetime.now(UTC))
    agent.pop("org_id", None)
    return agent


@router.post("/prospects/import")
def post_import(
    body: ImportBody,
    db=Depends(get_db),
    auth: AuthContext = Depends(get_auth),
):
    return oc.import_prospects(db, auth.org_id, body.rows)


@router.get("/prospects")
def get_prospects(db=Depends(get_db), auth: AuthContext = Depends(get_auth)):
    rows = (
        db.table("outbound_prospects")
        .select("*")
        .eq("org_id", auth.org_id)
        .execute()
        .data
        or []
    )
    rows.sort(key=lambda p: (p.get("rank") is None, p.get("rank") or 0))
    return [_masked(r) for r in rows]


@router.post("/scrub")
def post_scrub(
    body: ScrubBody,
    db=Depends(get_db),
    auth: AuthContext = Depends(get_auth),
):
    try:
        registry = lk.get_registry_provider()
    except lk.RegistryNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return oc.scrub_prospects(
        db,
        auth.org_id,
        registry,
        batch_id=body.batch_id,
        registry_version=body.registry_version,
    )


@router.post("/opt-out")
def post_opt_out(
    body: OptOutBody,
    db=Depends(get_db),
    auth: AuthContext = Depends(get_auth),
):
    """HR-08/HR-21 intake: a revocation or opt-out from ANY channel
    lands in the same suppression ledger, honored permanently."""
    phone = oc.normalize_phone(body.phone)
    if phone is None:
        raise HTTPException(status_code=422, detail="unparseable phone")
    row = oc.record_suppression(
        db, auth.org_id, phone, source=body.source, reason=body.reason
    )
    return {"suppressed": True, "phone_masked": row.get("phone_masked")}


@router.post("/dial/{prospect_id}")
def post_dial(
    prospect_id: str,
    db=Depends(get_db),
    auth: AuthContext = Depends(get_auth),
):
    """Fail closed in slice 1: kill switches are enforced by the
    service gates reachable from here once the live conversation
    bridge exists; until then no HTTP call can start a conversation."""
    agent = oc.get_agent_settings(db, auth.org_id)
    if agent is None:
        raise HTTPException(status_code=409, detail="agent not configured")
    if not agent.get("enabled") or agent.get("revoked_at"):
        raise HTTPException(status_code=409, detail="agent disabled/revoked")
    raise HTTPException(
        status_code=503,
        detail="live conversation bridge not wired (slice 1 is test mode)",
    )


@router.get("/rollup")
def get_rollup(
    day: date,
    db=Depends(get_db),
    auth: AuthContext = Depends(get_auth),
):
    return oc.daily_rollup(db, auth.org_id, day)
