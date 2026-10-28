"""TW-284 AI phone automation service (Path A: Twilio + OpenAI Realtime).

Implements Justynn's 2026-10-27 rulings:
- Coverage 24/7, on as needed: after-hours auto-mode (per-shop hours
  config) AND manual on-demand activation with an expiry.
- Recording is a per-call toggle (starts OFF). Switching it ON mid-call
  makes the agent speak the recording announcement at that moment, before
  recording starts; every change is audit-logged.
- The agent discloses it is an AI in its first utterance (CO AI-disclosure
  law eff. 2027-01-01; two-party-consent states).
- Emergency escalation: the owner sets MULTIPLE emergency numbers; they
  are rung in priority order until one acknowledges. Unacknowledged
  escalation is bounded (max rounds) and lands in an `exhausted` final
  state — visible, never silently dropped.
- Emergency intake books onto the docket (job + appointment); missed or
  double-booked appointments auto-reschedule to the next free slot and
  the customer is told why it moved.

Nothing here goes live on its own: the global kill switch
(settings.phone_automation_enabled) defaults OFF, providers fail closed
without credentials, and the Twilio number purchase is Justynn's manual
step. PII doctrine (TW-552): raw numbers live only in bronze columns
(caller_number, emergency_contacts.phone_number); API-facing lists mask.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .call_service import _STATE_TZ, _hash_number, _mask_number, _match_customer

log = logging.getLogger(__name__)

RECORDING_ANNOUNCEMENT = "This call is now being recorded."

DISCLOSURE_TEMPLATE = (
    "Hi, this is the automated AI assistant answering for {shop}. "
    "I'm an AI assistant, not a person. I can take a message, book an "
    "emergency visit, and reach the owner right away if it's urgent."
)

_DEFAULT_SETTINGS = {
    "enabled": False,
    "after_hours_auto": True,
    "timezone": None,
    "open_days": [1, 2, 3, 4, 5],  # ISO weekdays, Mon=1
    "open_time": "07:00",
    "close_time": "19:00",
    "manual_active_until": None,
}

_REASON_TEXT = {
    "no_show": "we missed you at the original time",
    "double_booked": "a scheduling conflict on our end",
}


class PhoneAutomationError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# Config gates
# ---------------------------------------------------------------------------


def _global_enabled() -> bool:
    from ..config import settings

    return bool(getattr(settings, "phone_automation_enabled", False))


def _max_rounds() -> int:
    from ..config import settings

    return int(getattr(settings, "phone_automation_max_rounds", 2) or 2)


# ---------------------------------------------------------------------------
# Settings (per-shop toggle + hours)
# ---------------------------------------------------------------------------


def get_settings(db, org_id: str) -> dict:
    rows = (
        db.table("phone_automation_settings")
        .select("*")
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if rows:
        return {**_DEFAULT_SETTINGS, **rows[0]}
    return {**_DEFAULT_SETTINGS, "org_id": org_id}


def update_settings(db, org_id: str, **fields) -> dict:
    allowed = {k: v for k, v in fields.items() if k in _DEFAULT_SETTINGS}
    existing = (
        db.table("phone_automation_settings")
        .select("*")
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if existing:
        if allowed:
            db.table("phone_automation_settings").update(allowed).eq(
                "org_id", org_id
            ).execute()
    else:
        db.table("phone_automation_settings").insert(
            {"org_id": org_id, **_DEFAULT_SETTINGS, **allowed}
        ).execute()
    return get_settings(db, org_id)


def activate_manual(db, org_id: str, *, minutes: int, now: datetime) -> dict:
    """Owner turns coverage on right now (e.g. mid-job) for `minutes`."""
    until = now + timedelta(minutes=minutes)
    return update_settings(db, org_id, manual_active_until=until.isoformat())


def _shop_tz(db, org_id: str, cfg: dict) -> ZoneInfo:
    if cfg.get("timezone"):
        return ZoneInfo(cfg["timezone"])
    rows = (
        db.table("organizations").select("state").eq("id", org_id).limit(1).execute()
    ).data or []
    state = rows[0].get("state") if rows else None
    return ZoneInfo(_STATE_TZ.get((state or "").upper(), "America/Denver"))


def _parse_hhmm(value: str) -> int:
    hour, minute = (value or "00:00").split(":")[:2]
    return int(hour) * 60 + int(minute)


def _in_business_hours(db, org_id: str, cfg: dict, now: datetime) -> bool:
    local = now.astimezone(_shop_tz(db, org_id, cfg))
    if local.isoweekday() not in (cfg.get("open_days") or [1, 2, 3, 4, 5]):
        return False
    minutes = local.hour * 60 + local.minute
    return (
        _parse_hhmm(cfg.get("open_time"))
        <= minutes
        < _parse_hhmm(cfg.get("close_time"))
    )


def ai_should_answer(db, org_id: str, *, now: datetime) -> dict:
    """The coverage decision. All three gates must agree: global kill
    switch, shop toggle, and a coverage mode (manual window or
    after-hours auto-mode)."""
    if not _global_enabled():
        return {"answer": False, "mode": None, "reason": "global-disabled"}
    cfg = get_settings(db, org_id)
    if not cfg.get("enabled"):
        return {"answer": False, "mode": None, "reason": "shop-disabled"}
    manual_until = cfg.get("manual_active_until")
    if manual_until:
        until = datetime.fromisoformat(manual_until)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if now < until:
            return {"answer": True, "mode": "manual", "reason": None}
    if cfg.get("after_hours_auto") and not _in_business_hours(db, org_id, cfg, now):
        return {"answer": True, "mode": "after_hours", "reason": None}
    return {"answer": False, "mode": None, "reason": "business-hours"}


# ---------------------------------------------------------------------------
# Call sessions + AI disclosure
# ---------------------------------------------------------------------------


def _get_session(db, org_id: str, session_id: str) -> dict:
    rows = (
        db.table("ai_call_sessions")
        .select("*")
        .eq("id", session_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise PhoneAutomationError(404, "call session not found")
    return rows[0]


def start_ai_call(
    db,
    *,
    org_id: str,
    call_sid: str,
    from_number: str,
    telephony,
    voice_agent,
    now: datetime,
) -> dict:
    """Open an AI-answered call. The FIRST utterance is always the AI
    disclosure; recording starts OFF (per-call toggle)."""
    decision = ai_should_answer(db, org_id, now=now)
    if not decision["answer"]:
        raise PhoneAutomationError(
            409, f"AI answering not active ({decision['reason']})"
        )

    existing = (
        db.table("ai_call_sessions")
        .select("*")
        .eq("twilio_call_sid", call_sid)
        .limit(1)
        .execute()
    ).data or []
    if existing:  # Twilio webhook retry — never disclose or toll twice.
        return existing[0]

    org_rows = (
        db.table("organizations").select("name").eq("id", org_id).limit(1).execute()
    ).data or []
    shop_name = org_rows[0].get("name") if org_rows else "the shop"

    created = (
        db.table("ai_call_sessions")
        .insert(
            {
                "org_id": org_id,
                "twilio_call_sid": call_sid,
                "caller_number": from_number,  # bronze only
                "caller_hash": _hash_number(from_number),
                "customer_id": _match_customer(db, org_id, from_number),
                "status": "in_progress",
                "ai_disclosure_given": False,
                "disclosure_at": None,
                "recording_enabled": False,
                "intake": None,
                "job_id": None,
                "appointment_id": None,
                "started_at": now.isoformat(),
                "ended_at": None,
            }
        )
        .execute()
    )
    session = (created.data or [{}])[0]

    # Disclosure first — before any other utterance, by construction.
    voice_agent.speak(
        session_id=session["id"],
        text=DISCLOSURE_TEMPLATE.format(shop=shop_name),
    )
    db.table("ai_call_sessions").update(
        {
            "ai_disclosure_given": True,
            "disclosure_at": now.isoformat(),
        }
    ).eq("id", session["id"]).execute()
    session["ai_disclosure_given"] = True
    session["disclosure_at"] = now.isoformat()

    try:
        from .toll_service import record_toll_event

        record_toll_event(
            db,
            org_id,
            "call.ai_handled",
            1,
            idempotency_key=f"tw284-ai-{call_sid}",
            source_type="ai_call_session",
            source_id=session["id"],
        )
    except Exception as exc:  # toll failure never breaks the call (TW-208)
        log.warning("AI-handled toll emit failed (non-fatal): %s", exc)
    return session


# ---------------------------------------------------------------------------
# Recording toggle (per-call; spoken announcement; audit log)
# ---------------------------------------------------------------------------


def set_recording(
    db,
    *,
    org_id: str,
    session_id: str,
    on: bool,
    actor: str,
    telephony,
    voice_agent,
    now: datetime,
) -> dict:
    """Per-call recording toggle. Switching ON speaks the announcement
    BEFORE recording starts; every state change is audit-logged.
    Re-setting the current state is a no-op (no double announcement)."""
    session = _get_session(db, org_id, session_id)
    if bool(session.get("recording_enabled")) == bool(on):
        return session

    if on:
        voice_agent.speak(session_id=session_id, text=RECORDING_ANNOUNCEMENT)
        telephony.start_recording(call_sid=session["twilio_call_sid"])
        announcement = RECORDING_ANNOUNCEMENT
        event = "recording_enabled"
    else:
        telephony.stop_recording(call_sid=session["twilio_call_sid"])
        announcement = None
        event = "recording_disabled"

    db.table("ai_call_sessions").update({"recording_enabled": bool(on)}).eq(
        "id", session_id
    ).execute()
    db.table("call_recording_events").insert(
        {
            "org_id": org_id,
            "session_id": session_id,
            "event": event,
            "announcement_text": announcement,
            "announced_at": now.isoformat() if on else None,
            "actor": actor,
            "created_at": now.isoformat(),
        }
    ).execute()
    session["recording_enabled"] = bool(on)
    return session


# ---------------------------------------------------------------------------
# Emergency contacts + escalation
# ---------------------------------------------------------------------------


def replace_emergency_contacts(db, org_id: str, contacts: list[dict]) -> list[dict]:
    """Owner sets the emergency numbers at signup; multiple supported,
    priority-ordered. Replaces the previous set."""
    db.table("emergency_contacts").delete().eq("org_id", org_id).execute()
    for c in contacts:
        db.table("emergency_contacts").insert(
            {
                "org_id": org_id,
                "phone_number": c["phone_number"],  # bronze only
                "phone_hash": _hash_number(c["phone_number"]),
                "label": c.get("label"),
                "priority": int(c.get("priority", 99)),
                "is_active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    return list_emergency_contacts(db, org_id)


def _active_contacts(db, org_id: str) -> list[dict]:
    rows = (
        db.table("emergency_contacts").select("*").eq("org_id", org_id).execute()
    ).data or []
    active = [r for r in rows if r.get("is_active", True)]
    return sorted(active, key=lambda r: r.get("priority", 99))


def list_emergency_contacts(db, org_id: str) -> list[dict]:
    """API-facing view: masked numbers only, priority order."""
    return [
        {
            "id": r.get("id"),
            "label": r.get("label"),
            "priority": r.get("priority"),
            "phone_masked": _mask_number(r.get("phone_number", "")),
        }
        for r in _active_contacts(db, org_id)
    ]


def start_escalation(
    db,
    *,
    org_id: str,
    session_id: str | None,
    message: str,
    telephony,
    ack_probe=None,
    max_rounds: int | None = None,
    now: datetime,
) -> dict:
    """Ring the emergency contacts in priority order until one
    acknowledges. Bounded: at most max_rounds passes over the contact
    list. Nobody acknowledges -> chain lands `exhausted` (the final
    fallback state) with a marker event — loud, queryable, never lost.

    ack_probe(contact) -> bool reports whether that contact acknowledged
    during their attempt window. In production the probe reads the ack
    state recorded by the Twilio DTMF/status webhook via
    acknowledge_escalation(); the default probe never acks, so a chain
    started without a live ack channel walks to exhaustion by design.
    """
    rounds = max_rounds if max_rounds is not None else _max_rounds()
    contacts = _active_contacts(db, org_id)
    created = (
        db.table("escalation_chains")
        .insert(
            {
                "org_id": org_id,
                "session_id": session_id,
                "reason": message,
                "status": "pending",
                "acknowledged_by": None,
                "created_at": now.isoformat(),
                "resolved_at": None,
            }
        )
        .execute()
    )
    chain = (created.data or [{}])[0]

    if not contacts:
        db.table("escalation_chains").update({"status": "exhausted"}).eq(
            "id", chain["id"]
        ).execute()
        db.table("escalation_events").insert(
            {
                "org_id": org_id,
                "chain_id": chain["id"],
                "contact_id": None,
                "attempt_no": 0,
                "round": 0,
                "channel": "system",
                "status": "exhausted",
                "detail": "no emergency contacts configured",
                "created_at": now.isoformat(),
            }
        ).execute()
        chain["status"] = "exhausted"
        log.warning(
            "escalation %s exhausted: no contacts (org %s)", chain["id"], org_id
        )
        return chain

    attempt_no = 0
    for round_no in range(1, rounds + 1):
        for contact in contacts:
            attempt_no += 1
            telephony.place_call(to=contact["phone_number"], say_text=message)
            telephony.send_sms(to=contact["phone_number"], body=message)
            ev = (
                db.table("escalation_events")
                .insert(
                    {
                        "org_id": org_id,
                        "chain_id": chain["id"],
                        "contact_id": contact["id"],
                        "attempt_no": attempt_no,
                        "round": round_no,
                        "channel": "call",
                        "status": "sent",
                        "detail": None,
                        "created_at": now.isoformat(),
                    }
                )
                .execute()
            )
            event_row = (ev.data or [{}])[0]
            if ack_probe is not None and ack_probe(contact):
                db.table("escalation_events").update({"status": "acknowledged"}).eq(
                    "id", event_row["id"]
                ).execute()
                event_row["status"] = "acknowledged"
                db.table("escalation_chains").update(
                    {
                        "status": "acknowledged",
                        "acknowledged_by": contact["id"],
                        "resolved_at": now.isoformat(),
                    }
                ).eq("id", chain["id"]).execute()
                chain["status"] = "acknowledged"
                chain["acknowledged_by"] = contact["id"]
                return chain

    db.table("escalation_chains").update({"status": "exhausted"}).eq(
        "id", chain["id"]
    ).execute()
    db.table("escalation_events").insert(
        {
            "org_id": org_id,
            "chain_id": chain["id"],
            "contact_id": None,
            "attempt_no": attempt_no,
            "round": rounds,
            "channel": "system",
            "status": "exhausted",
            "detail": (
                f"no acknowledgment after {rounds} round(s) across "
                f"{len(contacts)} contact(s)"
            ),
            "created_at": now.isoformat(),
        }
    ).execute()
    chain["status"] = "exhausted"
    log.warning(
        "escalation %s exhausted after %d attempts (org %s)",
        chain["id"],
        attempt_no,
        org_id,
    )
    return chain


def acknowledge_escalation(
    db, *, org_id: str, chain_id: str, contact_id: str, now: datetime
) -> dict:
    # Tenant boundary (Rosa M1): the chain is looked up by id + org, so
    # a chain from another org is indistinguishable from a missing one
    # (404, never 403 — no org existence leak), and the acknowledging
    # contact must belong to the same org.
    rows = (
        db.table("escalation_chains")
        .select("*")
        .eq("id", chain_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise PhoneAutomationError(404, "escalation chain not found")
    chain = rows[0]
    contact_rows = (
        db.table("emergency_contacts")
        .select("id")
        .eq("id", contact_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if not contact_rows:
        raise PhoneAutomationError(404, "emergency contact not found")
    db.table("escalation_chains").update(
        {
            "status": "acknowledged",
            "acknowledged_by": contact_id,
            "resolved_at": now.isoformat(),
        }
    ).eq("id", chain_id).eq("org_id", org_id).execute()
    db.table("escalation_events").insert(
        {
            "org_id": chain["org_id"],
            "chain_id": chain_id,
            "contact_id": contact_id,
            "attempt_no": None,
            "round": None,
            "channel": "ack",
            "status": "acknowledged",
            "detail": None,
            "created_at": now.isoformat(),
        }
    ).execute()
    chain["status"] = "acknowledged"
    chain["acknowledged_by"] = contact_id
    return chain


# ---------------------------------------------------------------------------
# Emergency intake -> docket booking
# ---------------------------------------------------------------------------


def _human_time(db, org_id: str, cfg: dict, when: datetime) -> str:
    local = when.astimezone(_shop_tz(db, org_id, cfg))
    return local.strftime("%A %b %d, %I:%M %p")


def record_emergency_intake(
    db,
    *,
    org_id: str,
    session_id: str,
    details: dict,
    telephony,
    ack_probe=None,
    now: datetime,
) -> dict:
    """Book an emergency from a live AI call: job + appointment on the
    docket, customer confirmation SMS, owner escalation started."""
    session = _get_session(db, org_id, session_id)
    cfg = get_settings(db, org_id)
    phone = details.get("phone") or session.get("caller_number")
    issue = details["issue"]

    customer_id = session.get("customer_id")
    if not customer_id and phone:
        customer_id = _match_customer(db, org_id, phone)
    if not customer_id:
        created = (
            db.table("customers")
            .insert(
                {
                    "org_id": org_id,
                    "name": details.get("caller_name") or "Phone caller",
                    "phone": phone,
                    "address": details.get("address"),
                }
            )
            .execute()
        )
        customer_id = (created.data or [{}])[0].get("id")

    scheduled_raw = details.get("requested_at") or now.isoformat()
    scheduled = datetime.fromisoformat(scheduled_raw)
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)

    job = (
        db.table("jobs")
        .insert(
            {
                "org_id": org_id,
                "customer_id": customer_id,
                "job_type": "emergency",
                "description": issue,
                "status": "scheduled",
                "scheduled_at": scheduled.isoformat(),
                "source": "phone_ai",
            }
        )
        .execute()
    )
    job_row = (job.data or [{}])[0]

    appt = (
        db.table("appointments")
        .insert(
            {
                "org_id": org_id,
                "job_id": job_row["id"],
                "customer_id": customer_id,
                "visit_type": "job_visit",
                "scheduled_at": scheduled.isoformat(),
                "notes": f"AI emergency intake: {issue}",
            }
        )
        .execute()
    )
    appt_row = (appt.data or [{}])[0]

    db.table("ai_call_sessions").update(
        {
            "intake": details,
            "job_id": job_row["id"],
            "appointment_id": appt_row["id"],
            "customer_id": customer_id,
        }
    ).eq("id", session_id).execute()

    when_human = _human_time(db, org_id, cfg, scheduled)
    if phone:
        try:
            telephony.send_sms(
                to=phone,
                body=(
                    f"You're booked: an emergency visit on {when_human} "
                    f"for: {issue}. We'll confirm shortly."
                ),
            )
        except Exception as exc:  # confirmation never breaks booking
            log.warning("intake confirmation SMS failed (non-fatal): %s", exc)

    org_rows = (
        db.table("organizations").select("name").eq("id", org_id).limit(1).execute()
    ).data or []
    shop_name = org_rows[0].get("name") if org_rows else "the shop"
    chain = start_escalation(
        db,
        org_id=org_id,
        session_id=session_id,
        message=(f"Emergency booked for {shop_name}: {issue}. Visit on {when_human}."),
        telephony=telephony,
        ack_probe=ack_probe,
        now=now,
    )
    return {
        "job_id": job_row["id"],
        "appointment_id": appt_row["id"],
        "customer_id": customer_id,
        "escalation_status": chain["status"],
    }


# ---------------------------------------------------------------------------
# Auto-reschedule: missed / double-booked -> next free slot + tell customer
# ---------------------------------------------------------------------------


def _slot_taken(
    db, org_id: str, when: datetime, technician_id: str | None, exclude_id: str
) -> bool:
    rows = (
        db.table("appointments").select("*").eq("org_id", org_id).execute()
    ).data or []
    for row in rows:
        if row["id"] == exclude_id:
            continue
        raw = row.get("scheduled_at")
        if not raw:
            continue
        other = datetime.fromisoformat(raw)
        if other.tzinfo is None:
            other = other.replace(tzinfo=timezone.utc)
        if other != when:
            continue
        if technician_id and row.get("technician_id") != technician_id:
            continue
        return True
    return False


def _next_free_slot(
    db,
    org_id: str,
    cfg: dict,
    start: datetime,
    technician_id: str | None,
    exclude_id: str,
) -> datetime:
    candidate = start
    for _ in range(14 * 48):  # bounded: 14 days of half-hour steps
        candidate += timedelta(minutes=30)
        if not _in_business_hours(db, org_id, cfg, candidate):
            continue
        if _slot_taken(db, org_id, candidate, technician_id, exclude_id):
            continue
        return candidate
    raise PhoneAutomationError(409, "no free slot within 14 days")


def auto_reschedule_appointment(
    db, *, org_id: str, appointment_id: str, reason: str, telephony, now: datetime
) -> dict:
    """Move a missed or double-booked appointment to the next free
    business-hours slot and tell the customer why it moved."""
    if reason not in _REASON_TEXT:
        raise PhoneAutomationError(400, f"unknown reschedule reason: {reason}")
    rows = (
        db.table("appointments")
        .select("*")
        .eq("id", appointment_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise PhoneAutomationError(404, "appointment not found")
    appt = rows[0]
    cfg = get_settings(db, org_id)

    old = datetime.fromisoformat(appt["scheduled_at"])
    if old.tzinfo is None:
        old = old.replace(tzinfo=timezone.utc)
    new_slot = _next_free_slot(
        db, org_id, cfg, old, appt.get("technician_id"), appointment_id
    )

    note = (appt.get("notes") or "").strip()
    note = (f"{note} Auto-rescheduled ({reason}) from {appt['scheduled_at']}.").strip()
    db.table("appointments").update(
        {
            "scheduled_at": new_slot.isoformat(),
            "notes": note,
        }
    ).eq("id", appointment_id).execute()

    notified = False
    cust_rows = (
        db.table("customers")
        .select("phone")
        .eq("id", appt["customer_id"])
        .limit(1)
        .execute()
    ).data or []
    phone = cust_rows[0].get("phone") if cust_rows else None
    if phone:
        org_rows = (
            db.table("organizations").select("name").eq("id", org_id).limit(1).execute()
        ).data or []
        shop_name = org_rows[0].get("name") if org_rows else "the shop"
        when_human = _human_time(db, org_id, cfg, new_slot)
        try:
            telephony.send_sms(
                to=phone,
                body=(
                    f"Update from {shop_name}: your appointment was "
                    f"moved to {when_human} because "
                    f"{_REASON_TEXT[reason]}. Sorry for the change — "
                    f"reply here or call us with questions."
                ),
            )
            notified = True
        except Exception as exc:  # the move stands even if SMS fails
            log.warning("reschedule SMS failed (non-fatal): %s", exc)

    return {
        "appointment_id": appointment_id,
        "old_scheduled_at": appt["scheduled_at"],
        "new_scheduled_at": new_slot.isoformat(),
        "reason": reason,
        "notified": notified,
    }
