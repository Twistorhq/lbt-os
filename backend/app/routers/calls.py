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
from ..services.call_service import (
    _WebhookError,
    get_missed_calls,
    handle_voice_webhook,
    recover_call,
)

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
    from ..services.call_service import _resolve_org_id, _validate_twilio_signature
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
