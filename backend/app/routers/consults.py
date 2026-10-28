"""TW-734 video consult router.

Authenticated endpoints are org-scoped via get_auth; cross-org access
returns 404 (never 403 — no org existence leak). The two public join
endpoints carry no session auth: the join token (SHA-256 hashed at
rest, 192-bit entropy) is the customer's only credential.

Provider-facing actions fail closed (503) until the go-live wiring
exists — Jitsi base URL configured (TW-729 package deployed), the
selected phone provider configured — nothing half-live.

PII doctrine (TW-552): responses carry masked numbers only.
"""

import logging
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..services import consult_providers as cp
from ..services import video_consult as vc
from ..services.voice_providers import ProviderNotConfigured, get_voice_agent

log = logging.getLogger(__name__)

router = APIRouter(prefix="/consults")


def _room_or_503():
    try:
        return cp.get_room_provider()
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _voice_agent_or_503():
    try:
        return get_voice_agent()
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _telephony_or_503():
    from ..services.voice_providers import get_telephony_provider

    try:
        return get_telephony_provider()
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))


class ConsultSettingsBody(BaseModel):
    enabled: Optional[bool] = None
    pricing_mode: Optional[str] = None
    per_minute_rate_cents: Optional[int] = None
    flat_rate_cents: Optional[int] = None
    tech_join_window_seconds: Optional[int] = None


@router.get("/settings")
def consult_settings(
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Shop toggle + consult pricing (per-minute or flat)."""
    cfg = vc.get_consult_settings(db, auth.org_id)
    cfg.pop("org_id", None)
    return cfg


@router.put("/settings")
def consult_settings_update(
    body: ConsultSettingsBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        cfg = vc.update_consult_settings(db, auth.org_id, **fields)
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    cfg.pop("org_id", None)
    return cfg


class CreateSessionBody(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    issue: Optional[str] = None


@router.post("/sessions")
def create_session(
    body: CreateSessionBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Open a consult and mint the customer's join link. The AI
    disclosure is spoken before the room is handed over."""
    room_provider = _room_or_503()
    voice_agent = _voice_agent_or_503()
    try:
        created = vc.create_session(
            db,
            org_id=auth.org_id,
            customer_name=body.customer_name,
            customer_phone=body.customer_phone,
            issue=body.issue,
            room_provider=room_provider,
            voice_agent=voice_agent,
            now=datetime.now(timezone.utc),
        )
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "session_id": created["id"],
        "status": created["status"],
        "join_url": created["join_url"],
        "join_token": created["join_token"],  # shown once, never stored raw
        "room_url": created["room_url"],
        "triage_deadline_at": created["triage_deadline_at"],
    }


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    try:
        row = vc.get_session(db, org_id=auth.org_id, session_id=session_id)
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return vc.public_view(row)


@router.post("/sessions/{session_id}/tech-join")
def tech_join(
    session_id: str,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """The tech joins: consult goes live, billable clock starts."""
    try:
        row = vc.tech_join(
            db,
            org_id=auth.org_id,
            session_id=session_id,
            tech_user_id=auth.user_id,
            now=datetime.now(timezone.utc),
        )
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return {"session_id": session_id, "status": row["status"]}


class RecordingBody(BaseModel):
    on: bool
    actor: str = "tech"


@router.post("/sessions/{session_id}/recording")
def session_recording(
    session_id: str,
    body: RecordingBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Per-session recording toggle. Switching ON speaks the
    announcement before recording starts."""
    room_provider = _room_or_503()
    voice_agent = _voice_agent_or_503()
    try:
        row = vc.set_recording(
            db,
            org_id=auth.org_id,
            session_id=session_id,
            on=body.on,
            actor=body.actor,
            room_provider=room_provider,
            voice_agent=voice_agent,
            now=datetime.now(timezone.utc),
        )
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"session_id": session_id, "recording_enabled": row["recording_enabled"]}


@router.post("/sessions/{session_id}/expire-triage")
def expire_triage(
    session_id: str,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """The join window lapsed: answering machine + owner escalation.
    In production a worker sweeps for lapsed windows; this endpoint
    is the same transition, triggered explicitly."""
    telephony = _telephony_or_503()
    try:
        row = vc.expire_triage(
            db,
            org_id=auth.org_id,
            session_id=session_id,
            telephony=telephony,
            now=datetime.now(timezone.utc),
        )
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "session_id": session_id,
        "status": row["status"],
        "escalation_chain_id": row.get("escalation_chain_id"),
    }


@router.post("/sessions/{session_id}/end")
def end_session(
    session_id: str,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    try:
        row = vc.end_session(
            db,
            org_id=auth.org_id,
            session_id=session_id,
            now=datetime.now(timezone.utc),
        )
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return {
        "session_id": session_id,
        "status": row["status"],
        "billable_minutes": row.get("billable_minutes"),
        "charge_cents": row.get("charge_cents"),
        "charge_status": row.get("charge_status"),
    }


class ConvertBody(BaseModel):
    requested_at: Optional[str] = None


@router.post("/sessions/{session_id}/convert")
def convert_to_visit(
    session_id: str,
    body: ConvertBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Video couldn't fix it: book the real visit, problem seen."""
    telephony = _telephony_or_503()
    try:
        row = vc.convert_to_visit(
            db,
            org_id=auth.org_id,
            session_id=session_id,
            telephony=telephony,
            requested_at=body.requested_at,
            now=datetime.now(timezone.utc),
        )
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "session_id": session_id,
        "status": row["status"],
        "job_id": row.get("job_id"),
        "appointment_id": row.get("appointment_id"),
        "charge_cents": row.get("charge_cents"),
        "charge_status": row.get("charge_status"),
    }


# ---------------------------------------------------------------------------
# Public customer surface — the token is the credential.
# ---------------------------------------------------------------------------


@router.get("/join/{token}")
def customer_join(token: str, db=Depends(get_db)):
    """What the homeowner's browser loads from the link/QR."""
    try:
        return vc.customer_join(db, token=token)
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


class LeaveMessageBody(BaseModel):
    recording_ref: str


@router.post("/join/{token}/message")
def leave_message(token: str, body: LeaveMessageBody, db=Depends(get_db)):
    """Answering machine: store the video message; transcription via
    the local faster-whisper path (pending until the bridge wires)."""
    try:
        msg = vc.leave_message(
            db,
            token=token,
            recording_ref=body.recording_ref,
            transcriber=cp.get_transcriber(),
            now=datetime.now(timezone.utc),
        )
    except vc.VideoConsultError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return {
        "message_id": msg["id"],
        "transcription_status": msg["transcription_status"],
        "summary": msg["summary"],
    }
