"""Outbound sales agent — dialer, talk-track engine, outcome plumbing.

TW-884 (slice 1 of epic TW-883). The agent works the TW-882 local
outreach list under Dez's talk track, books workflow-review demos onto
Justynn's calendar, and writes a disposition + transcript for every
call. Slice 1 is TEST MODE ONLY: the global kill switch defaults OFF,
test mode defaults ON (allowlisted numbers only), and every provider
(telephony, voice, line-type, registry, calendar) fails closed until
go-live wiring lands. Go-live is gated on Angela's TW-885 memo items,
Justynn's number purchase, and the TW-704 deploy.

Legal posture is code, not comments: LEGAL_GATE and STATE_RULES below
encode TW-885 hard rules HR-01..HR-22 as config assertions (tested in
tests/test_outbound_calls.py). The wall that shapes the whole design:
since FCC 24-17 an AI voice is an "artificial voice" under the TCPA,
so COLD dials go only to VERIFIED BUSINESS LANDLINES (HR-06). Wireless
and residential numbers are agent-callable only with a prior express
written consent record in outbound_consents (HR-07), and every dial
re-checks line type (HR-05), the suppression ledger (HR-09), the
registry-scrub result (HR-11), the state gate (HR-16), the prospect-
local window (HR-13/HR-14), and the 3-per-24h house cap (HR-15).

The conversation engine is a deterministic state machine driven by a
responder port: tests inject scripted responders; the production
responder (the voice agent's live conversation bridge) is go-live
wiring, the same seam TW-284 left for its providers. Classification in
slice 1 is keyword-based and documented as such in the TW-884 evidence
doc; the script lines themselves are TW-882's, verbatim where the pack
quotes them.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import phone_automation as pa
from . import voice_providers as vp
from .calendar_providers import (
    CalendarConflict,
    CalendarNotConfigured,
    get_calendar_provider,
)
from .call_service import _hash_number, _mask_number
from .outbound_lookups import LineTypeNotConfigured

__all__ = [
    "CADENCE_DAYS",
    "LEGAL_GATE",
    "DEFAULT_PERSONA_NAME",
    "STATE_RULES",
    "CalendarConflict",
    "CalendarNotConfigured",
    "OutboundError",
    "daily_rollup",
    "due_prospects",
    "get_agent_settings",
    "has_active_consent",
    "hash_phone",
    "import_prospects",
    "is_suppressed",
    "mask_phone",
    "place_outbound_call",
    "purge_expired",
    "record_suppression",
    "revoke_agent",
    "run_dialer",
    "scrub_prospects",
    "upsert_agent_settings",
]


class OutboundError(Exception):
    """Outbound failure with an HTTP-ish status, pa-style."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.status = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# TW-885 legal gate (HR-01..HR-22 as config assertions)
# ---------------------------------------------------------------------------

LEGAL_GATE = {
    "disclosure_first_utterance": True,  # HR-01 (invariant, no override)
    "claims_human": "never",  # HR-02
    "cnam": "Twistor",  # HR-03
    "stir_shaken_attestation_required": "A",  # HR-03
    "number_pool_spoofing": "disabled",  # HR-04
    "line_type_check": "before_every_dial",  # HR-05
    "cold_dial_allowed_line_types": ["business_landline"],  # HR-06
    "wireless_requires_pewc": True,  # HR-07
    "revocation_any_channel": "instant_suppression",  # HR-08
    "suppression_check": "at_dial_time",  # HR-09
    "dnc_honor": "permanent",  # HR-10
    "dnc_record_retention_years": 5,  # HR-10
    "batch_registry_scrub": "required",  # HR-11
    "window_source": "prospect_local_time",  # HR-13
    "max_attempts_per_number_per_24h": 3,  # HR-15 (FL cap, house-wide)
    "recording_default": "off",  # HR-17
    "announcement_before_capture": True,  # HR-17
    "recording_unknown_state": "disabled",  # HR-18
    "transcript_storage": "bronze_only",  # HR-19
    # HR-20 (periods per TW-885 section 7, pending Justynn's approval)
    "retention_days": {
        "audio": 90,
        "transcripts": 395,  # 13 months
        "consents": 1825,
        "suppression": 1825,
        "scrub_records": 1825,
    },
}

#: HR-14/HR-16. dialing_enabled=False states never dial until the
#: TW-885 section 8 counsel items for that state are documented clear.
STATE_RULES = {
    "CO": {
        "window_start": "08:00",
        "window_end": "17:00",  # house window, stricter than federal
        "dialing_enabled": True,
        "holidays": set(),
        "recording_consent": "one_party_announce_anyway",
    },
    "TX": {
        "window_start": "09:00",
        "window_end": "21:00",
        "sunday_start": "12:00",
        "dialing_enabled": False,  # ch. 302 registration + SB 140
        "holidays": set(),
        "recording_consent": "one_party_announce_anyway",
    },
    "FL": {
        "window_start": "08:00",
        "window_end": "20:00",
        "dialing_enabled": False,  # FTSA + Telemarketing Act licensing
        "holidays": set(),
        "recording_consent": "all_party",
    },
    "MO": {
        "window_start": "08:00",
        "window_end": "21:00",
        "dialing_enabled": True,
        "holidays": set(),
        "recording_consent": "one_party_announce_anyway",
    },
    "KS": {
        "window_start": "08:00",
        "window_end": "21:00",
        "dialing_enabled": True,
        "holidays": set(),
        "recording_consent": "one_party_announce_anyway",
    },
}

STATE_DEFAULT_TZ = {
    "CO": "America/Denver",
    "MO": "America/Chicago",
    "KS": "America/Chicago",
    "TX": "America/Chicago",
    "FL": "America/New_York",
}

#: Denver-metro cities from the TW-882 source list are Colorado.
_CITY_STATE = {
    city: "CO"
    for city in (
        "Arvada",
        "Aurora",
        "Boulder",
        "Brighton",
        "Broomfield",
        "Castle Rock",
        "Centennial",
        "Commerce City",
        "Denver",
        "Edgewater",
        "Englewood",
        "Erie",
        "Federal Heights",
        "Golden",
        "Greenwood Village",
        "Highlands Ranch",
        "Idaho Springs",
        "Lafayette",
        "Lakewood",
        "Littleton",
        "Lone Tree",
        "Louisville",
        "Morrison",
        "Northglenn",
        "Parker",
        "Sheridan",
        "Superior",
        "Thornton",
        "Westminster",
        "Wheat Ridge",
    )
}

#: TW-882 CALENDAR-90 cadence: touches land on Day 0 / 3 / 7 / 12.
CADENCE_DAYS = (0, 3, 7, 12)

#: Persona default (TW-886, name approved by Justynn 2026-11-01).
#: The slot itself is config-driven: the live value is the agent
#: settings row's persona_name; this is only the default a fresh
#: settings row starts from.
DEFAULT_PERSONA_NAME = "Tori Calloway"

_DEFAULT_AGENT_SETTINGS = {
    "enabled": False,
    "revoked_at": None,
    "persona_name": DEFAULT_PERSONA_NAME,
    "timezone": "America/Denver",
    "window_start": "08:00",
    "window_end": "17:00",
    "lunch_start": "12:00",
    "lunch_end": "13:00",
    "call_days": [0, 1, 2, 3, 4],
    "daily_cap": 25,
    "demo_duration_min": 20,
    "demo_buffer_min": 10,
    "demo_window_start": "09:00",
    "demo_window_end": "16:00",
    "calendar_id": None,
}


# ---------------------------------------------------------------------------
# Talk track (TW-882 TALK-TRACK.md; persona name is the TW-886 slot)
# ---------------------------------------------------------------------------

OPENING = (
    "Hi, this is {name}, an automated assistant calling for Twistor. "
    "Quick heads up before anything else: I am an automated assistant, "
    "and this is a sales call. Am I catching the owner, or the person "
    "who looks after the phones and booking?"
)
GATEKEEPER_LINE = (
    "No problem at all. I help shops catch the calls they miss after "
    "hours. Is the owner around, or is there a better number or time "
    "to catch them?"
)
PROBLEM_LINE = (
    "Appreciate you. I will be quick. Shops your size tell us the "
    "phones ring out after close and on weekends, and those callers "
    "just dial the next shop. Nobody ever sees what walked away."
)
Q1_LINE = (
    "Quick questions so I do not waste your time. How many phones "
    "ring when a customer calls, and who usually answers?"
)
Q2_LINE = "And after 5pm or on weekends, who answers then?"
AUDIT1_LINE = (
    "Mind if I run a 30-second test with your own numbers? No pitch "
    "in it. In a typical week, how many calls come in after hours or "
    "on weekends, roughly?"
)
AUDIT2_LINE = "Of those, how many get a call back within the hour?"
WORKED_LINE = (
    "So that is about {leak} calls a week that ring out and never "
    "get a call back inside the hour. Even if only half of those were "
    "real jobs, that is the leak, and it repeats every week."
)
WORKED_LINE_UNKNOWN = (
    "Even a few of those a week is the leak, and it repeats every "
    "week. Half of them could be real jobs."
)
CLOSE_LINE = (
    "Here is what I can do. Shops are claiming founding pilot seats "
    "right now: 14 days free, then $299 a month for Starter or $499 "
    "for Pro if it earns its keep. I can book you a workflow review "
    "with Justynn, the founder, to map your phones before anything "
    "starts. Want me to grab you a slot?"
)
BOOKED_LINE = (
    "Done, you are booked. The confirmation names Twistor and me as "
    "its automated assistant, and it carries a human contact if you "
    "need to move it. Thanks for your time."
)
SEND_INFO_LINE = (
    "Happy to. The one-pager shows the math on missed calls, what "
    "the pilot includes, and the founding price, no fluff. What is "
    "the best email for it? And the review is free either way: it is "
    "a look at your phones, not a project. Want me to hold you a "
    "slot for it?"
)
BUSY_LINE = (
    "Totally fair, busy is the job. When is a better time to catch you for two minutes?"
)
EXIT_LINE = (
    "No pressure at all. If the phones ever start costing you work, "
    "we are easy to find. Thanks for your time."
)
DNC_LINE = "You got it. You will not hear from us again. Take care."
TRUTH_LINE = (
    "Fair question, and I will always answer it straight: I am an "
    "automated assistant, not a person. I work for Twistor, and I "
    "can keep going or get you a human."
)
HONEST_START_LINE = (
    "Straight answer: founding shops get onboarded first as the "
    "pilot opens, in the order they claimed their seats. I am not "
    "going to invent a date for you. The review is how you claim "
    "the seat."
)
PRICE_LINE = (
    "Fair question. After the free 14 days it is $299 a month for "
    "Starter, $499 for Pro. Most shops your size start on Starter. "
    "No contract theatre, cancel if it does not earn."
)
HAS_SYSTEM_LINE = (
    "Good, then you are ahead of most. We do not replace what books "
    "the job. The gap we close is the calls that come in when nobody "
    "can pick up. Different job entirely."
)
SERVICETITAN_LINE = (
    "Plenty of shops we talk to run ServiceTitan and keep it. We are "
    "not asking you to rip it out. The leak is the after-hours and "
    "overflow calls it never sees. That is the piece we close."
)
TRIED_LINE = (
    "Heard that a lot, and usually what they tried was a generic "
    "answering service reading a script. This sits on your actual "
    "booking flow and proves itself in the free 14 days or it is "
    "gone."
)
CALLBACK_LINE = "Thank you, I will try then. Appreciate your help."
VOICEMAIL_LINE = (
    "Hi, this is {name}, Twistor's automated assistant, calling for "
    "Twistor Holdings LLC. Quick call about missed calls costing "
    "shops work. I will try again in a few days, or reach us at "
    "{caller_id}."
)
RECORDING_OFF_LINE = "Absolutely, recording is off."

_STATE_PROMPTS = {
    "gatekeeper": GATEKEEPER_LINE,
    "q1": Q1_LINE,
    "q2": Q2_LINE,
    "audit1": AUDIT1_LINE,
    "audit2": AUDIT2_LINE,
    "close": CLOSE_LINE,
}

#: Keyword classification (slice 1; the production bridge classifies
#: with the voice agent — see the module docstring).
DNC_PHRASES = (
    "do not call",
    "don't call",
    "dont call",
    "stop calling",
    "take me off",
    "remove me from",
    "never call",
)
RECORDING_OBJECTION_PHRASES = (
    "do not record",
    "don't record",
    "dont record",
    "stop recording",
    "no recording",
    "turn off the recording",
)
HUMAN_QUESTION_PHRASES = (
    "are you a robot",
    "are you human",
    "are you a real person",
    "are you ai",
    "are you an ai",
    "you a robot",
    "is this a robot",
    "am i talking to a robot",
)
OWNER_SIGNALS = (
    "speaking",
    "this is he",
    "this is she",
    "that's me",
    "that is me",
    "i am the owner",
    "i'm the owner",
    "this is the owner",
    "you got him",
    "you got her",
)
CALLBACK_SIGNALS = (
    "call back",
    "not in",
    "not here",
    "not available",
    "try again",
    "call tomorrow",
    "call later",
    "he's out",
    "she's out",
    "is out",
)
START_SIGNALS = (
    "when can i start",
    "when can we start",
    "when do we start",
    "how soon",
    "start right away",
    "sign me up today",
)
BUSY_PHRASES = (
    "too busy",
    "no time",
    "in the middle",
    "busy right now",
    "can't talk",
    "cannot talk",
)
SEND_INFO_PHRASES = (
    "send me",
    "send it",
    "send some",
    "send info",
    "email me",
    "just send",
)
PRICE_PHRASES = ("how much", "price", "cost", "what does it run")
SERVICETITAN_PHRASES = ("servicetitan", "service titan")
HAS_SYSTEM_PHRASES = (
    "already have",
    "already use",
    "already covered",
    "we use",
    "we have a system",
    "answering service",
    "software for",
)
TRIED_PHRASES = ("tried", "did that before", "been there")
AFFIRMATIVE_PHRASES = (
    "yes",
    "yeah",
    "yep",
    "sure",
    "sounds good",
    "book it",
    "let's do it",
    "lets do it",
    "works for me",
    "sign me up",
    "grab",
)
NEGATIVE_PHRASES = (
    "not interested",
    "no thanks",
    "no thank you",
    "don't think so",
    "do not think so",
    "not for us",
    "i'll pass",
    "ill pass",
)


def _has(low: str, phrases) -> bool:
    return any(p in low for p in phrases)


def _is_negative(low: str) -> bool:
    if _has(low, NEGATIVE_PHRASES):
        return True
    return low.strip().rstrip(".!") in ("no", "nope", "nah")


def _is_affirmative(low: str) -> bool:
    if _is_negative(low):
        return False
    if re.search(r"\bok(ay)?\b", low):
        return True
    return _has(low, AFFIRMATIVE_PHRASES)


def _first_number(text: str):
    match = re.search(r"(\d+(?:\.\d+)?)", text or "")
    if not match:
        return None
    value = float(match.group(1))
    return int(value) if value.is_integer() else value


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def hash_phone(phone: str) -> str:
    return _hash_number(phone)


def mask_phone(phone: str) -> str:
    return _mask_number(phone)


def _cfg():
    from ..config import settings

    return settings


def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _hhmm(value: str) -> int:
    hour, minute = (value or "00:00").split(":")[:2]
    return int(hour) * 60 + int(minute)


def _rows(db, table, org_id):
    return db.table(table).select("*").eq("org_id", org_id).execute().data or []


def _update(db, table, row_id, **fields):
    db.table(table).update(fields).eq("id", row_id).execute()


def normalize_phone(raw: str):
    """Best-effort E.164 for US numbers; None when unparseable."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if str(raw).strip().startswith("+") and len(digits) >= 11:
        return "+" + digits
    return None


# ---------------------------------------------------------------------------
# Agent settings (per-org kill switch + persona slot + windows)
# ---------------------------------------------------------------------------


def get_agent_settings(db, org_id: str):
    """Merged settings row, or None when the org has no agent yet."""
    rows = _rows(db, "outbound_agent_settings", org_id)
    if not rows:
        return None
    return {**_DEFAULT_AGENT_SETTINGS, **rows[0]}


def upsert_agent_settings(db, org_id: str, **fields) -> dict:
    allowed = {k: v for k, v in fields.items() if k in _DEFAULT_AGENT_SETTINGS}
    rows = _rows(db, "outbound_agent_settings", org_id)
    if rows:
        if allowed:
            db.table("outbound_agent_settings").update(allowed).eq(
                "org_id", org_id
            ).execute()
    else:
        db.table("outbound_agent_settings").insert(
            {"org_id": org_id, **_DEFAULT_AGENT_SETTINGS, **allowed}
        ).execute()
    return get_agent_settings(db, org_id)


def revoke_agent(db, org_id: str, *, now: datetime) -> dict:
    """TW-766 revocation: instant, and the dialer re-reads it before
    every utterance, so in-flight work stops too."""
    return upsert_agent_settings(db, org_id, enabled=False, revoked_at=now.isoformat())


def _agent_alive(db, org_id: str) -> bool:
    agent = get_agent_settings(db, org_id)
    return bool(agent and agent.get("enabled") and not agent.get("revoked_at"))


def _call_alive(db, org_id: str) -> bool:
    """Per-utterance liveness, re-read fresh on EVERY check: the
    global kill switch AND the per-agent revocation row (TW-766).
    Either one flipping stops an in-flight call before its next
    utterance or side effect — the global brake included (TW-889:
    slice 1 read the global flag once at dial entry and an in-flight
    call never saw the flip)."""
    if not _cfg().outbound_calls_enabled:
        return False
    return _agent_alive(db, org_id)


# ---------------------------------------------------------------------------
# Suppression ledger + consent ledger (HR-07..HR-11)
# ---------------------------------------------------------------------------


def record_suppression(
    db,
    org_id: str,
    phone: str,
    *,
    source: str,
    reason: str | None = None,
    now: datetime | None = None,
):
    """One ledger, every input: DNC requests, consent revocations (any
    channel, HR-08), registry hits, web opt-outs. Permanent (HR-10)."""
    phone_hash = hash_phone(phone)
    for row in _rows(db, "outbound_suppression", org_id):
        if row.get("phone_hash") == phone_hash:
            return row
    created = (
        db.table("outbound_suppression")
        .insert(
            {
                "org_id": org_id,
                "phone": phone,  # bronze only
                "phone_hash": phone_hash,
                "phone_masked": mask_phone(phone),
                "source": source,
                "reason": reason,
                "created_at": (now or datetime.now(UTC)).isoformat(),
            }
        )
        .execute()
    )
    return (created.data or [{}])[0]


def is_suppressed(db, org_id: str, phone: str) -> bool:
    phone_hash = hash_phone(phone)
    return any(
        row.get("phone_hash") == phone_hash
        for row in _rows(db, "outbound_suppression", org_id)
    )


def has_active_consent(db, org_id: str, phone: str) -> bool:
    """HR-07: a wireless/residential number is agent-callable only
    with a prior express written consent record naming Twistor, the
    number, and AI/automated voice calls, not since revoked."""
    phone_hash = hash_phone(phone)
    for row in _rows(db, "outbound_consents", org_id):
        if row.get("phone_hash") != phone_hash:
            continue
        if row.get("revoked_at"):
            continue
        if not row.get("scope_ai_voice"):
            continue
        if row.get("seller_name") not in ("Twistor", "Twistor Holdings LLC"):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# List intake: import (TW-882 CSV shape) + registry scrub (HR-11)
# ---------------------------------------------------------------------------


def import_prospects(
    db, org_id: str, rows: list[dict], *, now: datetime | None = None
) -> dict:
    now = now or datetime.now(ZoneInfo("America/Denver"))
    existing = {r.get("phone_hash") for r in _rows(db, "outbound_prospects", org_id)}
    summary = {"imported": 0, "duplicates": 0, "skipped_suppressed": 0}
    invalid = 0
    rank = len(existing)
    for raw in rows:
        phone = normalize_phone(raw.get("phone") or "")
        if phone is None:
            invalid += 1
            continue
        phone_hash = hash_phone(phone)
        if phone_hash in existing:
            summary["duplicates"] += 1
            continue
        if is_suppressed(db, org_id, phone):
            summary["skipped_suppressed"] += 1
            continue
        city = raw.get("city")
        state = raw.get("state") or _CITY_STATE.get(city or "") or None
        state = state.upper() if state else None
        rank += 1
        db.table("outbound_prospects").insert(
            {
                "org_id": org_id,
                "company": raw.get("company") or raw.get("name"),
                "owner_name": raw.get("owner_name") or raw.get("owner"),
                "city": city,
                "state": state,
                "timezone": raw.get("timezone") or STATE_DEFAULT_TZ.get(state or ""),
                "phone": phone,  # bronze only
                "phone_hash": phone_hash,
                "phone_masked": mask_phone(phone),
                "website": raw.get("website"),
                "rank": raw.get("rank") or rank,
                "status": "new",
                "lane": "agent",
                "touch_count": 0,
                "cadence_start": now.date().isoformat(),
                "next_touch_at": None,
                "last_disposition": None,
                "scrub_status": "unscrubbed",
                "scrub_batch_id": None,
                "line_type": None,
                "no_record": False,
                "created_at": now.isoformat(),
            }
        ).execute()
        existing.add(phone_hash)
        summary["imported"] += 1
    if invalid:
        summary["invalid"] = invalid
    return summary


def scrub_prospects(
    db,
    org_id: str,
    registry,
    *,
    batch_id: str,
    registry_version: str,
    now: datetime | None = None,
) -> dict:
    """HR-11: scrub the unscrubbed against the registry; a hit is
    suppressed (registry_hit) and never reaches the agent lane. The
    scrub run itself is logged as proof of process."""
    now = now or datetime.now(UTC)
    checked = 0
    hits = 0
    for prospect in _rows(db, "outbound_prospects", org_id):
        if prospect.get("scrub_status") != "unscrubbed":
            continue
        checked += 1
        hit = bool(registry.is_registered(prospect["phone"]))
        _update(
            db,
            "outbound_prospects",
            prospect["id"],
            scrub_status="registry_hit" if hit else "clear",
            scrub_batch_id=batch_id,
        )
        if hit:
            hits += 1
            record_suppression(
                db,
                org_id,
                prospect["phone"],
                source="registry_hit",
                reason=f"registry scrub {registry_version}",
                now=now,
            )
    db.table("outbound_scrub_runs").insert(
        {
            "org_id": org_id,
            "batch_id": batch_id,
            "registry_version": registry_version,
            "numbers_checked": checked,
            "hits": hits,
            "created_at": now.isoformat(),
        }
    ).execute()
    return {"checked": checked, "hits": hits}


# ---------------------------------------------------------------------------
# Window gate (HR-13/HR-14): prospect-local, state table ∩ agent window
# ---------------------------------------------------------------------------


def _window_open(agent: dict, prospect: dict, now: datetime) -> bool:
    tz_name = prospect.get("timezone")
    state = (prospect.get("state") or "").upper()
    if not tz_name or state not in STATE_RULES:
        return False
    rules = STATE_RULES[state]
    local = now.astimezone(ZoneInfo(tz_name))
    if local.weekday() not in (agent.get("call_days") or [0, 1, 2, 3, 4]):
        return False
    if local.date().isoformat() in (rules.get("holidays") or set()):
        return False
    minutes = local.hour * 60 + local.minute
    start = max(
        _hhmm(agent.get("window_start") or "08:00"),
        _hhmm(rules["window_start"]),
    )
    # HR-14: a Sunday start in the state table (TX: noon, Mon-Sat
    # 9-9 / Sun 12-9) replaces the weekday start on Sundays. Latent
    # while TX is counsel-gated off, but loaded config must be
    # enforced config — a rule nobody reads is how gates rot (M2).
    if local.weekday() == 6 and rules.get("sunday_start"):
        start = max(start, _hhmm(rules["sunday_start"]))
    end = min(
        _hhmm(agent.get("window_end") or "17:00"),
        _hhmm(rules["window_end"]),
    )
    if not (start <= minutes < end):
        return False
    lunch_start, lunch_end = agent.get("lunch_start"), agent.get("lunch_end")
    if lunch_start and lunch_end:
        if _hhmm(lunch_start) <= minutes < _hhmm(lunch_end):
            return False
    return True


# ---------------------------------------------------------------------------
# Demo booking (calendar port; HR-22 confirmation content)
# ---------------------------------------------------------------------------


def _find_demo_slot(
    agent: dict,
    calendar,
    prospect: dict,
    answers: dict,
    now: datetime,
    require_alive=None,
):
    """First free demo slot inside the configured windows, stepping by
    duration + buffer so two demos can never sit back-to-back without
    the buffer, and never overlap (double-book prevention).

    `require_alive` (TW-888) is re-checked immediately before the
    create: a kill-switch flip that lands during the slot search must
    stop the booking side effect, not just the next utterance."""
    tz = ZoneInfo(agent.get("timezone") or "America/Denver")
    duration = timedelta(minutes=int(agent.get("demo_duration_min") or 20))
    step = duration + timedelta(minutes=int(agent.get("demo_buffer_min") or 0))
    win_start = _hhmm(agent.get("demo_window_start") or "09:00")
    win_end = _hhmm(agent.get("demo_window_end") or "16:00")
    caller_id = _cfg().outbound_caller_id or ""
    description = "\n".join(
        [
            "Booked by Twistor's automated assistant on an outbound call.",
            "To reschedule or cancel, call Twistor at "
            f"{caller_id} and ask for a human.",
            f"Prospect: {prospect.get('company')}"
            + (
                f" ({prospect.get('owner_name')})" if prospect.get("owner_name") else ""
            ),
            f"Phone (masked): {prospect.get('phone_masked')}",
            f"Q1 (phones / who answers): {answers.get('q1')}",
            f"Q2 (after hours / weekends): {answers.get('q2')}",
            "Self-audit: "
            f"{answers.get('after_hours_calls')} after-hours/weekend "
            f"calls, {answers.get('callbacks_within_hour')} called back "
            f"within the hour, leak {answers.get('leak_per_week')}/week.",
        ]
    )
    title = f"Twistor workflow review — {prospect.get('company')}"
    first_day = now.astimezone(tz).date()
    for offset in range(14):
        day = first_day + timedelta(days=offset)
        if day.weekday() > 4:
            continue
        cursor = datetime.combine(day, time(win_start // 60, win_start % 60), tzinfo=tz)
        limit = datetime.combine(day, time(win_end // 60, win_end % 60), tzinfo=tz)
        while cursor + duration <= limit:
            slot_end = cursor + duration
            if not calendar.list_events(cursor, slot_end):
                if require_alive is not None and not require_alive():
                    raise _Aborted()
                try:
                    return calendar.create_event(
                        title=title,
                        start=cursor,
                        end=slot_end,
                        description=description,
                    )
                except CalendarConflict:
                    pass
            cursor += step
    return None


# ---------------------------------------------------------------------------
# Conversation engine (TW-882 talk track as a state machine)
# ---------------------------------------------------------------------------


class _Aborted(Exception):
    """Kill switch flipped mid-call; stop before the next utterance."""


class _Conversation:
    def __init__(
        self,
        db,
        org_id,
        agent,
        prospect,
        responder,
        *,
        now,
        telephony,
        voice_agent,
        calendar,
        session_id,
        caller_id,
    ):
        self.db = db
        self.org_id = org_id
        self.agent = agent
        self.prospect = prospect
        self.responder = responder
        self.now = now
        self.telephony = telephony
        self.voice_agent = voice_agent
        self.calendar = calendar
        self.session_id = session_id
        self.caller_id = caller_id
        self.transcript: list[dict] = []
        self.answers: dict = {}
        self.state = "gatekeeper"
        self.owner_reached = False
        self.gatekeeper_seen = False
        self.recording_on = False
        self.demo_event_id = None
        self._booked_calendar = None
        self._fetches = 0

    # -- speech / hearing -------------------------------------------------

    def speak(self, text: str):
        if not _call_alive(self.db, self.org_id):
            raise _Aborted()
        self.transcript.append({"role": "agent", "text": text})
        if self.voice_agent is not None:
            try:
                self.voice_agent.speak(session_id=self.session_id, text=text)
            except vp.ProviderNotConfigured:
                pass  # transcript is the record; rendering is the bridge

    def fetch(self):
        self._fetches += 1
        if self._fetches > 24:  # bounded conversation, never a loop
            return None
        try:
            reply = self.responder(self.transcript)
        except StopIteration:
            return None
        if reply is None:
            return None
        self.transcript.append({"role": "prospect", "text": reply})
        return reply

    # -- booking ----------------------------------------------------------

    def book(self, *, review_only: bool) -> bool:
        # TW-888: liveness is re-checked BEFORE any booking side
        # effect. Slice 1 booked first and let speak() discover the
        # revocation afterwards, leaving a phantom calendar event
        # behind an "aborted" disposition.
        if not _call_alive(self.db, self.org_id):
            raise _Aborted()
        calendar = self.calendar
        if calendar is None:
            try:
                calendar = get_calendar_provider()
            except CalendarNotConfigured:
                calendar = None
        if calendar is None:
            self.answers["booking_failed"] = True
            return False
        event = _find_demo_slot(
            self.agent,
            calendar,
            self.prospect,
            self.answers,
            self.now,
            require_alive=lambda: _call_alive(self.db, self.org_id),
        )
        if event is None:
            self.answers["booking_failed"] = True
            return False
        self.demo_event_id = event.get("id")
        self._booked_calendar = calendar
        self.answers["review_only"] = review_only
        return True

    def _void_booking(self):
        """TW-888 compensation: an event created in the sliver before
        an abort becomes visible must not survive it — a revoked (or
        globally stopped) agent books nothing, and the audit trail
        never claims a demo the calendar still holds. Best-effort:
        a void failure never masks the abort itself."""
        event_id, self.demo_event_id = self.demo_event_id, None
        calendar, self._booked_calendar = self._booked_calendar, None
        if event_id is None or calendar is None:
            return
        delete = getattr(calendar, "delete_event", None)
        if delete is None:
            return
        try:
            delete(event_id)
        except Exception:
            return
        self.answers["booking_voided"] = True

    def close_booking(self, *, review_only: bool):
        try:
            if self.book(review_only=review_only):
                self.speak(BOOKED_LINE)
                return "demo_set" if not review_only else "audit_booked"
            # Self-healing: never crash a live call on a booking seam.
            self.speak(
                "I have you down for a callback from our side to lock the "
                "time. Thanks for your time."
            )
            return "callback"
        except _Aborted:
            self._void_booking()
            raise

    # -- global branches --------------------------------------------------

    def _handle_dnc(self, utterance: str):
        # HR-10: the suppression record is written BEFORE the call
        # closes; the pitch ends on this utterance.
        record_suppression(
            self.db,
            self.org_id,
            self.prospect["phone"],
            source="dnc_request",
            reason=utterance,
            now=self.now,
        )
        self.speak(DNC_LINE)
        return "dnc"

    def _handle_recording_objection(self):
        # HR-17: stop capture immediately, flag the number forever.
        # The toggle is guarded (Rosa M4): a toggle fault must not
        # crash the call or skip the permanent flag — the same
        # degrade-don't-detonate pattern as the abort path.
        if self.recording_on:
            try:
                pa.set_recording(
                    self.db,
                    org_id=self.org_id,
                    session_id=self.session_id,
                    on=False,
                    actor="prospect_objection",
                    telephony=self.telephony,
                    voice_agent=self.voice_agent,
                    now=self.now,
                )
            except Exception:
                pass
            self.recording_on = False
        _update(self.db, "outbound_prospects", self.prospect["id"], no_record=True)
        self.prospect["no_record"] = True
        return ("ask", [RECORDING_OFF_LINE, _STATE_PROMPTS[self.state]], self.state)

    # -- one prospect utterance -------------------------------------------

    def step(self, utterance: str):
        """Returns ("end", disposition) | ("handled", next_reply) |
        ("ask", lines, new_state)."""
        low = utterance.lower()
        if _has(low, DNC_PHRASES):
            return ("end", self._handle_dnc(utterance))
        if _has(low, RECORDING_OBJECTION_PHRASES):
            return self._handle_recording_objection()
        if _has(low, HUMAN_QUESTION_PHRASES):
            return ("ask", [TRUTH_LINE, _STATE_PROMPTS[self.state]], self.state)
        if _has(low, BUSY_PHRASES):
            self.speak(BUSY_LINE)
            reply = self.fetch()
            if reply is not None and _has(reply.lower(), DNC_PHRASES):
                return ("end", self._handle_dnc(reply))
            return ("end", "callback")
        if _has(low, SEND_INFO_PHRASES):
            self.speak(SEND_INFO_LINE)
            reply = self.fetch()
            if reply is not None:
                rlow = reply.lower()
                if _has(rlow, DNC_PHRASES):
                    return ("end", self._handle_dnc(reply))
                if _is_affirmative(rlow):
                    return ("end", self.close_booking(review_only=True))
                if _is_negative(rlow):
                    self.speak(EXIT_LINE)
                    return ("end", "not_interested")
            return ("end", "reached" if self.owner_reached else "gatekeeper")
        if _has(low, PRICE_PHRASES):
            return ("ask", [PRICE_LINE, CLOSE_LINE], "close")
        if _has(low, SERVICETITAN_PHRASES):
            if self.state == "gatekeeper":
                self.owner_reached = True
                return ("ask", [SERVICETITAN_LINE, PROBLEM_LINE, Q1_LINE], "q1")
            return ("ask", [SERVICETITAN_LINE, _STATE_PROMPTS[self.state]], self.state)
        if _has(low, HAS_SYSTEM_PHRASES):
            if self.state == "gatekeeper":
                self.owner_reached = True
                return ("ask", [HAS_SYSTEM_LINE, PROBLEM_LINE, Q1_LINE], "q1")
            return ("ask", [HAS_SYSTEM_LINE, _STATE_PROMPTS[self.state]], self.state)
        if _has(low, TRIED_PHRASES):
            return ("ask", [TRIED_LINE, CLOSE_LINE], "close")

        if self.state == "gatekeeper":
            if _has(low, OWNER_SIGNALS):
                self.owner_reached = True
                return ("ask", [PROBLEM_LINE, Q1_LINE], "q1")
            if _has(low, CALLBACK_SIGNALS):
                self.speak(CALLBACK_LINE)
                return ("end", "callback")
            self.gatekeeper_seen = True
            return ("ask", [GATEKEEPER_LINE], "gatekeeper")
        if self.state == "q1":
            self.answers["q1"] = utterance
            return ("ask", [Q2_LINE], "q2")
        if self.state == "q2":
            self.answers["q2"] = utterance
            return ("ask", [AUDIT1_LINE], "audit1")
        if self.state == "audit1":
            self.answers["after_hours_raw"] = utterance
            self.answers["after_hours_calls"] = _first_number(utterance)
            return ("ask", [AUDIT2_LINE], "audit2")
        if self.state == "audit2":
            self.answers["callbacks_raw"] = utterance
            self.answers["callbacks_within_hour"] = _first_number(utterance)
            n3 = self.answers.get("after_hours_calls")
            n4 = self.answers.get("callbacks_within_hour")
            leak = (
                max(0, n3 - n4)
                if isinstance(n3, (int, float)) and isinstance(n4, (int, float))
                else None
            )
            self.answers["leak_per_week"] = leak
            worked = (
                WORKED_LINE.format(leak=leak)
                if leak is not None
                else WORKED_LINE_UNKNOWN
            )
            return ("ask", [worked, CLOSE_LINE], "close")
        if self.state == "close":
            if _has(low, START_SIGNALS):
                return ("ask", [HONEST_START_LINE, CLOSE_LINE], "close")
            if _is_affirmative(low):
                return ("end", self.close_booking(review_only=False))
            if _is_negative(low):
                self.speak(EXIT_LINE)
                return ("end", "not_interested")
            return ("ask", [CLOSE_LINE], "close")
        return ("ask", [_STATE_PROMPTS[self.state]], self.state)

    # -- the call ----------------------------------------------------------

    def run(self, opening: str):
        # The opening rides the dial itself (place_call say_text) and is
        # transcript line one by construction: disclosure first (HR-01).
        self.transcript.append({"role": "agent", "text": opening})
        try:
            reply = self.responder(self.transcript)
        except StopIteration:
            reply = None
        if reply == "NO_ANSWER":
            return "no_answer", []
        if reply == "VOICEMAIL":
            self.speak(
                VOICEMAIL_LINE.format(
                    name=self.agent.get("persona_name") or DEFAULT_PERSONA_NAME,
                    caller_id=self.caller_id,
                )
            )
            return "voicemail", self.transcript
        if reply is None:
            return self._fallback(), self.transcript
        self.transcript.append({"role": "prospect", "text": reply})
        while reply is not None:
            tag, *rest = self.step(reply)
            if tag == "end":
                return rest[0], self.transcript
            if tag == "handled":
                reply = rest[0]
                continue
            lines, new_state = rest
            for line in lines:
                self.speak(line)
            self.state = new_state
            reply = self.fetch()
        return self._fallback(), self.transcript

    def _fallback(self):
        if self.owner_reached:
            return "reached"
        if self.gatekeeper_seen:
            return "gatekeeper"
        return "no_response"


# ---------------------------------------------------------------------------
# Dialer
# ---------------------------------------------------------------------------


def _get_prospect(db, org_id: str, prospect_id: str):
    for row in _rows(db, "outbound_prospects", org_id):
        if row.get("id") == prospect_id:
            return row
    return None


def _attempts(db, org_id: str, prospect_id: str | None = None):
    rows = _rows(db, "outbound_call_attempts", org_id)
    if prospect_id is not None:
        rows = [r for r in rows if r.get("prospect_id") == prospect_id]
    return rows


def _dialed_since(
    db, org_id: str, since: datetime, prospect_id: str | None = None
) -> int:
    count = 0
    for row in _attempts(db, org_id, prospect_id):
        if not row.get("dialed"):
            continue
        created = _parse_dt(row.get("created_at"))
        if created is not None and created >= since:
            count += 1
    return count


def _write_attempt(
    db,
    org_id: str,
    prospect: dict,
    *,
    now: datetime,
    session_id,
    disposition: str,
    dialed: bool,
    transcript=None,
    answers=None,
    demo_event_id=None,
    recording: bool = False,
    line_type=None,
    carrier=None,
):
    touch = int(prospect.get("touch_count") or 0)
    created = (
        db.table("outbound_call_attempts")
        .insert(
            {
                "org_id": org_id,
                "prospect_id": prospect["id"],
                "session_id": session_id,
                "attempt_no": touch + 1,
                "touch_day": CADENCE_DAYS[min(touch, len(CADENCE_DAYS) - 1)],
                "dialed": dialed,
                "disposition": disposition,
                "transcript": transcript,  # bronze only (HR-19)
                "audit_answers": answers or {},
                "demo_event_id": demo_event_id,
                "recording_enabled": recording,
                "line_type": line_type,
                "carrier": carrier,
                "created_at": now.isoformat(),
            }
        )
        .execute()
    )
    return (created.data or [{}])[0]


def _finalize_prospect(db, prospect: dict, disposition: str, new_touch: int):
    status_map = {
        "demo_set": "converted",
        "audit_booked": "engaged",
        "callback": "engaged",
        "dnc": "dnc",
        "not_interested": "resting",
    }
    if disposition in status_map:
        status = status_map[disposition]
    elif new_touch >= len(CADENCE_DAYS):
        status = "resting"
    else:
        status = "in_cadence"
    next_touch = None
    if status == "in_cadence" and new_touch < len(CADENCE_DAYS):
        tz = ZoneInfo(prospect.get("timezone") or "America/Denver")
        start = date.fromisoformat(prospect["cadence_start"]) + timedelta(
            days=CADENCE_DAYS[new_touch]
        )
        next_touch = datetime.combine(start, time(8, 0), tzinfo=tz).isoformat()
    _update(
        db,
        "outbound_prospects",
        prospect["id"],
        touch_count=new_touch,
        status=status,
        last_disposition=disposition,
        next_touch_at=next_touch,
    )


def place_outbound_call(
    db,
    org_id: str,
    prospect_id: str,
    responder,
    *,
    now: datetime | None = None,
    recording_enabled: bool = False,
    telephony=None,
    voice_agent=None,
    line_types=None,
    calendar=None,
) -> dict:
    """One outbound call, every gate in order. Refusals before the
    dial return a status dict; configuration failures raise
    OutboundError (fail closed). `responder` is the conversation port:
    callable(transcript) -> the prospect's next utterance, or the
    sentinels "NO_ANSWER" / "VOICEMAIL" as its first return."""
    now = now or datetime.now(UTC)
    cfg = _cfg()
    agent = get_agent_settings(db, org_id)
    if agent is None:
        raise OutboundError(409, "outbound agent is not configured")
    prospect = _get_prospect(db, org_id, prospect_id)
    if prospect is None:
        raise OutboundError(404, "prospect not found")

    # Kill switches (global, then per-agent TW-766 revocation).
    if not cfg.outbound_calls_enabled:
        raise OutboundError(409, "outbound calls are globally disabled")
    if not agent.get("enabled") or agent.get("revoked_at"):
        raise OutboundError(409, "outbound agent is disabled or revoked")

    # Caller ID (HR-03/HR-04): owned number, CNAM "Twistor", A-level
    # attestation, or the dialer does not dial at all.
    if (
        not cfg.outbound_caller_id
        or cfg.outbound_cnam != LEGAL_GATE["cnam"]
        or cfg.outbound_stir_shaken_attestation
        != LEGAL_GATE["stir_shaken_attestation_required"]
    ):
        raise OutboundError(
            503, "caller ID is not in its compliant configuration (HR-03)"
        )

    # Test mode allowlist, or the HR-12/HR-21 launch gates live.
    if cfg.outbound_test_mode:
        allowed = {
            n.strip() for n in (cfg.outbound_test_numbers or "").split(",") if n.strip()
        }
        if prospect["phone"] not in allowed:
            raise OutboundError(403, "test mode: number is not on the test allowlist")
    elif not (
        cfg.outbound_dnc_policy_on_file
        and cfg.outbound_privacy_section_live
        and cfg.outbound_web_optout_live
    ):
        raise OutboundError(
            503,
            "launch gates are closed: DNC policy / privacy section / "
            "web opt-out not all live (HR-12, HR-21)",
        )

    # Locale + state gate (HR-13/HR-16): unknown locale never dials;
    # TX/FL never dial until counsel clears them on TW-885.
    state = (prospect.get("state") or "").upper()
    if state not in STATE_RULES or not prospect.get("timezone"):
        return {"status": "blocked_unknown_locale"}
    if not STATE_RULES[state]["dialing_enabled"]:
        return {"status": "blocked_state_gate"}

    # Window (HR-13/HR-14), prospect-local.
    if not _window_open(agent, prospect, now):
        return {"status": "deferred_window"}

    # Suppression, checked at dial time (HR-09): registry-scrub result
    # first, then the ledger itself. Batch-load state is never trusted.
    if prospect.get("scrub_status") == "registry_hit":
        return {"status": "skipped_dnc"}
    if prospect.get("scrub_status") != "clear":
        return {"status": "blocked_unscrubbed"}
    if is_suppressed(db, org_id, prospect["phone"]):
        return {"status": "skipped_dnc"}

    # Caps: 3 attempts per number per rolling 24h (HR-15), then the
    # agent's daily cap.
    if (
        _dialed_since(db, org_id, now - timedelta(hours=24), prospect_id=prospect["id"])
        >= LEGAL_GATE["max_attempts_per_number_per_24h"]
    ):
        return {"status": "deferred_attempt_cap"}
    agent_tz = ZoneInfo(agent.get("timezone") or "America/Denver")
    day_start = datetime.combine(
        now.astimezone(agent_tz).date(), time(0, 0), tzinfo=agent_tz
    )
    if _dialed_since(db, org_id, day_start) >= int(agent.get("daily_cap") or 25):
        return {"status": "deferred_daily_cap"}

    # Line type before EVERY dial (HR-05), fail closed (HR-06/HR-07).
    if line_types is None:
        from .outbound_lookups import get_line_type_provider

        try:
            line_types = get_line_type_provider()
        except LineTypeNotConfigured as exc:
            raise OutboundError(503, str(exc)) from exc
    try:
        lookup = line_types.lookup(prospect["phone"])
    except Exception:  # a failed lookup is an unknown line: no dial
        lookup = {"line_type": "unknown", "carrier": None, "verified": False}
    line_type = lookup.get("line_type") or "unknown"
    carrier = lookup.get("carrier")
    line_verified = lookup.get("verified") is True
    _update(db, "outbound_prospects", prospect["id"], line_type=line_type)
    prospect["line_type"] = line_type
    # HR-06 is VERIFIED landlines (TW-890): a business_landline label
    # the carrier did not verify is a guess, so it takes the same
    # consent gate as wireless/residential (HR-07) instead of the
    # cold-dial path. Verified landlines are the ONLY cold dials.
    unverified_landline = (
        line_type in LEGAL_GATE["cold_dial_allowed_line_types"] and not line_verified
    )
    if line_type in ("wireless", "residential") or unverified_landline:
        if not has_active_consent(db, org_id, prospect["phone"]):
            _write_attempt(
                db,
                org_id,
                prospect,
                now=now,
                session_id=None,
                disposition="blocked_consent_required",
                dialed=False,
                line_type=line_type,
                carrier=carrier,
            )
            _update(db, "outbound_prospects", prospect["id"], lane="consent_capture")
            return {"status": "consent_required", "line_type": line_type}
    elif line_type not in LEGAL_GATE["cold_dial_allowed_line_types"]:
        _write_attempt(
            db,
            org_id,
            prospect,
            now=now,
            session_id=None,
            disposition="blocked_line_type",
            dialed=False,
            line_type=line_type,
            carrier=carrier,
        )
        _update(db, "outbound_prospects", prospect["id"], lane="human")
        return {"status": "blocked_line_type", "line_type": line_type}

    # Recording prerequisites resolve BEFORE the dial (Rosa M3): a
    # voice-provider config failure must never leave a dialed call
    # with no disposition row. Resolution is side-effect free; the
    # toggle itself still fires after session creation, because
    # announce-before-capture (HR-17) rides the live call.
    resolved_voice = voice_agent
    want_recording = (
        recording_enabled and not prospect.get("no_record") and state in STATE_RULES
    )
    if want_recording and resolved_voice is None:
        try:
            resolved_voice = vp.get_voice_agent()
        except vp.ProviderNotConfigured as exc:
            raise OutboundError(503, str(exc)) from exc

    # Dial. The disclosure opening is the dial's first utterance.
    if telephony is None:
        try:
            telephony = vp.get_telephony_provider()
        except vp.ProviderNotConfigured as exc:
            raise OutboundError(503, str(exc)) from exc
    persona = agent.get("persona_name") or DEFAULT_PERSONA_NAME
    opening = OPENING.format(name=persona)
    try:
        call_sid = telephony.place_call(to=prospect["phone"], say_text=opening)
    except vp.ProviderNotConfigured as exc:
        raise OutboundError(503, str(exc)) from exc

    created = (
        db.table("ai_call_sessions")
        .insert(
            {
                "org_id": org_id,
                "twilio_call_sid": call_sid,  # provider ref (TW-284 name)
                "caller_number": prospect["phone"],  # bronze only
                "caller_hash": hash_phone(prospect["phone"]),
                "customer_id": None,
                "status": "in_progress",
                "ai_disclosure_given": True,
                "disclosure_at": now.isoformat(),
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

    # Recording (HR-17/HR-18): off by default; the TW-284 toggle
    # announces before capture, in every state; a flagged number
    # (prior objection) is never recorded again. Provider resolution
    # already happened pre-dial (M3); a mid-setup toggle fault
    # degrades to an unrecorded call instead of detonating it, so
    # every dialed call still ends with a disposition.
    recording_on = False
    if want_recording:
        try:
            pa.set_recording(
                db,
                org_id=org_id,
                session_id=session["id"],
                on=True,
                actor="outbound_agent",
                telephony=telephony,
                voice_agent=resolved_voice,
                now=now,
            )
            recording_on = True
        except Exception:
            recording_on = False

    conversation = _Conversation(
        db,
        org_id,
        agent,
        prospect,
        responder,
        now=now,
        telephony=telephony,
        voice_agent=resolved_voice,
        calendar=calendar,
        session_id=session["id"],
        caller_id=cfg.outbound_caller_id,
    )
    conversation.recording_on = recording_on
    try:
        disposition, transcript = conversation.run(opening)
    except _Aborted:
        if recording_on:
            try:
                pa.set_recording(
                    db,
                    org_id=org_id,
                    session_id=session["id"],
                    on=False,
                    actor="outbound_revoked",
                    telephony=telephony,
                    voice_agent=resolved_voice,
                    now=now,
                )
            except Exception:
                pass
        attempt = _write_attempt(
            db,
            org_id,
            prospect,
            now=now,
            session_id=session["id"],
            disposition="aborted",
            dialed=True,
            transcript=conversation.transcript,
            answers=conversation.answers,
            recording=False,
            line_type=line_type,
            carrier=carrier,
        )
        _finalize_prospect(
            db,
            prospect,
            "aborted",
            int(prospect.get("touch_count") or 0) + 1,
        )
        return {
            "status": "aborted",
            "disposition": "aborted",
            "transcript": conversation.transcript,
            "attempt_id": attempt.get("id"),
            "session_id": session["id"],
        }

    _update(
        db,
        "ai_call_sessions",
        session["id"],
        status="completed",
        ended_at=now.isoformat(),
    )
    if disposition == "callback" and conversation.owner_reached:
        # Spec item 5 / Rosa M1: an owner's callback request is an
        # escalation, not a bare disposition — daily_rollup keys off
        # this flag to attach the transcript. (Slice 1 read the flag
        # in the rollup but nothing ever set it: dead code.)
        conversation.answers["owner_callback"] = True
    attempt = _write_attempt(
        db,
        org_id,
        prospect,
        now=now,
        session_id=session["id"],
        disposition=disposition,
        dialed=True,
        transcript=transcript,
        answers=conversation.answers,
        demo_event_id=conversation.demo_event_id,
        recording=conversation.recording_on,
        line_type=line_type,
        carrier=carrier,
    )
    _finalize_prospect(
        db,
        prospect,
        disposition,
        int(prospect.get("touch_count") or 0) + 1,
    )
    return {
        "status": "completed",
        "disposition": disposition,
        "transcript": transcript,
        "attempt_id": attempt.get("id"),
        "session_id": session["id"],
        "demo_event_id": conversation.demo_event_id,
    }


# ---------------------------------------------------------------------------
# Dial queue (cadence) + batch runner
# ---------------------------------------------------------------------------


def due_prospects(db, org_id: str, now: datetime) -> list[dict]:
    """Agent-lane prospects whose next touch is due: new rows dial on
    Day 0; cadence rows when next_touch_at has arrived (Day 3/7/12)."""
    due = []
    for prospect in _rows(db, "outbound_prospects", org_id):
        if prospect.get("lane") != "agent":
            continue
        status = prospect.get("status")
        if status == "new":
            due.append(prospect)
        elif status == "in_cadence":
            nxt = _parse_dt(prospect.get("next_touch_at"))
            if nxt is None or nxt <= now:
                due.append(prospect)
    due.sort(key=lambda p: (p.get("rank") is None, p.get("rank") or 0))
    return due


def run_dialer(
    db, org_id: str, responder_factory, *, now: datetime | None = None, **deps
) -> dict:
    """Dial the due queue up to the agent's remaining daily cap.
    responder_factory(prospect) -> responder for that call."""
    now = now or datetime.now(UTC)
    agent = get_agent_settings(db, org_id)
    if agent is None:
        raise OutboundError(409, "outbound agent is not configured")
    agent_tz = ZoneInfo(agent.get("timezone") or "America/Denver")
    day_start = datetime.combine(
        now.astimezone(agent_tz).date(), time(0, 0), tzinfo=agent_tz
    )
    remaining = max(
        0,
        int(agent.get("daily_cap") or 25) - _dialed_since(db, org_id, day_start),
    )
    results = []
    for prospect in due_prospects(db, org_id, now)[:remaining]:
        try:
            results.append(
                place_outbound_call(
                    db,
                    org_id,
                    prospect["id"],
                    responder_factory(prospect),
                    now=now,
                    **deps,
                )
            )
        except OutboundError as exc:
            results.append(
                {"status": "error", "detail": exc.detail, "prospect_id": prospect["id"]}
            )
        except Exception as exc:
            # Self-healing architecture rule (Rosa M4): per-item
            # fault isolation. One prospect's provider explosion is
            # a dead-letter record in the results, never a dead batch.
            results.append(
                {
                    "status": "error",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "prospect_id": prospect["id"],
                }
            )
    dialed = sum(1 for r in results if r.get("status") in ("completed", "aborted"))
    return {"dialed": dialed, "attempted": len(results), "results": results}


# ---------------------------------------------------------------------------
# Daily rollup + retention (HR-19/HR-20)
# ---------------------------------------------------------------------------


def daily_rollup(db, org_id: str, day: date) -> dict:
    """Pipeline rollup for one day (agent-local): conversations,
    audits run, demos set, DNCs, and escalations — a demo set or an
    owner callback request — each WITH its transcript attached."""
    agent = get_agent_settings(db, org_id) or {}
    tz = ZoneInfo(agent.get("timezone") or "America/Denver")

    def on_day(row):
        created = _parse_dt(row.get("created_at"))
        return created is not None and created.astimezone(tz).date() == day

    attempts = [a for a in _attempts(db, org_id) if a.get("dialed") and on_day(a)]
    by_disposition: dict[str, int] = {}
    for attempt in attempts:
        disp = attempt.get("disposition") or "unknown"
        by_disposition[disp] = by_disposition.get(disp, 0) + 1
    prospects = {p.get("id"): p for p in _rows(db, "outbound_prospects", org_id)}
    escalations = []
    for attempt in attempts:
        answers = attempt.get("audit_answers") or {}
        if attempt.get("disposition") == "demo_set" or (
            attempt.get("disposition") == "callback" and answers.get("owner_callback")
        ):
            prospect = prospects.get(attempt.get("prospect_id")) or {}
            escalations.append(
                {
                    "prospect_id": attempt.get("prospect_id"),
                    "company": prospect.get("company"),
                    "disposition": attempt.get("disposition"),
                    "transcript": attempt.get("transcript"),
                }
            )
    dncs = sum(
        1
        for row in _rows(db, "outbound_suppression", org_id)
        if row.get("source") in ("dnc_request", "revocation") and on_day(row)
    )
    audits = sum(
        1
        for a in attempts
        if "leak_per_week" in (a.get("audit_answers") or {})
        or a.get("disposition") in ("audit_booked", "demo_set")
    )
    return {
        "day": day.isoformat(),
        "conversations": len(attempts),
        "by_disposition": by_disposition,
        "audits": audits,
        "demos": by_disposition.get("demo_set", 0),
        "dncs": dncs,
        "escalations": escalations,
    }


def purge_expired(db, org_id: str, *, now: datetime) -> dict:
    """HR-20 retention execution, not just storage classes: expired
    transcripts are really deleted (set to null on the bronze row)
    and the purge is logged per asset class. Suppression and consent
    records are never purged here (5-year minimums; honor is
    permanent). Provider-side audio purge is go-live wiring."""
    cutoff = now - timedelta(days=LEGAL_GATE["retention_days"]["transcripts"])
    purged = 0
    for attempt in _attempts(db, org_id):
        if attempt.get("transcript") is None:
            continue
        created = _parse_dt(attempt.get("created_at"))
        if created is not None and created < cutoff:
            _update(db, "outbound_call_attempts", attempt["id"], transcript=None)
            purged += 1
    # HR-20 proof (Rosa N1): EVERY run writes its purge-log row,
    # including no-op runs — a missing row must mean "the job never
    # ran", never "nothing happened to expire".
    db.table("outbound_purge_log").insert(
        {
            "org_id": org_id,
            "asset_class": "transcripts",
            "purged_count": purged,
            "run_at": now.isoformat(),
        }
    ).execute()
    return {"transcripts": purged, "audio": 0}
