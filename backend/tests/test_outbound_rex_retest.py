"""TW-884 REX RETEST: adversarial suite against the outbound agent.

Rex Ryder, offensive retest of branch zeke/tw-884-outbound-calls.
Every test is an attack: make the dialer break its own law (TW-885
HR-01..HR-22). A passing test = KILL CONFIRMED (the control held).
A failing test = a FINDING, reproduced. Harness fakes are copied per
house pattern (one copy per test file); only the service and router
under test are imported.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.auth import AuthContext
from app.config import settings
from app.routers import outbound as outbound_router
from app.services import outbound_calls as oc

DENVER = ZoneInfo("America/Denver")
CHICAGO = ZoneInfo("America/Chicago")
ORG = "org-rex-a"
ORG2 = "org-rex-b"
BASE_NOW = datetime(2026, 11, 2, 10, 0, tzinfo=DENVER)  # Monday 10:00
LANDLINE = "+13035550101"
LANDLINE_2 = "+13035550102"
WIRELESS = "+13035550199"


# ---------------------------------------------------------------------------
# Fakes (house FakeDB/FakeQuery pattern, one copy per test file)
# ---------------------------------------------------------------------------


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters = []
        self._op = "select"
        self._payload = None
        self._order = None
        self._limit = None
        self._single = False
        self._maybe_single = False

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def _rows(self):
        return self.db.tables.setdefault(self.table, [])

    def _match(self, row):
        for op, col, val in self._filters:
            cur = row.get(col)
            if op == "eq" and cur != val:
                return False
            if op == "neq" and cur == val:
                return False
            if op == "in" and cur not in val:
                return False
            if op == "is" and val == "null" and cur is not None:
                return False
        return True

    def execute(self):
        rows = self._rows()
        if self._op == "insert":
            payloads = (
                self._payload if isinstance(self._payload, list) else [self._payload]
            )
            inserted = []
            for payload in payloads:
                row = dict(payload)
                if not row.get("id"):
                    self.db.counter += 1
                    row["id"] = f"{self.table}-{self.db.counter}"
                rows.append(row)
                inserted.append(row)
            return _Result(inserted)
        if self._op == "update":
            updated = []
            for row in rows:
                if self._match(row):
                    row.update(self._payload)
                    updated.append(row)
            return _Result(updated)
        matched = [dict(r) for r in rows if self._match(r)]
        if self._order:
            col, desc = self._order
            matched.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._limit:
            matched = matched[: self._limit]
        if self._single:
            return _Result(matched[:1], single=True)
        if self._maybe_single:
            return _Result(matched[:1], single=True, maybe=True)
        return _Result(matched)


class _Result:
    def __init__(self, data, single=False, maybe=False):
        self.data = data[0] if single and data else (None if single else data)
        if single and not data and not maybe:
            raise RuntimeError("no row for .single()")


class FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.counter = 0

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, *_a, **_k):
        raise AssertionError("rpc not expected in outbound tests")


class FakeTelephony:
    def __init__(self, order_log=None):
        self.calls = []
        self.recording_calls = []
        self.order_log = order_log if order_log is not None else []

    def place_call(self, *, to, say_text):
        self.calls.append({"to": to, "say_text": say_text})
        self.order_log.append(f"place_call:{to}")
        return f"CA-fake-{len(self.calls)}"

    def start_recording(self, *, call_sid):
        self.recording_calls.append(("start", call_sid))
        self.order_log.append("start_recording")

    def stop_recording(self, *, call_sid):
        self.recording_calls.append(("stop", call_sid))
        self.order_log.append("stop_recording")

    def send_sms(self, *, to, body):
        pass


class FakeVoiceAgent:
    def __init__(self, order_log=None):
        self.spoken = []
        self.order_log = order_log if order_log is not None else []

    def speak(self, *, session_id, text):
        self.spoken.append(text)
        self.order_log.append(f"speak:{text}")


class FakeLineTypes:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.lookups = []

    def lookup(self, phone_e164):
        self.lookups.append(phone_e164)
        return self.mapping.get(
            phone_e164, {"line_type": "unknown", "carrier": None, "verified": False}
        )


class RaisingLineTypes:
    """Carrier lookup that errors/times out mid-flight (HR-05/HR-06)."""

    def lookup(self, phone_e164):
        raise TimeoutError("carrier lookup timed out")


class FakeRegistry:
    def __init__(self, hits=()):
        self.hits = set(hits)

    def is_registered(self, phone_e164):
        return phone_e164 in self.hits


class FakeCalendar:
    def __init__(self):
        self.events = []

    def list_events(self, start, end):
        return [e for e in self.events if e["start"] < end and start < e["end"]]

    def create_event(self, *, title, start, end, description, location=None):
        for e in self.events:
            if e["start"] < end and start < e["end"]:
                raise oc.CalendarConflict("overlap")
        event = {
            "id": f"evt-{len(self.events) + 1}",
            "title": title,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
        }
        self.events.append(event)
        return event


class ScriptedResponder:
    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, history):
        if not self.replies:
            raise StopIteration
        item = self.replies.pop(0)
        if callable(item):
            return item(history)
        return item


# ---------------------------------------------------------------------------
# Fixtures / seeding
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(settings, "outbound_calls_enabled", True)
    monkeypatch.setattr(settings, "outbound_test_mode", True)
    monkeypatch.setattr(
        settings, "outbound_test_numbers", ",".join([LANDLINE, LANDLINE_2, WIRELESS])
    )
    monkeypatch.setattr(settings, "outbound_caller_id", "+13035550100")
    monkeypatch.setattr(settings, "outbound_cnam", "Twistor")
    monkeypatch.setattr(settings, "outbound_stir_shaken_attestation", "A")
    monkeypatch.setattr(settings, "outbound_dnc_policy_on_file", True)
    monkeypatch.setattr(settings, "outbound_privacy_section_live", True)
    monkeypatch.setattr(settings, "outbound_web_optout_live", True)
    yield


def landline_types(*numbers, verified=True):
    return FakeLineTypes(
        {
            n: {
                "line_type": "business_landline",
                "carrier": "TestTel",
                "verified": verified,
            }
            for n in numbers
        }
    )


def seed_agent(db, org=ORG, **over):
    row = {
        "org_id": org,
        "enabled": True,
        "revoked_at": None,
        "persona_name": "Tori Calloway",
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
    }
    row.update(over)
    return db.table("outbound_agent_settings").insert(row).execute().data[0]


def seed_prospect(db, org=ORG, **over):
    row = {
        "org_id": org,
        "company": "Hughes Mechanical",
        "owner_name": "Kevin Hughes",
        "city": "Aurora",
        "state": "CO",
        "timezone": "America/Denver",
        "phone": LANDLINE,
        "phone_hash": oc.hash_phone(LANDLINE),
        "phone_masked": oc.mask_phone(LANDLINE),
        "website": None,
        "rank": 1,
        "status": "new",
        "lane": "agent",
        "touch_count": 0,
        "cadence_start": BASE_NOW.date().isoformat(),
        "next_touch_at": None,
        "last_disposition": None,
        "scrub_status": "clear",
        "scrub_batch_id": "batch-1",
        "line_type": None,
        "no_record": False,
        "created_at": BASE_NOW.isoformat(),
    }
    row.update(over)
    if "phone" in over:
        row["phone_hash"] = oc.hash_phone(over["phone"])
        row["phone_masked"] = oc.mask_phone(over["phone"])
    return db.table("outbound_prospects").insert(row).execute().data[0]


def seed_attempt(db, prospect_id, when, org=ORG, dialed=True):
    return (
        db.table("outbound_call_attempts")
        .insert(
            {
                "org_id": org,
                "prospect_id": prospect_id,
                "session_id": None,
                "attempt_no": 1,
                "touch_day": 0,
                "dialed": dialed,
                "disposition": "reached",
                "transcript": None,
                "audit_answers": {},
                "demo_event_id": None,
                "recording_enabled": False,
                "line_type": "business_landline",
                "carrier": "TestTel",
                "created_at": when.isoformat(),
            }
        )
        .execute()
        .data[0]
    )


def seed_consent(db, phone, org=ORG, **over):
    row = {
        "org_id": org,
        "phone": phone,
        "phone_hash": oc.hash_phone(phone),
        "seller_name": "Twistor",
        "scope_ai_voice": True,
        "revoked_at": None,
        "created_at": BASE_NOW.isoformat(),
    }
    row.update(over)
    return db.table("outbound_consents").insert(row).execute().data[0]


def dial(db, prospect, responder, org=ORG, now=BASE_NOW, **kw):
    kw.setdefault("telephony", FakeTelephony())
    kw.setdefault("voice_agent", FakeVoiceAgent())
    kw.setdefault("line_types", landline_types(prospect["phone"]))
    kw.setdefault("calendar", FakeCalendar())
    return oc.place_outbound_call(db, org, prospect["id"], responder, now=now, **kw)


TO_CLOSE = [
    "This is Kevin, speaking.",
    "Three phones, front desk answers.",
    "Whoever is free grabs it, weekends it rings out.",
    "About 6 after-hours or weekend calls a week.",
    "Maybe 2 get a call back within the hour.",
]


# ---------------------------------------------------------------------------
# V1: REVOCATION — per-agent mid-call, global kill mid-call, pre-close
# ---------------------------------------------------------------------------


def test_revoke_mid_conversation_stops_utterances():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    voice = FakeVoiceAgent()
    cal = FakeCalendar()

    def revoke_then_answer(_history):
        oc.revoke_agent(db, ORG, now=BASE_NOW)
        return "Three phones, front desk answers."

    responder = ScriptedResponder(["This is Kevin, speaking.", revoke_then_answer])
    result = dial(db, p, responder, voice_agent=voice, calendar=cal)
    assert result["status"] == "aborted"
    assert oc.Q1_LINE in voice.spoken  # pitch reached Q1 before the flip
    assert oc.Q2_LINE not in voice.spoken  # nothing spoken after revocation
    assert cal.events == []


def test_revoke_at_close_creates_no_booking():
    """Revocation lands between the pitch and the close: the prospect
    says yes AFTER the flip. No calendar event may be created."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    voice = FakeVoiceAgent()
    cal = FakeCalendar()

    def revoke_then_yes(_history):
        oc.revoke_agent(db, ORG, now=BASE_NOW)
        return "Yeah, book it."

    responder = ScriptedResponder([*TO_CLOSE, revoke_then_yes])
    result = dial(db, p, responder, voice_agent=voice, calendar=cal)
    assert result["status"] == "aborted"
    assert oc.BOOKED_LINE not in voice.spoken
    assert cal.events == [], "booking side effect survived revocation"


def test_global_kill_switch_stops_in_flight_call():
    """The global kill switch is flipped mid-call (settings flag).
    The in-flight conversation must stop before its next utterance,
    not just refuse the NEXT dial."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    voice = FakeVoiceAgent()

    def kill_then_answer(_history):
        settings.outbound_calls_enabled = False
        return "Three phones, front desk answers."

    responder = ScriptedResponder(["This is Kevin, speaking.", kill_then_answer])
    dial(db, p, responder, voice_agent=voice)
    assert oc.Q2_LINE not in voice.spoken


# ---------------------------------------------------------------------------
# V2: LINE TYPE — fail closed on error, unknown, unverified (HR-05/06)
# ---------------------------------------------------------------------------


def test_line_type_lookup_timeout_refuses_dial():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    result = dial(
        db, p, ScriptedResponder(TO_CLOSE), telephony=tel, line_types=RaisingLineTypes()
    )
    assert tel.calls == []
    assert result["status"] == "blocked_line_type"
    attempts = db.tables["outbound_call_attempts"]
    assert attempts and attempts[-1]["dialed"] is False


def test_voip_unknown_line_type_refuses_dial():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    types = FakeLineTypes(
        {LANDLINE: {"line_type": "voip_unknown", "carrier": None, "verified": False}}
    )
    result = dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel, line_types=types)
    assert tel.calls == []
    assert result["status"] == "blocked_line_type"


def test_unverified_landline_classification_refuses_dial():
    """HR-06: cold dials go only to VERIFIED business landlines. A
    lookup that labels the line a landline but does NOT verify it
    must not be enough to dial."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    result = dial(
        db,
        p,
        ScriptedResponder(TO_CLOSE),
        telephony=tel,
        line_types=landline_types(LANDLINE, verified=False),
    )
    assert tel.calls == [], "unverified landline classification was dialed"
    assert result["status"] != "completed"


# ---------------------------------------------------------------------------
# V3: STATE GATES + WINDOW EDGES (HR-13/14/16)
# ---------------------------------------------------------------------------


def test_texas_prospect_undialable():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db, state="TX", city="Austin", timezone="America/Chicago")
    tel = FakeTelephony()
    result = dial(
        db,
        p,
        ScriptedResponder(TO_CLOSE),
        telephony=tel,
        now=datetime(2026, 11, 2, 10, 0, tzinfo=CHICAGO),
    )
    assert result["status"] == "blocked_state_gate"
    assert tel.calls == []


def test_florida_prospect_undialable():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db, state="FL", city="Miami", timezone="America/New_York")
    tel = FakeTelephony()
    result = dial(
        db,
        p,
        ScriptedResponder(TO_CLOSE),
        telephony=tel,
        now=datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    assert result["status"] == "blocked_state_gate"
    assert tel.calls == []


def test_unknown_state_and_missing_timezone_refuse():
    db = FakeDB()
    seed_agent(db)
    tel = FakeTelephony()
    wy = seed_prospect(db, state="WY", city="Cheyenne", timezone="America/Denver")
    result = dial(db, wy, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert result["status"] == "blocked_unknown_locale"
    notz = seed_prospect(db, phone=LANDLINE_2, timezone=None)
    result = dial(db, notz, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert result["status"] == "blocked_unknown_locale"
    assert tel.calls == []


@pytest.mark.parametrize(
    "hour,minute,should_dial",
    [
        (7, 59, False),  # one minute before the CO house window
        (8, 0, True),  # window opens
        (12, 30, False),  # lunch skip
        (16, 59, True),  # last dialable minute
        (17, 0, False),  # window closed (end-exclusive)
    ],
)
def test_co_window_edges(hour, minute, should_dial):
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    now = datetime(2026, 11, 2, hour, minute, tzinfo=DENVER)
    result = dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel, now=now)
    assert (result["status"] == "completed") is should_dial
    assert (len(tel.calls) == 1) is should_dial


def test_saturday_and_dst_monday_windows():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=DENVER)
    result = dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel, now=saturday)
    assert result["status"] == "deferred_window"
    # Monday after spring-forward: local-time math must still hold.
    dst_monday = datetime(2027, 3, 15, 10, 0, tzinfo=DENVER)
    result = dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel, now=dst_monday)
    assert result["status"] == "completed"


def test_prospect_local_time_governs_not_agent_time():
    """MO prospect: 17:30 prospect-local is outside the agent window
    intersection even though it is 16:30 back in Denver."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db, state="MO", city="Kansas City", timezone="America/Chicago")
    tel = FakeTelephony()
    inside = datetime(2026, 11, 2, 16, 30, tzinfo=CHICAGO)
    result = dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel, now=inside)
    assert result["status"] == "completed"
    db2 = FakeDB()
    seed_agent(db2)
    p2 = seed_prospect(db2, state="MO", city="Kansas City", timezone="America/Chicago")
    tel2 = FakeTelephony()
    outside = datetime(2026, 11, 2, 17, 30, tzinfo=CHICAGO)
    result = dial(db2, p2, ScriptedResponder(TO_CLOSE), telephony=tel2, now=outside)
    assert result["status"] == "deferred_window"
    assert tel2.calls == []


# ---------------------------------------------------------------------------
# V4: SUPPRESSION — dial-time check, mid-call DNC, second dial (HR-08..11)
# ---------------------------------------------------------------------------


def test_suppression_after_import_blocks_at_dial_time():
    db = FakeDB()
    seed_agent(db)
    summary = oc.import_prospects(
        db, ORG, [{"company": "A Shop", "city": "Denver", "phone": "303-555-0101"}]
    )
    assert summary["imported"] == 1
    prospect = db.tables["outbound_prospects"][0]
    # Unscrubbed rows never dial (HR-11).
    tel = FakeTelephony()
    result = dial(db, prospect, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert result["status"] == "blocked_unscrubbed"
    oc.scrub_prospects(db, ORG, FakeRegistry(), batch_id="b1", registry_version="v1")
    # Suppression lands AFTER batch load + scrub: dial time must see it.
    oc.record_suppression(db, ORG, LANDLINE, source="web_optout")
    result = dial(db, prospect, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert result["status"] == "skipped_dnc"
    assert tel.calls == []


def test_registry_hit_suppresses_and_blocks():
    db = FakeDB()
    seed_agent(db)
    oc.import_prospects(
        db, ORG, [{"company": "A Shop", "city": "Denver", "phone": "303-555-0101"}]
    )
    prospect = db.tables["outbound_prospects"][0]
    oc.scrub_prospects(
        db, ORG, FakeRegistry(hits={LANDLINE}), batch_id="b1", registry_version="v1"
    )
    assert oc.is_suppressed(db, ORG, LANDLINE)
    tel = FakeTelephony()
    result = dial(db, prospect, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert result["status"] == "skipped_dnc"
    assert tel.calls == []


def test_mid_call_dnc_writes_suppression_and_second_dial_refuses():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    responder = ScriptedResponder(["This is Kevin, speaking.", "Do not call me again."])
    result = dial(db, p, responder, telephony=tel)
    assert result["disposition"] == "dnc"
    # Suppression record exists (written before the call closed) and
    # the DNC line is the last thing the agent said.
    assert oc.is_suppressed(db, ORG, LANDLINE)
    agent_lines = [t["text"] for t in result["transcript"] if t["role"] == "agent"]
    assert agent_lines[-1] == oc.DNC_LINE
    # Second dial: refused before any telephony.
    result2 = dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert result2["status"] == "skipped_dnc"
    assert len(tel.calls) == 1


# ---------------------------------------------------------------------------
# V5: CAPS — 3/24h, rolling window, daily cap, re-import, cadence (HR-15)
# ---------------------------------------------------------------------------


def test_attempt_cap_blocks_fourth_dial_within_24h():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    for hours_ago in (1, 2, 3):
        seed_attempt(db, p["id"], BASE_NOW - timedelta(hours=hours_ago))
    tel = FakeTelephony()
    result = dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert result["status"] == "deferred_attempt_cap"
    assert tel.calls == []


def test_attempt_cap_rolls_off_after_24h():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    for hours_ago in (25, 26, 27):
        seed_attempt(db, p["id"], BASE_NOW - timedelta(hours=hours_ago))
    result = dial(db, p, ScriptedResponder(TO_CLOSE))
    assert result["status"] == "completed"


def test_daily_cap_blocks_across_prospects():
    db = FakeDB()
    seed_agent(db, daily_cap=2)
    p1 = seed_prospect(db)
    p2 = seed_prospect(db, phone=LANDLINE_2)
    p3 = seed_prospect(db, phone=WIRELESS)
    seed_attempt(db, p1["id"], BASE_NOW)
    seed_attempt(db, p2["id"], BASE_NOW)
    tel = FakeTelephony()
    types = FakeLineTypes(
        {WIRELESS: {"line_type": "business_landline", "carrier": "T", "verified": True}}
    )
    result = dial(db, p3, ScriptedResponder(TO_CLOSE), telephony=tel, line_types=types)
    assert result["status"] == "deferred_daily_cap"
    assert tel.calls == []


def test_reimport_does_not_reset_caps_or_duplicate():
    db = FakeDB()
    seed_agent(db)
    rows = [{"company": "A Shop", "city": "Denver", "phone": "303-555-0101"}]
    oc.import_prospects(db, ORG, rows)
    again = oc.import_prospects(db, ORG, rows)
    assert again["duplicates"] == 1 and again["imported"] == 0
    assert len(db.tables["outbound_prospects"]) == 1
    prospect = db.tables["outbound_prospects"][0]
    oc.scrub_prospects(db, ORG, FakeRegistry(), batch_id="b1", registry_version="v1")
    for hours_ago in (1, 2, 3):
        seed_attempt(db, prospect["id"], BASE_NOW - timedelta(hours=hours_ago))
    tel = FakeTelephony()
    result = dial(db, prospect, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert result["status"] == "deferred_attempt_cap"
    assert tel.calls == []


def test_cadence_not_due_after_first_touch_via_dialer_queue():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    first = dial(db, p, ScriptedResponder(["This is Kevin, speaking."]))
    assert first["status"] == "completed"
    tel = FakeTelephony()
    out = oc.run_dialer(
        db,
        ORG,
        lambda _prospect: ScriptedResponder(TO_CLOSE),
        now=BASE_NOW,
        telephony=tel,
        voice_agent=FakeVoiceAgent(),
        line_types=landline_types(LANDLINE),
        calendar=FakeCalendar(),
    )
    assert out["dialed"] == 0
    assert tel.calls == []


# ---------------------------------------------------------------------------
# V6: RECORDING — announce before capture, objection, toggle off (HR-17)
# ---------------------------------------------------------------------------


def test_recording_announcement_precedes_capture():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    order: list[str] = []
    tel = FakeTelephony(order_log=order)
    voice = FakeVoiceAgent(order_log=order)
    dial(
        db,
        p,
        ScriptedResponder(TO_CLOSE),
        telephony=tel,
        voice_agent=voice,
        recording_enabled=True,
    )
    announcement = "speak:This call is now being recorded."
    assert announcement in order
    assert order.index(announcement) < order.index("start_recording")


def test_recording_objection_stops_capture_and_flags_forever():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    voice = FakeVoiceAgent()
    responder = ScriptedResponder(
        ["This is Kevin, speaking.", "Do not record this call."]
    )
    result = dial(
        db, p, responder, telephony=tel, voice_agent=voice, recording_enabled=True
    )
    starts = [c for c in tel.recording_calls if c[0] == "start"]
    stops = [c for c in tel.recording_calls if c[0] == "stop"]
    assert len(starts) == 1 and len(stops) == 1  # capture stopped mid-call
    attempt = db.tables["outbound_call_attempts"][-1]
    assert attempt["recording_enabled"] is False
    stored = [r for r in db.tables["outbound_prospects"] if r["id"] == p["id"]][0]
    assert stored["no_record"] is True
    # A later call to the flagged number never starts recording again.
    dial(
        db,
        stored,
        ScriptedResponder(TO_CLOSE),
        telephony=tel,
        voice_agent=voice,
        recording_enabled=True,
    )
    starts = [c for c in tel.recording_calls if c[0] == "start"]
    assert len(starts) == 1, "flagged number was recorded again"
    assert result["disposition"] is not None


def test_recording_toggle_off_writes_no_capture():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel, recording_enabled=False)
    assert tel.recording_calls == []
    assert db.tables.get("call_recording_events", []) == []
    session = db.tables["ai_call_sessions"][-1]
    assert session["recording_enabled"] is False


# ---------------------------------------------------------------------------
# V7: CONSENT PATH — scope, seller, revocation, org (HR-07)
# ---------------------------------------------------------------------------


def _wireless_prospect(db, org=ORG):
    return seed_prospect(db, org=org, phone=WIRELESS)


def _wireless_types():
    return FakeLineTypes(
        {WIRELESS: {"line_type": "wireless", "carrier": "TestMobile", "verified": True}}
    )


def test_wireless_valid_consent_control_dials():
    db = FakeDB()
    seed_agent(db)
    p = _wireless_prospect(db)
    seed_consent(db, WIRELESS)
    result = dial(db, p, ScriptedResponder(TO_CLOSE), line_types=_wireless_types())
    assert result["status"] == "completed"


def test_wireless_consent_wrong_scope_refuses():
    db = FakeDB()
    seed_agent(db)
    p = _wireless_prospect(db)
    seed_consent(db, WIRELESS, scope_ai_voice=False)  # PEWC, wrong scope
    tel = FakeTelephony()
    result = dial(
        db, p, ScriptedResponder(TO_CLOSE), telephony=tel, line_types=_wireless_types()
    )
    assert result["status"] == "consent_required"
    assert tel.calls == []


def test_wireless_consent_other_seller_refuses():
    db = FakeDB()
    seed_agent(db)
    p = _wireless_prospect(db)
    seed_consent(db, WIRELESS, seller_name="Some Other Company LLC")
    tel = FakeTelephony()
    result = dial(
        db, p, ScriptedResponder(TO_CLOSE), telephony=tel, line_types=_wireless_types()
    )
    assert result["status"] == "consent_required"
    assert tel.calls == []


def test_wireless_consent_revoked_refuses():
    db = FakeDB()
    seed_agent(db)
    p = _wireless_prospect(db)
    seed_consent(db, WIRELESS, revoked_at=BASE_NOW.isoformat())
    tel = FakeTelephony()
    result = dial(
        db, p, ScriptedResponder(TO_CLOSE), telephony=tel, line_types=_wireless_types()
    )
    assert result["status"] == "consent_required"
    assert tel.calls == []


def test_wireless_consent_other_org_does_not_unlock():
    db = FakeDB()
    seed_agent(db)
    seed_agent(db, org=ORG2)
    p = _wireless_prospect(db)
    seed_consent(db, WIRELESS, org=ORG2)  # consent belongs to another org
    tel = FakeTelephony()
    result = dial(
        db, p, ScriptedResponder(TO_CLOSE), telephony=tel, line_types=_wireless_types()
    )
    assert result["status"] == "consent_required"
    assert tel.calls == []


# ---------------------------------------------------------------------------
# V8: ORG ISOLATION — service + router surfaces
# ---------------------------------------------------------------------------


def test_service_refuses_cross_org_prospect():
    db = FakeDB()
    seed_agent(db)
    seed_agent(db, org=ORG2)
    p = seed_prospect(db)  # belongs to ORG
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(TO_CLOSE), org=ORG2)
    assert exc.value.status_code == 404


def test_router_prospect_list_is_org_scoped_and_masked():
    db = FakeDB()
    seed_agent(db)
    seed_agent(db, org=ORG2)
    seed_prospect(db)
    auth_b = AuthContext("user-b", ORG2)
    assert outbound_router.get_prospects(db=db, auth=auth_b) == []
    auth_a = AuthContext("user-a", ORG)
    rows = outbound_router.get_prospects(db=db, auth=auth_a)
    assert len(rows) == 1
    assert "phone" not in rows[0]  # bronze number never leaves the API
    assert rows[0]["phone_masked"] == oc.mask_phone(LANDLINE)


def test_router_optout_lands_only_in_callers_org_ledger():
    db = FakeDB()
    seed_agent(db)
    seed_agent(db, org=ORG2)
    p = seed_prospect(db)
    auth_b = AuthContext("user-b", ORG2)
    body = outbound_router.OptOutBody(phone="(303) 555-0101")
    out = outbound_router.post_opt_out(body, db=db, auth=auth_b)
    assert out["suppressed"] is True
    assert oc.is_suppressed(db, ORG2, LANDLINE)
    assert not oc.is_suppressed(db, ORG, LANDLINE)
    # ORG's own dial to that number is unaffected by ORG2's ledger.
    result = dial(db, p, ScriptedResponder(TO_CLOSE))
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# V9: LAUNCH GATES + CALLER ID (HR-03/12/21)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    [
        "outbound_dnc_policy_on_file",
        "outbound_privacy_section_live",
        "outbound_web_optout_live",
    ],
)
def test_live_mode_refuses_without_each_launch_gate(monkeypatch, flag):
    monkeypatch.setattr(settings, "outbound_test_mode", False)
    monkeypatch.setattr(settings, flag, False)
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert exc.value.status_code == 503
    assert tel.calls == []


def test_live_mode_with_all_gates_open_dials(monkeypatch):
    monkeypatch.setattr(settings, "outbound_test_mode", False)
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    result = dial(db, p, ScriptedResponder(TO_CLOSE))
    assert result["status"] == "completed"


@pytest.mark.parametrize(
    "attr,value",
    [
        ("outbound_caller_id", ""),
        ("outbound_caller_id", None),
        ("outbound_cnam", "Acme Corp"),
        ("outbound_stir_shaken_attestation", "B"),
        ("outbound_stir_shaken_attestation", None),
    ],
)
def test_caller_id_misconfiguration_refuses(monkeypatch, attr, value):
    monkeypatch.setattr(settings, attr, value)
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(TO_CLOSE), telephony=tel)
    assert exc.value.status_code == 503
    assert tel.calls == []
