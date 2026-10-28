"""M2 missed-call layer router (TW-556, Jobber wedge).

Webhook endpoints are Twilio-signed (no session auth); unsigned requests are
rejected with 401. All data endpoints are org-scoped via get_auth; cross-org
access returns 404 (never 403 — no org existence leak).

PII doctrine (TW-552): responses carry masked numbers only.
"""
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..services import phone_automation as pa
from ..services.call_service import (
    _resolve_org_id,
    _validate_twilio_signature,
    _WebhookError,
    get_missed_calls,
    handle_voice_webhook,
    recover_call,
)
from ..services.voice_providers import ProviderNotConfigured

log = logging.getLogger(__name__)

router = APIRouter(prefix="/calls")

_TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/voice-webhook", response_class=PlainTextResponse)
async def voice_webhook(
    request: Request,
    db=Depends(get_db),
    x_twilio_signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None,
):
    """Twilio voice status callback. Webhook-ready; the shop's Twilio number
    attaches later via Twilio Console (manual Justynn step)."""
    form = await request.form()
    params = {k: v for k, v in form.items()}
    url = str(request.url)
    try:
        result = handle_voice_webhook(
            db=db, url=url, params=params,
            signature=x_twilio_signature or "",
        )
    except _WebhookError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    log.info("voice webhook processed: %s", result.get("call_log_id"))
    return _TWIML_EMPTY


@router.post("/sms-webhook", response_class=PlainTextResponse)
async def sms_webhook(
    request: Request,
    db=Depends(get_db),
    x_twilio_signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None,
):
    """Inbound SMS (booking confirmations, 'call me back').

    v1: validates the signature, resolves the org, and acknowledges. Intent
    routing (BOOK/STOP handling) is a follow-up ticket — the recovery SMS
    already carries opt-out language per the TCPA consent model.
    """
    from ..config import settings
    from ..services.call_service import _resolve_org_id, _validate_twilio_signature

    form = await request.form()
    params = {k: v for k, v in form.items()}
    if not _validate_twilio_signature(str(request.url), params,
                                      x_twilio_signature or "",
                                      settings.twilio_auth_token):
        raise HTTPException(status_code=401, detail="invalid Twilio signature")
    org_id = _resolve_org_id(db, params.get("To", ""))
    if not org_id:
        raise HTTPException(status_code=404, detail="unknown destination number")
    log.info("inbound SMS received (org scoped, body not stored in v1)")
    return _TWIML_EMPTY


@router.get("/missed")
def missed_calls(
    days: int = 7,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """The money view: unrecovered missed calls, numbers masked."""
    return get_missed_calls(db=db, org_id=auth.org_id,
                            days=max(1, min(days, 90)))


# ---------------------------------------------------------------------------
# TW-284 AI phone automation. Data endpoints are org-scoped via
# get_auth; provider-facing actions fail closed (503) until the
# selected provider (Fonoster by default, TW-727) is configured and
# its go-live bridge exists — nothing half-live.
# ---------------------------------------------------------------------------

def _providers_or_503():
    from ..services.voice_providers import get_telephony_provider, get_voice_agent
    try:
        return get_telephony_provider(), get_voice_agent()
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))


class PhoneSettingsBody(BaseModel):
    enabled: Optional[bool] = None
    after_hours_auto: Optional[bool] = None
    timezone: Optional[str] = None
    open_days: Optional[list[int]] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None


@router.get("/phone-automation/settings")
def phone_settings(
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Shop toggle + coverage hours for AI answering (TW-284)."""
    cfg = pa.get_settings(db, auth.org_id)
    cfg.pop("org_id", None)
    return cfg


@router.put("/phone-automation/settings")
def phone_settings_update(
    body: PhoneSettingsBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = pa.update_settings(db, auth.org_id, **fields)
    cfg.pop("org_id", None)
    return cfg


class ActivateBody(BaseModel):
    minutes: int = 120


@router.post("/phone-automation/activate")
def phone_activate(
    body: ActivateBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """On-demand activation: owner turns coverage on right now."""
    from datetime import datetime, timezone
    cfg = pa.activate_manual(
        db, auth.org_id, minutes=max(1, min(body.minutes, 24 * 60)),
        now=datetime.now(timezone.utc))
    return {"manual_active_until": cfg.get("manual_active_until")}


class EmergencyContactsBody(BaseModel):
    contacts: list[dict]  # [{phone_number, label, priority}]


@router.get("/phone-automation/emergency-contacts")
def emergency_contacts_list(
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Masked, priority-ordered. Raw numbers never leave the backend."""
    return pa.list_emergency_contacts(db, auth.org_id)


@router.put("/phone-automation/emergency-contacts")
def emergency_contacts_replace(
    body: EmergencyContactsBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Owner sets the emergency numbers (multiple supported) at signup."""
    return pa.replace_emergency_contacts(db, auth.org_id, body.contacts)


class RecordingBody(BaseModel):
    on: bool
    actor: str = "owner"


@router.post("/phone-automation/sessions/{session_id}/recording")
def session_recording(
    session_id: str,
    body: RecordingBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Per-call recording toggle. Switching ON speaks the announcement
    ('this call is now being recorded') before recording starts."""
    from datetime import datetime, timezone
    telephony, voice_agent = _providers_or_503()
    try:
        session = pa.set_recording(
            db, org_id=auth.org_id, session_id=session_id, on=body.on,
            actor=body.actor, telephony=telephony,
            voice_agent=voice_agent, now=datetime.now(timezone.utc))
    except pa.PhoneAutomationError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"session_id": session_id,
            "recording_enabled": session["recording_enabled"]}


class AcknowledgeBody(BaseModel):
    contact_id: str


@router.post("/escalations/{chain_id}/acknowledge")
def escalation_acknowledge(
    chain_id: str,
    body: AcknowledgeBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    from datetime import datetime, timezone
    try:
        chain = pa.acknowledge_escalation(
            db, org_id=auth.org_id, chain_id=chain_id,
            contact_id=body.contact_id, now=datetime.now(timezone.utc))
    except pa.PhoneAutomationError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return {"chain_id": chain_id, "status": chain["status"]}


class RescheduleBody(BaseModel):
    reason: str  # no_show | double_booked


@router.post("/appointments/{appointment_id}/auto-reschedule")
def appointment_auto_reschedule(
    appointment_id: str,
    body: RescheduleBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    """Move a missed/double-booked appointment to the next free slot and
    tell the customer why it moved."""
    from datetime import datetime, timezone

    from ..services.voice_providers import get_telephony_provider
    try:
        telephony = get_telephony_provider()
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    try:
        return pa.auto_reschedule_appointment(
            db, org_id=auth.org_id, appointment_id=appointment_id,
            reason=body.reason, telephony=telephony,
            now=datetime.now(timezone.utc))
    except pa.PhoneAutomationError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.post("/ai-voice-webhook", response_class=PlainTextResponse)
async def ai_voice_webhook(
    request: Request,
    db=Depends(get_db),
    x_twilio_signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None,
):
    """Inbound call entry for AI answering. Twilio-signed. The AI only
    answers when the coverage decision says yes; otherwise the call
    falls through untouched (empty TwiML, M2 behavior continues)."""
    from datetime import datetime, timezone

    from ..config import settings

    form = await request.form()
    params = {k: v for k, v in form.items()}
    if not _validate_twilio_signature(str(request.url), params,
                                      x_twilio_signature or "",
                                      settings.twilio_auth_token):
        raise HTTPException(status_code=401, detail="invalid Twilio signature")
    org_id = _resolve_org_id(db, params.get("To", ""))
    if not org_id:
        raise HTTPException(status_code=404, detail="unknown destination number")

    now = datetime.now(timezone.utc)
    decision = pa.ai_should_answer(db, org_id, now=now)
    if not decision["answer"]:
        return _TWIML_EMPTY
    if not settings.phone_automation_voice_stream_url:
        # Coverage is on but the media bridge is not wired (go-live step):
        # fail closed, never half-answer a caller.
        log.error("TW-284 coverage active but voice stream URL unset (org %s)",
                  org_id)
        return _TWIML_EMPTY
    telephony, voice_agent = _providers_or_503()
    try:
        pa.start_ai_call(
            db, org_id=org_id, call_sid=params.get("CallSid", ""),
            from_number=params.get("From", ""), telephony=telephony,
            voice_agent=voice_agent, now=now)
    except pa.PhoneAutomationError as exc:
        log.warning("AI call start refused: %s", exc.detail)
        return _TWIML_EMPTY
    stream = settings.phone_automation_voice_stream_url
    return ('<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
            f'<Stream url="{stream}"/></Connect></Response>')


class RecoverBody(BaseModel):
    outcome: str  # called_back | booked | lost
    appointment_id: Optional[str] = None
    revenue: Optional[float] = None


@router.post("/{call_log_id}/recover")
def recover(
    call_log_id: str,
    body: RecoverBody,
    db=Depends(get_db),
    auth: Annotated[AuthContext, Depends(get_auth)] = None,
):
    try:
        return recover_call(
            db=db, org_id=auth.org_id, call_log_id=call_log_id,
            outcome=body.outcome, appointment_id=body.appointment_id,
            revenue=body.revenue,
        )
    except _WebhookError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
