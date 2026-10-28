"""TW-734 video consult — the consult a shop SELLS (Justynn,
2026-10-28 refinement).

A homeowner video-calls their HVAC/plumbing shop from the browser via
a tokenized link (no app install, no account) and talks to a real
tech: show the furnace, the leak, the panel. Primary use: DIYers who
want pro help WITHOUT the pro coming out — the tech guides the fix
live. If video can't fix it, the consult is the diagnostic and
converts to a booked visit with the problem already seen.

Money shape (shop-configured, per shop):
- pricing_mode "per_minute": customer pays per billable minute of
  tech time under the STARTED-MINUTE rule (any started minute bills
  in full; a live consult bills at least one minute).
- pricing_mode "flat": one flat rate for the consult, any duration.
- A session that never reaches a tech (answering machine) charges $0.
- Twistor's toll runs on the M1 ledger in BOTH modes, exactly one
  toll per finalized session (idempotency-keyed): 5 cents per
  billable minute in per-minute mode, 50 cents flat otherwise
  (event "consult.session", see TOLL_RATES).
- Card collection from the homeowner is NOT wired: the existing
  Stripe surface bills the SHOP's Twistor subscription, not a
  homeowner. The computed charge lands on the session as a
  RECEIVABLE (charge_status="receivable"); collection is go-live
  wiring, stated plainly — no payment integration is invented here.

AI layer (carried from TW-284/TW-727):
- The AI discloses itself in the first utterance, before the room is
  handed over; triage runs while the tech is paged.
- If no tech joins inside the shop's window, the consult lands in
  the answering machine (status message_left): the owner chain is
  rung (TW-284 escalation, visible exhausted state), and the
  customer can leave a video message — transcribed via the local
  faster-whisper path, summarized, attached to the session.
- Recording is a per-session toggle, starts OFF; switching ON speaks
  the recording announcement BEFORE recording starts; audit-logged.

Tenant boundary (Rosa M1, TW-284): every org-scoped lookup filters
by org_id, so cross-org access is a 404 — never a 403, no existence
leak. The customer's only credential is the join token; only its
SHA-256 hash is stored. PII doctrine (TW-552): the customer phone
lives only in the bronze column; API views mask it.

Nothing goes live on its own: the global kill switch
(settings.video_consult_enabled) defaults OFF, per-shop enable is
separate, and every provider fails closed until go-live wiring.
"""

import hashlib
import logging
import math
import secrets
from datetime import datetime, timedelta, timezone

from . import phone_automation as pa
from .call_service import _hash_number, _mask_number, _match_customer
from .voice_providers import ProviderNotConfigured

log = logging.getLogger(__name__)

DISCLOSURE_TEMPLATE = (
    "Hi, this is the automated AI assistant for {shop}. "
    "I'm an AI assistant, not a person. I'll bring a technician onto "
    "this video consult in just a moment. If nobody can join, you can "
    "leave a video message and we'll get back to you."
)

_DEFAULT_SETTINGS = {
    "enabled": False,
    "pricing_mode": "per_minute",
    "per_minute_rate_cents": 200,
    "flat_rate_cents": 4900,
    "tech_join_window_seconds": 120,
}

_PRICING_MODES = {"per_minute", "flat"}
_TOLL_PER_MINUTE_CENTS = 5  # Twistor toll per billable minute (per-minute mode)
_TOLL_FLAT_CENTS = 50  # == TOLL_RATES["consult.session"]; flat + answering toll

_TERMINAL = {"ended", "converted_to_visit"}


class VideoConsultError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# Config gates + settings
# ---------------------------------------------------------------------------


def _global_enabled() -> bool:
    from ..config import settings

    return bool(getattr(settings, "video_consult_enabled", False))


def get_consult_settings(db, org_id: str) -> dict:
    rows = (
        db.table("video_consult_settings")
        .select("*")
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if rows:
        return {**_DEFAULT_SETTINGS, **rows[0]}
    return {**_DEFAULT_SETTINGS, "org_id": org_id}


def update_consult_settings(db, org_id: str, **fields) -> dict:
    allowed = {}
    for key, value in fields.items():
        if key not in _DEFAULT_SETTINGS or value is None:
            continue
        if key == "pricing_mode" and value not in _PRICING_MODES:
            raise VideoConsultError(400, f"unknown pricing_mode: {value}")
        if key in ("per_minute_rate_cents", "flat_rate_cents") and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise VideoConsultError(400, f"{key} must be a non-negative integer")
        if key == "tech_join_window_seconds" and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 15 <= value <= 600
        ):
            raise VideoConsultError(
                400, "tech_join_window_seconds must be between 15 and 600"
            )
        allowed[key] = value
    existing = (
        db.table("video_consult_settings")
        .select("*")
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if existing:
        if allowed:
            db.table("video_consult_settings").update(allowed).eq(
                "org_id", org_id
            ).execute()
    else:
        db.table("video_consult_settings").insert(
            {"org_id": org_id, **_DEFAULT_SETTINGS, **allowed}
        ).execute()
    return get_consult_settings(db, org_id)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _get_session(db, org_id: str, session_id: str) -> dict:
    rows = (
        db.table("video_consult_sessions")
        .select("*")
        .eq("id", session_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise VideoConsultError(404, "consult session not found")
    return rows[0]


def get_session(db, *, org_id: str, session_id: str) -> dict:
    return _get_session(db, org_id, session_id)


def _session_by_token(db, token: str) -> dict:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    rows = (
        db.table("video_consult_sessions")
        .select("*")
        .eq("access_token_hash", token_hash)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise VideoConsultError(404, "consult session not found")
    return rows[0]


def _event(db, org_id: str, session_id: str, event: str, *, now, **fields) -> None:
    db.table("video_consult_events").insert(
        {
            "org_id": org_id,
            "session_id": session_id,
            "event": event,
            "announcement_text": fields.get("announcement_text"),
            "actor": fields.get("actor"),
            "detail": fields.get("detail"),
            "created_at": now.isoformat(),
        }
    ).execute()


def _shop_name(db, org_id: str) -> str:
    rows = (
        db.table("organizations").select("name").eq("id", org_id).limit(1).execute()
    ).data or []
    return rows[0].get("name") if rows else "the shop"


def public_view(row: dict) -> dict:
    """API-facing session view: no token hash, phone masked (TW-552)."""
    view = {
        k: v for k, v in row.items() if k not in ("access_token_hash", "customer_phone")
    }
    if row.get("customer_phone"):
        view["customer_phone_masked"] = _mask_number(row["customer_phone"])
    return view


# ---------------------------------------------------------------------------
# Create + AI triage (disclosure first)
# ---------------------------------------------------------------------------


def create_session(
    db,
    *,
    org_id: str,
    customer_name: str | None,
    customer_phone: str | None,
    issue: str | None,
    room_provider,
    voice_agent,
    now: datetime,
) -> dict:
    """Open a consult. The FIRST utterance is the AI disclosure —
    spoken before the room link is handed over — then triage runs
    while the shop's tech-join window counts down."""
    if not _global_enabled():
        raise VideoConsultError(409, "video consult not active (global-disabled)")
    cfg = get_consult_settings(db, org_id)
    if not cfg.get("enabled"):
        raise VideoConsultError(409, "video consult not active (shop-disabled)")

    token = secrets.token_urlsafe(24)
    created = (
        db.table("video_consult_sessions")
        .insert(
            {
                "org_id": org_id,
                "access_token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "customer_name": customer_name,
                "customer_phone": customer_phone,  # bronze only
                "customer_hash": _hash_number(customer_phone)
                if customer_phone
                else None,
                "customer_id": _match_customer(db, org_id, customer_phone)
                if customer_phone
                else None,
                "issue": issue,
                "status": "ringing",
                "room_name": None,
                "room_url": None,
                "ai_disclosure_given": False,
                "disclosure_at": None,
                "recording_enabled": False,
                "tech_user_id": None,
                "triage_started_at": None,
                "triage_deadline_at": None,
                "live_started_at": None,
                "ended_at": None,
                "escalation_chain_id": None,
                "job_id": None,
                "appointment_id": None,
                "billable_seconds": None,
                "billable_minutes": None,
                "charge_cents": None,
                "charge_status": "not_charged",
                "metered_at": None,
                "started_at": now.isoformat(),
            }
        )
        .execute()
    )
    session = (created.data or [{}])[0]

    # Disclosure first — before the room exists, by construction.
    voice_agent.speak(
        session_id=session["id"],
        text=DISCLOSURE_TEMPLATE.format(shop=_shop_name(db, org_id)),
    )
    room = room_provider.create_room(org_id=org_id, session_id=session["id"])
    deadline = now + timedelta(seconds=int(cfg["tech_join_window_seconds"]))
    updates = {
        "status": "ai_triaging",
        "room_name": room["room_name"],
        "room_url": room["room_url"],
        "ai_disclosure_given": True,
        "disclosure_at": now.isoformat(),
        "triage_started_at": now.isoformat(),
        "triage_deadline_at": deadline.isoformat(),
    }
    db.table("video_consult_sessions").update(updates).eq("id", session["id"]).execute()
    session.update(updates)
    _event(
        db,
        org_id,
        session["id"],
        "state_change",
        now=now,
        detail="ringing->ai_triaging",
    )
    return {**session, "join_token": token, "join_url": f"/consults/join/{token}"}


def customer_join(db, *, token: str) -> dict:
    """The homeowner's view through the link: room + status + shop.
    The token is the only credential; no PII leaves through it."""
    row = _session_by_token(db, token)
    return {
        "session_id": row["id"],
        "shop_name": _shop_name(db, row["org_id"]),
        "status": row["status"],
        "room_url": row.get("room_url"),
        "room_name": row.get("room_name"),
        "recording_enabled": bool(row.get("recording_enabled")),
        "ai_disclosure_given": bool(row.get("ai_disclosure_given")),
    }


# ---------------------------------------------------------------------------
# Tech join + live consult
# ---------------------------------------------------------------------------


def tech_join(db, *, org_id: str, session_id: str, tech_user_id: str, now) -> dict:
    """A tech from the shop joins: the consult goes live and the
    billable clock starts now (triage time is never billed)."""
    session = _get_session(db, org_id, session_id)
    if session["status"] != "ai_triaging":
        raise VideoConsultError(
            409, f"cannot join a consult in state {session['status']}"
        )
    updates = {
        "status": "live",
        "tech_user_id": tech_user_id,
        "live_started_at": now.isoformat(),
    }
    db.table("video_consult_sessions").update(updates).eq("id", session_id).execute()
    session.update(updates)
    _event(db, org_id, session_id, "state_change", now=now, detail="ai_triaging->live")
    return session


# ---------------------------------------------------------------------------
# Recording toggle (announcement BEFORE recording; audit log)
# ---------------------------------------------------------------------------


def set_recording(
    db,
    *,
    org_id: str,
    session_id: str,
    on: bool,
    actor: str,
    room_provider,
    voice_agent,
    now: datetime,
) -> dict:
    """Per-session recording toggle. Switching ON speaks the
    announcement BEFORE recording starts (TW-284 discipline);
    re-setting the current state is a no-op."""
    session = _get_session(db, org_id, session_id)
    if bool(session.get("recording_enabled")) == bool(on):
        return session
    if on:
        voice_agent.speak(session_id=session_id, text=pa.RECORDING_ANNOUNCEMENT)
        room_provider.start_recording(room_name=session["room_name"])
        announcement = pa.RECORDING_ANNOUNCEMENT
        event = "recording_enabled"
    else:
        room_provider.stop_recording(room_name=session["room_name"])
        announcement = None
        event = "recording_disabled"
    db.table("video_consult_sessions").update({"recording_enabled": bool(on)}).eq(
        "id", session_id
    ).execute()
    _event(
        db,
        org_id,
        session_id,
        event,
        now=now,
        announcement_text=announcement,
        actor=actor,
    )
    session["recording_enabled"] = bool(on)
    return session


# ---------------------------------------------------------------------------
# Metering (named rules; exactly one toll per finalized session)
# ---------------------------------------------------------------------------


def _finalize_metering(db, session: dict, *, end: datetime) -> dict:
    """Compute the customer charge + emit the single M1 toll.

    Idempotent: a session meters once (metered_at stamp), and the
    toll carries a per-session idempotency key as the backstop.
    Rules: billable time is tech time only (live_started_at -> end).
    Per-minute: STARTED-MINUTE rule, minimum one minute when live.
    Flat: the flat rate when live, any duration. Never live: $0.
    """
    if session.get("metered_at"):
        return session
    cfg = get_consult_settings(db, session["org_id"])
    went_live = session.get("live_started_at") is not None
    live_seconds = 0
    if went_live:
        started = datetime.fromisoformat(session["live_started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        live_seconds = max(0, int((end - started).total_seconds()))
    billable_minutes = max(1, math.ceil(live_seconds / 60)) if went_live else 0
    if not went_live:
        charge_cents = 0
        toll_cents = _TOLL_FLAT_CENTS  # the answering machine ran on the meter
        toll_quantity = 1
    elif cfg["pricing_mode"] == "per_minute":
        charge_cents = billable_minutes * int(cfg["per_minute_rate_cents"])
        toll_cents = _TOLL_PER_MINUTE_CENTS * billable_minutes
        toll_quantity = billable_minutes  # the per-minute metering record
    else:  # flat
        charge_cents = int(cfg["flat_rate_cents"])
        toll_cents = _TOLL_FLAT_CENTS
        toll_quantity = 1
    updates = {
        "billable_seconds": live_seconds,
        "billable_minutes": billable_minutes,
        "charge_cents": charge_cents,
        "charge_status": "receivable" if charge_cents > 0 else "not_charged",
        "metered_at": end.isoformat(),
    }
    db.table("video_consult_sessions").update(updates).eq("id", session["id"]).execute()
    session.update(updates)
    try:
        from .toll_service import record_toll_event

        record_toll_event(
            db,
            session["org_id"],
            "consult.session",
            toll_quantity,
            idempotency_key=f"tw734-consult-{session['id']}",
            source_type="video_consult_session",
            source_id=session["id"],
            amount_cents=toll_cents,
        )
    except Exception as exc:  # toll failure never breaks the consult (TW-208)
        log.warning("consult toll emit failed (non-fatal): %s", exc)
    return session


# ---------------------------------------------------------------------------
# Answering machine: window expiry -> escalation + video message
# ---------------------------------------------------------------------------


def expire_triage(
    db,
    *,
    org_id: str,
    session_id: str,
    telephony,
    ack_probe=None,
    now: datetime,
) -> dict:
    """The tech-join window lapsed with no tech: the consult lands
    in the answering machine (message_left), meters at $0, and the
    owner chain is rung via TW-284 escalation — a chain nobody
    acknowledges lands in the visible `exhausted` state."""
    session = _get_session(db, org_id, session_id)
    if session["status"] != "ai_triaging":
        raise VideoConsultError(
            409, f"cannot expire triage in state {session['status']}"
        )
    deadline = datetime.fromisoformat(session["triage_deadline_at"])
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if now < deadline:
        raise VideoConsultError(409, "tech-join window has not lapsed")
    updates = {"status": "message_left"}
    db.table("video_consult_sessions").update(updates).eq("id", session_id).execute()
    session.update(updates)
    _event(
        db,
        org_id,
        session_id,
        "state_change",
        now=now,
        detail="ai_triaging->message_left",
    )
    _finalize_metering(db, session, end=now)
    chain = pa.start_escalation(
        db,
        org_id=org_id,
        session_id=None,
        message=(
            f"Video consult at {_shop_name(db, org_id)}: no technician "
            f"joined. Customer issue: {session.get('issue') or 'not stated'}. "
            "They can leave a video message on the consult link."
        ),
        telephony=telephony,
        ack_probe=ack_probe,
        now=now,
    )
    db.table("video_consult_sessions").update({"escalation_chain_id": chain["id"]}).eq(
        "id", session_id
    ).execute()
    session["escalation_chain_id"] = chain["id"]
    return session


def _summarize(issue: str | None, transcript: str | None) -> str:
    """Extractive summary (no LLM is wired into this backend): the
    stated issue plus the caller's first sentence. The local Ollama
    think bridge is go-live wiring, same seam as transcription."""
    parts = [f"Issue: {issue or 'not stated'}."]
    if transcript:
        first = transcript.strip().split(". ")[0].rstrip(".")
        parts.append(f"Caller said: {first}.")
    else:
        parts.append("Transcript pending.")
    return " ".join(parts)


def leave_message(
    db, *, token: str, recording_ref: str, transcriber, now: datetime
) -> dict:
    """The answering-machine act: the customer records a video
    message on the link. Transcription runs through the local
    faster-whisper path; if that bridge is not wired yet, the
    message is stored as transcription-pending, never dropped."""
    session = _session_by_token(db, token)
    if session["status"] not in ("ai_triaging", "message_left"):
        raise VideoConsultError(
            409, f"cannot leave a message in state {session['status']}"
        )
    if session["status"] == "ai_triaging":
        updates = {"status": "message_left"}
        db.table("video_consult_sessions").update(updates).eq(
            "id", session["id"]
        ).execute()
        session.update(updates)
        _event(
            db,
            session["org_id"],
            session["id"],
            "state_change",
            now=now,
            detail="ai_triaging->message_left",
        )
        _finalize_metering(db, session, end=now)
    transcript = None
    transcription_status = "pending"
    try:
        transcript = transcriber.transcribe(recording_ref=recording_ref)
        transcription_status = "complete"
    except ProviderNotConfigured:
        log.info("transcriber not wired; message stored pending transcription")
    except Exception as exc:  # a bad recording never loses the message
        log.warning("transcription failed (message kept): %s", exc)
    created = (
        db.table("video_consult_messages")
        .insert(
            {
                "org_id": session["org_id"],
                "session_id": session["id"],
                "recording_ref": recording_ref,
                "transcript": transcript,
                "summary": _summarize(session.get("issue"), transcript),
                "transcription_status": transcription_status,
                "created_at": now.isoformat(),
            }
        )
        .execute()
    )
    return (created.data or [{}])[0]


# ---------------------------------------------------------------------------
# End + convert to a booked visit
# ---------------------------------------------------------------------------


def end_session(db, *, org_id: str, session_id: str, now: datetime) -> dict:
    session = _get_session(db, org_id, session_id)
    if session["status"] not in ("live", "message_left"):
        raise VideoConsultError(
            409, f"cannot end a consult in state {session['status']}"
        )
    updates = {"status": "ended", "ended_at": now.isoformat()}
    db.table("video_consult_sessions").update(updates).eq("id", session_id).execute()
    session.update(updates)
    _event(db, org_id, session_id, "state_change", now=now, detail="->ended")
    _finalize_metering(db, session, end=now)
    return session


def convert_to_visit(
    db,
    *,
    org_id: str,
    session_id: str,
    telephony,
    requested_at: str | None = None,
    now: datetime,
) -> dict:
    """Video couldn't fix it: the consult becomes the diagnostic and
    a real visit is booked with the problem already seen. A live
    consult meters up to now first — the diagnostic time was sold
    time in both pricing modes."""
    session = _get_session(db, org_id, session_id)
    if session["status"] not in ("live", "ended", "message_left"):
        raise VideoConsultError(
            409, f"cannot convert a consult in state {session['status']}"
        )
    if session["status"] == "live":
        _finalize_metering(db, session, end=now)

    phone = session.get("customer_phone")
    customer_id = session.get("customer_id")
    if not customer_id and phone:
        customer_id = _match_customer(db, org_id, phone)
    if not customer_id:
        created = (
            db.table("customers")
            .insert(
                {
                    "org_id": org_id,
                    "name": session.get("customer_name") or "Video consult caller",
                    "phone": phone,
                    "address": None,
                }
            )
            .execute()
        )
        customer_id = (created.data or [{}])[0].get("id")

    scheduled_raw = requested_at or now.isoformat()
    scheduled = datetime.fromisoformat(scheduled_raw)
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)

    description = f"Video consult follow-up: {session.get('issue') or 'not stated'}"
    job = (
        db.table("jobs")
        .insert(
            {
                "org_id": org_id,
                "customer_id": customer_id,
                "job_type": "service_visit",
                "description": description,
                "status": "scheduled",
                "scheduled_at": scheduled.isoformat(),
                "source": "video_consult",
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
                "notes": f"Booked from video consult {session['id']}: {description}",
            }
        )
        .execute()
    )
    appt_row = (appt.data or [{}])[0]

    updates = {
        "status": "converted_to_visit",
        "job_id": job_row["id"],
        "appointment_id": appt_row["id"],
        "customer_id": customer_id,
    }
    db.table("video_consult_sessions").update(updates).eq("id", session_id).execute()
    session.update(updates)
    _event(
        db,
        org_id,
        session_id,
        "state_change",
        now=now,
        detail="->converted_to_visit",
    )
    if phone:
        try:
            telephony.send_sms(
                to=phone,
                body=(
                    f"You're booked: a visit from {_shop_name(db, org_id)} "
                    f"for: {session.get('issue') or 'your issue'}. "
                    "We saw the problem on video, so we'll come prepared."
                ),
            )
        except Exception as exc:  # confirmation never breaks booking
            log.warning("convert confirmation SMS failed (non-fatal): %s", exc)
    return session
