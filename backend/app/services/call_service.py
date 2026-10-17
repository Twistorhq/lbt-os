"""M2 missed-call layer service (TW-556, Jobber wedge).

Twilio voice webhooks -> call_logs (bronze) -> missed-call detection ->
instant auto-SMS (business hours) or callback lead (after hours).
Deterministic IVR-lite; no LLM in v1.

PII doctrine (TW-552): raw numbers live ONLY in call_logs (bronze, needed for
SMS-back). Everything returned upstream is masked. No PII in logs.
"""
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII helpers
# ---------------------------------------------------------------------------

def _hash_number(phone: str) -> str:
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def _mask_number(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else digits
    return f"(***) ***-{last4}"


# ---------------------------------------------------------------------------
# Twilio signature validation
# ---------------------------------------------------------------------------

def _validate_twilio_signature(url: str, params: dict, signature: str,
                               auth_token: str | None) -> bool:
    """HMAC-SHA1 over full URL + sorted params, per Twilio's signing scheme."""
    if not auth_token or not signature:
        return False
    try:
        from twilio.request_validator import RequestValidator
        return RequestValidator(auth_token).validate(url, params, signature)
    except Exception as exc:  # never fail open
        log.warning("twilio signature validation error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Org resolution (by the shop's Twilio number; unknown -> None -> 404)
# ---------------------------------------------------------------------------

def _resolve_org_id(db, to_number: str) -> str | None:
    res = (
        db.table("org_phone_numbers")
        .select("org_id")
        .eq("phone_number", to_number)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0]["org_id"] if rows else None


# ---------------------------------------------------------------------------
# Business hours: Mon-Fri 07:00-19:00 in the org's state timezone (v1 rule)
# ---------------------------------------------------------------------------

_STATE_TZ = {
    "CO": "America/Denver", "TX": "America/Chicago", "NY": "America/New_York",
    "CA": "America/Los_Angeles", "FL": "America/New_York", "AZ": "America/Phoenix",
    "WA": "America/Los_Angeles", "IL": "America/Chicago", "GA": "America/New_York",
    "NC": "America/New_York", "OH": "America/New_York", "PA": "America/New_York",
}


def _in_business_hours(now_utc: datetime, state: str | None) -> bool:
    tz = ZoneInfo(_STATE_TZ.get((state or "").upper(), "America/Denver"))
    local = now_utc.astimezone(tz)
    if local.weekday() >= 5:  # Sat/Sun
        return False
    return 7 <= local.hour < 19


def _next_morning_8am(now_utc: datetime, state: str | None) -> datetime:
    tz = ZoneInfo(_STATE_TZ.get((state or "").upper(), "America/Denver"))
    local = now_utc.astimezone(tz)
    nxt = local.replace(hour=8, minute=0, second=0, microsecond=0)
    if local.hour >= 8:
        nxt = nxt + timedelta(days=1)
    while nxt.weekday() >= 5:  # skip to Monday
        nxt = nxt + timedelta(days=1)
    return nxt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# 10DLC gate
# ---------------------------------------------------------------------------

def _ten_dlc_configured() -> bool:
    from ..config import settings
    return bool(getattr(settings, "twilio_10dlc_configured", False))


# ---------------------------------------------------------------------------
# SMS sending (single attempt; retry loop lives in the recovery worker)
# ---------------------------------------------------------------------------

def _send_recovery_sms(*, to: str, body: str) -> None:
    from ..config import settings
    if not all([settings.twilio_account_sid, settings.twilio_auth_token,
                settings.twilio_from_number]):
        raise RuntimeError("twilio-not-configured")
    from twilio.rest import Client
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    client.messages.create(to=to, from_=settings.twilio_from_number, body=body)
    log.info("recovery SMS sent to %s", _mask_number(to))


_SMS_TEMPLATE = (
    "Sorry we missed you — this is {shop}. Reply BOOK and we'll get you "
    "scheduled. Reply STOP to opt out."
)


# ---------------------------------------------------------------------------
# Core webhook handler
# ---------------------------------------------------------------------------

def _match_customer(db, org_id: str, from_number: str) -> str | None:
    # Bronze-to-bronze match on the operational customers table.
    res = (
        db.table("customers")
        .select("id")
        .eq("org_id", org_id)
        .eq("phone", from_number)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0]["id"] if rows else None


def handle_voice_webhook(*, db, url: str, params: dict,
                         signature: str) -> dict:
    """Process one Twilio voice webhook. Returns a result dict for the router.

    Raises _WebhookError with an HTTP status for 401/404 cases.
    """
    from ..config import settings

    if not _validate_twilio_signature(url, params, signature,
                                      settings.twilio_auth_token):
        raise _WebhookError(401, "invalid Twilio signature")

    to_number = params.get("To", "")
    org_id = _resolve_org_id(db, to_number)
    if not org_id:
        raise _WebhookError(404, "unknown destination number")

    call_sid = params.get("CallSid", "")
    # Idempotency: Twilio retries webhooks.
    existing = (
        db.table("call_logs").select("id").eq("twilio_call_sid", call_sid)
        .limit(1).execute()
    )
    if existing.data:
        return {"ok": True, "duplicate": True,
                "call_log_id": existing.data[0]["id"]}

    from_number = params.get("From", "")
    status = params.get("CallStatus", "")
    caller_hash = _hash_number(from_number)
    customer_id = _match_customer(db, org_id, from_number)

    row = {
        "org_id": org_id,
        "twilio_call_sid": call_sid,
        "direction": params.get("Direction", "inbound"),
        "from_number": from_number,          # bronze only
        "from_number_hash": caller_hash,
        "to_number": to_number,              # bronze only
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "customer_id": customer_id,
    }
    created = db.table("call_logs").insert(row).execute()
    call_log_id = (created.data or [{}])[0].get("id")

    # Missed-call detection: inbound no-answer/busy/failed.
    if row["direction"] == "inbound" and status in ("no-answer", "busy", "failed"):
        _run_recovery(db=db, org_id=org_id, call_log_id=call_log_id,
                      caller=from_number, to_number=to_number)

    return {"ok": True, "duplicate": False, "call_log_id": call_log_id}


def _run_recovery(*, db, org_id: str, call_log_id: str,
                  caller: str, to_number: str) -> None:
    """Business-hours -> instant SMS; after-hours/no-10DLC -> callback lead."""
    from .toll_service import record_toll_event

    record_toll_event(db, org_id, "missed_call.detected", 1,
                      idempotency_key=f"m2-detect-{call_log_id}",
                      source_type="call_log", source_id=call_log_id)

    recovery = {
        "org_id": org_id,
        "call_log_id": call_log_id,
    }
    created = db.table("missed_call_recoveries").insert(recovery).execute()
    recovery_id = (created.data or [{}])[0].get("id")

    org = (
        db.table("organizations").select("name,state").eq("id", org_id)
        .limit(1).execute()
    )
    org_row = (org.data or [{}])[0]
    now = datetime.now(timezone.utc)

    if not _in_business_hours(now, org_row.get("state")):
        _callback_lead(db=db, org_id=org_id, caller=caller, now=now,
                       state=org_row.get("state"), reason="after-hours")
        return
    if not _ten_dlc_configured():
        _callback_lead(db=db, org_id=org_id, caller=caller, now=now,
                       state=org_row.get("state"),
                       reason="10dlc-not-configured")
        return

    body = _SMS_TEMPLATE.format(shop=org_row.get("name", "us"))
    sms_error = None
    for attempt in range(3):
        try:
            _send_recovery_sms(to=caller, body=body)
            sms_error = None
            break
        except Exception as exc:  # noqa: BLE001 — self-healing: retry then dead-letter
            sms_error = str(exc)
            log.warning("recovery SMS attempt %d failed: %s", attempt + 1, sms_error)
            time.sleep(0.5 * (2 ** attempt))

    if sms_error is not None:
        # Dead-letter: sms_sent_at stays NULL, visible in GET /calls/missed
        # for manual recovery. The webhook already returned 200.
        db.table("missed_call_recoveries").update(
            {"sms_error": sms_error}).eq("id", recovery_id).execute()
        return
    db.table("missed_call_recoveries").update(
        {"sms_sent_at": now.isoformat(), "sms_body": body}
    ).eq("id", recovery_id).execute()
    try:
        record_toll_event(db, org_id, "sms.auto_reply", 1,
                          idempotency_key=f"m2-sms-{call_log_id}",
                          source_type="missed_call_recovery",
                          source_id=recovery_id)
    except Exception as exc:  # toll failure never breaks recovery
        log.warning("toll emit failed (non-fatal): %s", exc)


def _callback_lead(*, db, org_id: str, caller: str, now: datetime,
                   state: str | None, reason: str) -> None:
    db.table("leads").insert({
        "org_id": org_id,
        "name": f"Callback {_mask_number(caller)}",
        "phone": caller,  # bronze: leads table is operational, masked in API
        "source": "missed_call",
        "status": "new",
        "notes": f"Missed call ({reason}); auto-created by M2 recovery worker.",
        "follow_up_at": _next_morning_8am(now, state).isoformat(),
    }).execute()
    log.info("callback lead created (%s)", reason)


# ---------------------------------------------------------------------------
# Money view + manual recovery
# ---------------------------------------------------------------------------

def get_missed_calls(*, db, org_id: str, days: int = 7) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    logs = (
        db.table("call_logs").select("*").eq("org_id", org_id)
        .in_("status", ["no-answer", "busy", "failed"])
        .execute()
    ).data or []
    recs = (
        db.table("missed_call_recoveries").select("*").eq("org_id", org_id)
        .execute()
    ).data or []
    rec_by_log = {r["call_log_id"]: r for r in recs}
    out = []
    for entry in logs:
        if (entry.get("started_at") or "") < since:
            continue
        r = rec_by_log.get(entry["id"], {})
        out.append({
            "call_log_id": entry["id"],
            "from_number_masked": _mask_number(entry.get("from_number", "")),
            "started_at": entry.get("started_at"),
            "status": entry.get("status"),
            "sms_sent_at": r.get("sms_sent_at"),
            "sms_error": r.get("sms_error"),
            "recovered_at": r.get("recovered_at"),
            "revenue_attributed": r.get("revenue_attributed"),
        })
    out.sort(key=lambda x: x["started_at"] or "", reverse=True)
    return out


def recover_call(*, db, org_id: str, call_log_id: str, outcome: str,
                 appointment_id: str | None = None,
                 revenue: float | None = None) -> dict:
    logs = (
        db.table("call_logs").select("id").eq("id", call_log_id)
        .eq("org_id", org_id).limit(1).execute()
    ).data or []
    if not logs:
        raise _WebhookError(404, "call log not found")
    now = datetime.now(timezone.utc).isoformat()
    db.table("missed_call_recoveries").update({
        "recovered_at": now,
        "resulting_appointment_id": appointment_id,
        "revenue_attributed": revenue,
    }).eq("call_log_id", call_log_id).eq("org_id", org_id).execute()
    return {"ok": True, "outcome": outcome, "recovered_at": now}


class _WebhookError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail
