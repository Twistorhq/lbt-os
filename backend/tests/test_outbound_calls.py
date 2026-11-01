"""TW-884: outbound sales agent (slice 1) + TW-885 legal gate rules.

RED-first suite. Covers the dialer gates (kill switches, test mode,
windows, cadence, caps), the TW-882 talk-track conversation engine,
calendar booking, outcome plumbing, and Angela's HR-01..HR-22 hard
rules from the TW-885 gate memo (landline-only cold dialing, consent
ledger, dial-time suppression, scrub gate, state gates, caller ID,
recording objection, retention purge).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.services import outbound_calls as oc

DENVER = ZoneInfo("America/Denver")
ORG = "org-tw884"
BASE_NOW = datetime(2026, 11, 2, 10, 0, tzinfo=DENVER)  # Monday 10:00
LANDLINE = "+13035550101"
LANDLINE_2 = "+13035550102"
LANDLINE_3 = "+13035550103"
WIRELESS = "+13035550199"
UNKNOWN_NUM = "+13035550155"


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
    """TW-284 telephony port shape: place_call / start_recording /
    stop_recording, with a shared order log for sequence assertions."""

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
    """TW-284 voice-agent port shape: speak(session_id, text)."""

    def __init__(self, order_log=None):
        self.spoken = []
        self.order_log = order_log if order_log is not None else []

    def speak(self, *, session_id, text):
        self.spoken.append(text)
        self.order_log.append(f"speak:{text}")


class FakeLineTypes:
    """Carrier-lookup fake. Default verdict: unknown (fail closed)."""

    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.lookups = []

    def lookup(self, phone_e164):
        self.lookups.append(phone_e164)
        return self.mapping.get(
            phone_e164, {"line_type": "unknown", "carrier": None, "verified": False}
        )


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

    def delete_event(self, event_id):
        before = len(self.events)
        self.events = [e for e in self.events if e["id"] != event_id]
        return len(self.events) < before


class RevokingCalendar(FakeCalendar):
    """TW-888 race fake: the event lands, THEN revocation lands —
    the booking exists before the abort becomes visible."""

    def __init__(self, db):
        super().__init__()
        self.db = db

    def create_event(self, **kwargs):
        event = super().create_event(**kwargs)
        oc.revoke_agent(self.db, ORG, now=BASE_NOW)
        return event


class ScriptedResponder:
    """Pops canned prospect replies; callables run side effects."""

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
# Fixtures / seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    """Legal + dialer config at its compliant test posture."""
    monkeypatch.setattr(settings, "outbound_calls_enabled", True)
    monkeypatch.setattr(settings, "outbound_test_mode", True)
    monkeypatch.setattr(
        settings,
        "outbound_test_numbers",
        ",".join([LANDLINE, LANDLINE_2, LANDLINE_3, WIRELESS, UNKNOWN_NUM]),
    )
    monkeypatch.setattr(settings, "outbound_caller_id", "+13035550100")
    monkeypatch.setattr(settings, "outbound_cnam", "Twistor")
    monkeypatch.setattr(settings, "outbound_stir_shaken_attestation", "A")
    monkeypatch.setattr(settings, "outbound_dnc_policy_on_file", True)
    monkeypatch.setattr(settings, "outbound_privacy_section_live", True)
    monkeypatch.setattr(settings, "outbound_web_optout_live", True)
    yield


def landline_types(*numbers):
    return FakeLineTypes(
        {
            n: {
                "line_type": "business_landline",
                "carrier": "TestTel",
                "verified": True,
            }
            for n in numbers
        }
    )


def make_db():
    return FakeDB()


def seed_agent(db, **over):
    row = {
        "org_id": ORG,
        "enabled": True,
        "revoked_at": None,
        "persona_name": oc.DEFAULT_PERSONA_NAME,
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
    db.table("outbound_agent_settings").insert(row).execute()
    return row


def seed_prospect(db, **over):
    row = {
        "org_id": ORG,
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
    return db.table("outbound_prospects").insert(row).execute().data[0]


def dial(db, prospect, responder, now=BASE_NOW, **kw):
    kw.setdefault("telephony", FakeTelephony())
    kw.setdefault("voice_agent", FakeVoiceAgent())
    kw.setdefault("line_types", landline_types(prospect["phone"]))
    kw.setdefault("calendar", FakeCalendar())
    return oc.place_outbound_call(db, ORG, prospect["id"], responder, now=now, **kw)


HAPPY = [
    "This is Kevin, speaking.",
    "Three phones, front desk answers.",
    "Whoever is free grabs it, weekends it rings out.",
    "About 6 after-hours or weekend calls a week.",
    "Maybe 2 get a call back within the hour.",
    "Yeah, book it.",
]


# ---------------------------------------------------------------------------
# Gates: kill switches, test mode, caller ID (HR-03, HR-04)
# ---------------------------------------------------------------------------


def test_global_kill_switch_blocks_dial(monkeypatch):
    monkeypatch.setattr(settings, "outbound_calls_enabled", False)
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert exc.value.status_code == 409
    assert tel.calls == []


def test_agent_disabled_blocks_dial():
    db = make_db()
    seed_agent(db, enabled=False)
    p = seed_prospect(db)
    tel = FakeTelephony()
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert exc.value.status_code == 409
    assert tel.calls == []


def test_agent_revoked_blocks_dial():
    db = make_db()
    seed_agent(db, revoked_at=BASE_NOW.isoformat())
    p = seed_prospect(db)
    tel = FakeTelephony()
    with pytest.raises(oc.OutboundError):
        dial(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert tel.calls == []


def test_test_mode_allowlist_refuses_other_numbers():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(
        db, phone="+13035550999", phone_hash=oc.hash_phone("+13035550999")
    )
    tel = FakeTelephony()
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert exc.value.status_code == 403
    assert tel.calls == []


def test_caller_id_must_be_owned_config_with_cnam_and_attestation(monkeypatch):
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    monkeypatch.setattr(settings, "outbound_cnam", "Somebody Else")
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(HAPPY))
    assert exc.value.status_code == 503
    monkeypatch.setattr(settings, "outbound_cnam", "Twistor")
    monkeypatch.setattr(settings, "outbound_stir_shaken_attestation", None)
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(HAPPY))
    assert exc.value.status_code == 503
    monkeypatch.setattr(settings, "outbound_stir_shaken_attestation", "A")
    monkeypatch.setattr(settings, "outbound_caller_id", None)
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(HAPPY))
    assert exc.value.status_code == 503


def test_launch_gates_block_live_mode(monkeypatch):
    """HR-12 + HR-21: no live (non-test) dial without the DNC policy
    document on file and the prospect surface live."""
    monkeypatch.setattr(settings, "outbound_test_mode", False)
    monkeypatch.setattr(settings, "outbound_dnc_policy_on_file", False)
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    with pytest.raises(oc.OutboundError) as exc:
        dial(db, p, ScriptedResponder(HAPPY))
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Windows, state gates, caps (HR-13, HR-14, HR-15, HR-16)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 11, 1, 10, 0, tzinfo=DENVER),  # Sunday
        datetime(2026, 11, 2, 18, 0, tzinfo=DENVER),  # after window
        datetime(2026, 11, 2, 12, 30, tzinfo=DENVER),  # lunch skip
        datetime(2026, 11, 2, 7, 30, tzinfo=DENVER),  # before window
    ],
)
def test_outside_window_defers_without_dialing(moment):
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    result = dial(db, p, ScriptedResponder(HAPPY), now=moment, telephony=tel)
    assert result["status"] == "deferred_window"
    assert tel.calls == []
    assert db.tables.get("outbound_call_attempts", []) == []


def test_unknown_timezone_never_dials():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db, timezone=None, state=None)
    result = dial(db, p, ScriptedResponder(HAPPY))
    assert result["status"] == "blocked_unknown_locale"


@pytest.mark.parametrize("state", ["TX", "FL"])
def test_counsel_gated_states_never_dial(state):
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db, state=state, timezone="America/Chicago")
    tel = FakeTelephony()
    result = dial(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert result["status"] == "blocked_state_gate"
    assert tel.calls == []


def test_three_attempts_per_24h_house_cap():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    for i in range(3):
        db.table("outbound_call_attempts").insert(
            {
                "org_id": ORG,
                "prospect_id": p["id"],
                "session_id": None,
                "attempt_no": i + 1,
                "touch_day": 0,
                "dialed": True,
                "disposition": "no_answer",
                "created_at": (BASE_NOW - timedelta(hours=2)).isoformat(),
            }
        ).execute()
    tel = FakeTelephony()
    result = dial(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert result["status"] == "deferred_attempt_cap"
    assert tel.calls == []


# ---------------------------------------------------------------------------
# Line type + consent ledger (HR-05, HR-06, HR-07)
# ---------------------------------------------------------------------------


def test_wireless_without_consent_routes_to_consent_lane():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(
        db,
        phone=WIRELESS,
        phone_hash=oc.hash_phone(WIRELESS),
        phone_masked=oc.mask_phone(WIRELESS),
    )
    tel = FakeTelephony()
    types = FakeLineTypes(
        {WIRELESS: {"line_type": "wireless", "carrier": "MobileCo", "verified": True}}
    )
    result = dial(db, p, ScriptedResponder(HAPPY), telephony=tel, line_types=types)
    assert result["status"] == "consent_required"
    assert tel.calls == []
    row = db.tables["outbound_prospects"][0]
    assert row["lane"] == "consent_capture"
    attempts = db.tables["outbound_call_attempts"]
    assert attempts[0]["dialed"] is False
    assert attempts[0]["line_type"] == "wireless"


def test_wireless_with_written_consent_dials():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(
        db,
        phone=WIRELESS,
        phone_hash=oc.hash_phone(WIRELESS),
        phone_masked=oc.mask_phone(WIRELESS),
    )
    db.table("outbound_consents").insert(
        {
            "org_id": ORG,
            "phone_hash": oc.hash_phone(WIRELESS),
            "seller_name": "Twistor",
            "scope_ai_voice": True,
            "source": "web_form",
            "evidence_ref": "form-2026-10-30",
            "granted_at": "2026-10-30T15:00:00-06:00",
            "revoked_at": None,
        }
    ).execute()
    types = FakeLineTypes(
        {WIRELESS: {"line_type": "wireless", "carrier": "MobileCo", "verified": True}}
    )
    result = dial(db, p, ScriptedResponder(["NO_ANSWER"]), line_types=types)
    assert result["status"] == "completed"
    assert result["disposition"] == "no_answer"


def test_unknown_line_type_fails_closed():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db, phone=UNKNOWN_NUM, phone_hash=oc.hash_phone(UNKNOWN_NUM))
    tel = FakeTelephony()
    result = dial(
        db, p, ScriptedResponder(HAPPY), telephony=tel, line_types=FakeLineTypes()
    )
    assert result["status"] == "blocked_line_type"
    assert tel.calls == []


def test_missing_line_type_provider_fails_closed(monkeypatch):
    import app.services.outbound_lookups as lk

    def boom():
        raise lk.LineTypeNotConfigured("no carrier lookup configured")

    monkeypatch.setattr(lk, "get_line_type_provider", boom)
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    with pytest.raises(oc.OutboundError) as exc:
        oc.place_outbound_call(
            db,
            ORG,
            p["id"],
            ScriptedResponder(HAPPY),
            now=BASE_NOW,
            telephony=FakeTelephony(),
            calendar=FakeCalendar(),
        )
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Suppression: dial-time DNC, registry scrub (HR-08..HR-11)
# ---------------------------------------------------------------------------


def test_suppression_after_import_blocks_dial_at_dial_time():
    """Number suppressed at 10:00 cannot be dialed at 10:05 by a batch
    loaded at 9:00 (HR-09)."""
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    oc.record_suppression(db, ORG, LANDLINE, source="web_optout")
    tel = FakeTelephony()
    result = dial(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert result["status"] == "skipped_dnc"
    assert tel.calls == []


def test_unscrubbed_prospect_never_dials():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db, scrub_status="unscrubbed")
    result = dial(db, p, ScriptedResponder(HAPPY))
    assert result["status"] == "blocked_unscrubbed"


def test_scrub_marks_registry_hits_and_logs_metadata():
    db = make_db()
    seed_agent(db)
    seed_prospect(db, scrub_status="unscrubbed")
    seed_prospect(
        db,
        company="Hit Co",
        phone=LANDLINE_2,
        rank=2,
        phone_hash=oc.hash_phone(LANDLINE_2),
        scrub_status="unscrubbed",
    )
    registry = FakeRegistry(hits={LANDLINE_2})
    summary = oc.scrub_prospects(
        db, ORG, registry, batch_id="batch-9", registry_version="2026-11-01"
    )
    assert summary == {"checked": 2, "hits": 1}
    rows = {r["phone"]: r for r in db.tables["outbound_prospects"]}
    assert rows[LANDLINE]["scrub_status"] == "clear"
    assert rows[LANDLINE_2]["scrub_status"] == "registry_hit"
    assert db.tables["outbound_suppression"][0]["source"] == "registry_hit"
    run = db.tables["outbound_scrub_runs"][0]
    assert run["registry_version"] == "2026-11-01"
    assert run["batch_id"] == "batch-9"


def test_import_dedupes_and_skips_suppressed():
    db = make_db()
    seed_agent(db)
    oc.record_suppression(db, ORG, LANDLINE_3, source="dnc_request")
    rows = [
        {"company": "A Co", "city": "Denver", "phone": LANDLINE},
        {"company": "A Co Dup", "city": "Denver", "phone": "303-555-0101"},
        {"company": "Suppressed Co", "city": "Denver", "phone": LANDLINE_3},
    ]
    summary = oc.import_prospects(db, ORG, rows)
    assert summary == {"imported": 1, "duplicates": 1, "skipped_suppressed": 1}
    stored = db.tables["outbound_prospects"][0]
    assert stored["phone"] == LANDLINE  # normalized to E.164
    assert stored["scrub_status"] == "unscrubbed"
    assert stored["state"] == "CO"
    assert stored["timezone"] == "America/Denver"


# ---------------------------------------------------------------------------
# Conversation engine: disclosure, flow, objections (HR-01, HR-02)
# ---------------------------------------------------------------------------


def test_disclosure_is_first_utterance_with_all_elements():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    result = dial(db, p, ScriptedResponder(HAPPY))
    opening = result["transcript"][0]
    assert opening["role"] == "agent"
    text = opening["text"]
    assert oc.DEFAULT_PERSONA_NAME in text
    assert "automated assistant" in text
    assert "Twistor" in text
    assert "sales call" in text
    attempt = db.tables["outbound_call_attempts"][0]
    session = db.tables["ai_call_sessions"][0]
    assert session["ai_disclosure_given"] is True
    assert attempt["disposition"] == "demo_set"


def test_happy_path_books_demo_with_audit_answers():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    cal = FakeCalendar()
    result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert result["disposition"] == "demo_set"
    assert len(cal.events) == 1
    event = cal.events[0]
    assert event["title"] == "Twistor workflow review — Hughes Mechanical"
    assert "Twistor's automated assistant" in event["description"]  # HR-22
    assert "reschedule" in event["description"]
    assert (event["end"] - event["start"]) == timedelta(minutes=20)
    attempt = db.tables["outbound_call_attempts"][0]
    answers = attempt["audit_answers"]
    assert answers["q1"] == "Three phones, front desk answers."
    assert answers["after_hours_calls"] == 6
    assert answers["callbacks_within_hour"] == 2
    assert answers["leak_per_week"] == 4
    assert attempt["demo_event_id"] == event["id"]
    prospect = db.tables["outbound_prospects"][0]
    assert prospect["status"] == "converted"
    assert prospect["touch_count"] == 1


def test_demo_slot_respects_buffer_and_never_double_books():
    db = make_db()
    seed_agent(db, daily_cap=25)
    cal = FakeCalendar()
    p1 = seed_prospect(db)
    dial(db, p1, ScriptedResponder(HAPPY), calendar=cal)
    p2 = seed_prospect(
        db,
        company="Air Repair",
        phone=LANDLINE_2,
        rank=2,
        phone_hash=oc.hash_phone(LANDLINE_2),
    )
    dial(
        db,
        p2,
        ScriptedResponder(HAPPY),
        calendar=cal,
        line_types=landline_types(LANDLINE_2),
    )
    assert len(cal.events) == 2
    first, second = sorted(cal.events, key=lambda e: e["start"])
    gap = second["start"] - first["end"]
    assert gap >= timedelta(minutes=10)  # configured buffer


def test_missing_calendar_degrades_to_callback_not_failure():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    result = oc.place_outbound_call(
        db,
        ORG,
        p["id"],
        ScriptedResponder(HAPPY),
        now=BASE_NOW,
        telephony=FakeTelephony(),
        line_types=landline_types(LANDLINE),
        calendar=None,
    )
    assert result["disposition"] == "callback"
    attempt = db.tables["outbound_call_attempts"][0]
    assert attempt["audit_answers"]["booking_failed"] is True


def test_robot_question_gets_truthful_answer():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    replies = [
        "This is Kevin, speaking.",
        "Wait, are you a robot?",
        "Three phones, front desk answers.",
        "Whoever is free grabs it, weekends it rings out.",
        "About 6 after-hours or weekend calls a week.",
        "Maybe 2 get a call back within the hour.",
        "Yeah, book it.",
    ]
    result = dial(db, p, ScriptedResponder(replies))
    agent_lines = [t["text"] for t in result["transcript"] if t["role"] == "agent"]
    truth = [
        t for t in agent_lines if "automated assistant" in t and "not a person" in t
    ]
    assert truth, "HR-02: direct question, direct truthful answer"
    assert result["disposition"] == "demo_set"


def test_servicetitan_objection_branch():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    replies = [
        "This is Kevin, speaking.",
        "We already use ServiceTitan for all of that.",
        "Three phones, front desk answers.",
        "Whoever is free grabs it, weekends it rings out.",
        "About 6 after-hours or weekend calls a week.",
        "Maybe 2 get a call back within the hour.",
        "Yeah, book it.",
    ]
    result = dial(db, p, ScriptedResponder(replies))
    agent_lines = [t["text"] for t in result["transcript"] if t["role"] == "agent"]
    assert any("not asking you to rip it out" in t for t in agent_lines)
    assert result["disposition"] == "demo_set"


def test_honest_no_date_start_line():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    replies = HAPPY[:-1] + ["Sounds good, when can I start?"]
    result = dial(db, p, ScriptedResponder(replies))
    agent_lines = [t["text"] for t in result["transcript"] if t["role"] == "agent"]
    assert any("not going to invent a date" in t for t in agent_lines)


def test_send_info_books_review_only_audit():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    replies = [
        "This is Kevin, speaking.",
        "Just send me some info.",
        "Sure, the free review works.",
    ]
    result = dial(db, p, ScriptedResponder(replies))
    assert result["disposition"] == "audit_booked"
    assert db.tables["outbound_prospects"][0]["status"] == "engaged"


def test_gatekeeper_callback_disposition():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    replies = [
        "Hughes Mechanical, how can I help?",
        "He is not in right now, call back tomorrow morning.",
    ]
    result = dial(db, p, ScriptedResponder(replies))
    assert result["disposition"] == "callback"
    assert db.tables["outbound_prospects"][0]["status"] == "engaged"


def test_voicemail_gets_disclosure_message_and_cadence():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    result = dial(db, p, ScriptedResponder(["VOICEMAIL"]))
    assert result["disposition"] == "voicemail"
    agent_lines = [t["text"] for t in result["transcript"] if t["role"] == "agent"]
    vm = agent_lines[-1]
    assert "automated assistant" in vm
    assert "+13035550100" in vm  # callback number (47 CFR 64.1200(b))
    prospect = db.tables["outbound_prospects"][0]
    assert prospect["touch_count"] == 1
    assert prospect["next_touch_at"].startswith("2026-11-05")


def test_no_answer_counts_touch():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    result = dial(db, p, ScriptedResponder(["NO_ANSWER"]))
    assert result["disposition"] == "no_answer"
    assert result["transcript"] == []


# ---------------------------------------------------------------------------
# DNC mid-call + revocation (HR-08, HR-10)
# ---------------------------------------------------------------------------


def test_dnc_mid_call_stops_everything_and_suppresses():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    replies = [
        "This is Kevin, speaking.",
        "Do not call this number again.",
        "Yeah, book it.",  # must never be reached
    ]
    result = dial(db, p, ScriptedResponder(replies))
    assert result["disposition"] == "dnc"
    agent_lines = [t["text"] for t in result["transcript"] if t["role"] == "agent"]
    assert agent_lines[-1].startswith("You got it")
    suppression = db.tables["outbound_suppression"]
    assert suppression and suppression[0]["source"] == "dnc_request"
    prospect = db.tables["outbound_prospects"][0]
    assert prospect["status"] == "dnc"
    # and a later dial attempt is refused at dial time
    again = dial(db, p, ScriptedResponder(HAPPY))
    assert again["status"] == "skipped_dnc"


# ---------------------------------------------------------------------------
# Recording (HR-17, HR-18) via the TW-284 toggle
# ---------------------------------------------------------------------------


def test_recording_announcement_precedes_capture_on_outbound():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    order: list[str] = []
    tel = FakeTelephony(order_log=order)
    voice = FakeVoiceAgent(order_log=order)
    result = dial(
        db,
        p,
        ScriptedResponder(["NO_ANSWER"]),
        telephony=tel,
        voice_agent=voice,
        recording_enabled=True,
    )
    assert result["disposition"] == "no_answer"
    # TW-284 rule: the spoken announcement lands BEFORE capture starts.
    assert voice.spoken[0] == "This call is now being recorded."
    assert order.index("speak:This call is now being recorded.") < order.index(
        "start_recording"
    )
    events = db.tables["call_recording_events"]
    assert any(e["event"] == "recording_enabled" for e in events)
    assert db.tables["outbound_call_attempts"][0]["recording_enabled"] is True


def test_recording_objection_stops_and_flags_number():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    replies = [
        "This is Kevin, speaking.",
        "Do not record this call.",
        "Three phones, front desk answers.",
        "Whoever is free grabs it, weekends it rings out.",
        "About 6 after-hours or weekend calls a week.",
        "Maybe 2 get a call back within the hour.",
        "No thanks.",
    ]
    dial(db, p, ScriptedResponder(replies), recording_enabled=True)
    events = db.tables["call_recording_events"]
    assert any(e["event"] == "recording_disabled" for e in events)
    assert db.tables["outbound_prospects"][0]["no_record"] is True
    # future call: recording forced off even when requested
    db.tables["call_recording_events"].clear()
    p2 = db.tables["outbound_prospects"][0]
    dial(db, p2, ScriptedResponder(["NO_ANSWER"]), recording_enabled=True)
    assert db.tables["call_recording_events"] == []
    assert db.tables["outbound_call_attempts"][-1]["recording_enabled"] is False


# ---------------------------------------------------------------------------
# Kill switch mid-call (TW-766 revocation stops in-flight work)
# ---------------------------------------------------------------------------


def test_revocation_mid_call_aborts_without_booking():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    cal = FakeCalendar()

    def revoke(_history):
        row = db.tables["outbound_agent_settings"][0]
        row["revoked_at"] = BASE_NOW.isoformat()
        row["enabled"] = False
        return "Three phones, front desk answers."

    replies = ["This is Kevin, speaking.", revoke] + HAPPY[2:]
    result = dial(db, p, ScriptedResponder(replies), calendar=cal)
    assert result["disposition"] == "aborted"
    assert cal.events == []


# ---------------------------------------------------------------------------
# Cadence + dialer run + daily cap
# ---------------------------------------------------------------------------


def test_cadence_day3_not_day1():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    dial(db, p, ScriptedResponder(["NO_ANSWER"]))  # Day 0 touch
    day1 = BASE_NOW + timedelta(days=1)
    assert oc.due_prospects(db, ORG, day1) == []
    day3 = BASE_NOW + timedelta(days=3)
    due = oc.due_prospects(db, ORG, day3)
    assert [d["id"] for d in due] == [p["id"]]


def test_fourth_touch_rests_prospect():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(
        db,
        touch_count=3,
        status="in_cadence",
        cadence_start="2026-10-21",
        next_touch_at="2026-11-02T08:00:00-06:00",
    )
    dial(db, p, ScriptedResponder(["NO_ANSWER"]))
    prospect = db.tables["outbound_prospects"][0]
    assert prospect["touch_count"] == 4
    assert prospect["status"] == "resting"


def test_run_dialer_honors_daily_cap():
    db = make_db()
    seed_agent(db, daily_cap=2)
    prospects = []
    for i, phone in enumerate([LANDLINE, LANDLINE_2, LANDLINE_3]):
        prospects.append(
            seed_prospect(
                db,
                company=f"Shop {i}",
                phone=phone,
                rank=i + 1,
                phone_hash=oc.hash_phone(phone),
            )
        )
    summary = oc.run_dialer(
        db,
        ORG,
        lambda _p: ScriptedResponder(["NO_ANSWER"]),
        now=BASE_NOW,
        telephony=FakeTelephony(),
        line_types=landline_types(LANDLINE, LANDLINE_2, LANDLINE_3),
        calendar=FakeCalendar(),
    )
    assert summary["dialed"] == 2
    assert len(db.tables["outbound_call_attempts"]) == 2


# ---------------------------------------------------------------------------
# Rollup + retention (HR-19, HR-20)
# ---------------------------------------------------------------------------


def test_daily_rollup_counts_and_escalations_carry_transcripts():
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    dial(db, p, ScriptedResponder(HAPPY))
    p2 = seed_prospect(
        db,
        company="DNC Co",
        phone=LANDLINE_2,
        rank=2,
        phone_hash=oc.hash_phone(LANDLINE_2),
    )
    dial(
        db,
        p2,
        ScriptedResponder(
            ["This is Kevin, speaking.", "Do not call this number again."]
        ),
        line_types=landline_types(LANDLINE_2),
    )
    rollup = oc.daily_rollup(db, ORG, date(2026, 11, 2))
    assert rollup["conversations"] == 2
    assert rollup["demos"] == 1
    assert rollup["dncs"] == 1
    assert rollup["audits"] == 1
    assert len(rollup["escalations"]) == 1
    esc = rollup["escalations"][0]
    assert esc["company"] == "Hughes Mechanical"
    assert esc["transcript"], "escalation must carry the transcript"


def test_retention_purge_expired_transcripts_only():
    db = make_db()
    seed_agent(db)
    old = datetime(2025, 9, 1, 10, 0, tzinfo=DENVER)
    db.table("outbound_call_attempts").insert(
        {
            "org_id": ORG,
            "prospect_id": "p-old",
            "dialed": True,
            "disposition": "no_answer",
            "transcript": [{"role": "agent", "text": "old"}],
            "created_at": old.isoformat(),
        }
    ).execute()
    db.table("outbound_call_attempts").insert(
        {
            "org_id": ORG,
            "prospect_id": "p-new",
            "dialed": True,
            "disposition": "no_answer",
            "transcript": [{"role": "agent", "text": "new"}],
            "created_at": BASE_NOW.isoformat(),
        }
    ).execute()
    oc.record_suppression(db, ORG, LANDLINE, source="dnc_request")
    counts = oc.purge_expired(db, ORG, now=BASE_NOW)
    assert counts["transcripts"] == 1
    rows = db.tables["outbound_call_attempts"]
    assert rows[0]["transcript"] is None
    assert rows[1]["transcript"] is not None
    assert db.tables["outbound_suppression"], "suppression is never purged here"
    assert db.tables["outbound_purge_log"][0]["asset_class"] == "transcripts"


# ---------------------------------------------------------------------------
# Legal gate config + migration + router contracts
# ---------------------------------------------------------------------------


def test_legal_gate_config_matches_memo():
    gate = oc.LEGAL_GATE
    assert gate["disclosure_first_utterance"] is True  # HR-01
    assert gate["claims_human"] == "never"  # HR-02
    assert gate["cnam"] == "Twistor"  # HR-03
    assert gate["stir_shaken_attestation_required"] == "A"  # HR-03
    assert gate["number_pool_spoofing"] == "disabled"  # HR-04
    assert gate["line_type_check"] == "before_every_dial"  # HR-05
    assert gate["cold_dial_allowed_line_types"] == ["business_landline"]  # HR-06
    assert gate["wireless_requires_pewc"] is True  # HR-07
    assert gate["revocation_any_channel"] == "instant_suppression"  # HR-08
    assert gate["suppression_check"] == "at_dial_time"  # HR-09
    assert gate["dnc_honor"] == "permanent"  # HR-10
    assert gate["dnc_record_retention_years"] >= 5  # HR-10
    assert gate["batch_registry_scrub"] == "required"  # HR-11
    assert gate["window_source"] == "prospect_local_time"  # HR-13
    assert gate["max_attempts_per_number_per_24h"] == 3  # HR-15
    assert gate["recording_default"] == "off"  # HR-17
    assert gate["announcement_before_capture"] is True  # HR-17
    assert gate["recording_unknown_state"] == "disabled"  # HR-18
    assert gate["transcript_storage"] == "bronze_only"  # HR-19
    retention = gate["retention_days"]  # HR-20
    assert retention["audio"] == 90
    assert retention["consents"] >= 1825
    assert retention["suppression"] >= 1825
    rules = oc.STATE_RULES  # HR-14 + HR-16
    assert rules["TX"]["dialing_enabled"] is False
    assert rules["FL"]["dialing_enabled"] is False
    assert (rules["CO"]["window_start"], rules["CO"]["window_end"]) == (
        "08:00",
        "17:00",
    )
    assert (rules["FL"]["window_start"], rules["FL"]["window_end"]) == (
        "08:00",
        "20:00",
    )
    assert "holidays" in rules["CO"]


def test_migration_contract():
    path = (
        Path(__file__).resolve().parents[2]
        / "supabase"
        / "migration_outbound_calls.sql"
    )
    sql = path.read_text()
    for table in (
        "outbound_agent_settings",
        "outbound_prospects",
        "outbound_call_attempts",
        "outbound_suppression",
        "outbound_consents",
        "outbound_scrub_runs",
        "outbound_purge_log",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
    assert sql.count("CREATE POLICY") >= 7
    assert "auth_org_id()" in sql
    assert re.search(r"bronze", sql, re.IGNORECASE)


def test_router_import_smoke():
    from app.routers import outbound

    paths = {r.path for r in outbound.router.routes}
    assert "/outbound/settings" in paths
    assert "/outbound/revoke" in paths
    assert "/outbound/prospects/import" in paths
    assert "/outbound/prospects" in paths
    assert "/outbound/scrub" in paths
    assert "/outbound/opt-out" in paths
    assert "/outbound/rollup" in paths


# ---------------------------------------------------------------------------
# Fix round (Rosa REQUEST CHANGES + Rex findings TW-888/889/890):
# RED proofs written against the slice-1 build, before the patch.
# ---------------------------------------------------------------------------

CHICAGO = ZoneInfo("America/Chicago")


def test_revoke_during_booking_creation_voids_the_event():
    """TW-888 compensation path: revocation lands AFTER create_event
    returns but before the confirmation is spoken. The phantom event
    must be voided, not left on the calendar."""
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    cal = RevokingCalendar(db)
    result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert result["status"] == "aborted"
    assert cal.events == [], "phantom booking survived revocation"
    attempt = db.tables["outbound_call_attempts"][-1]
    assert attempt["disposition"] == "aborted"
    assert attempt["demo_event_id"] is None
    assert (attempt["audit_answers"] or {}).get("booking_voided") is True


def test_unverified_landline_routes_to_consent_capture():
    """TW-890: a business_landline LABEL with verified=False is a
    guess, not a verified landline (HR-06). No dial; the number takes
    the consent gate like wireless/residential."""
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    types = FakeLineTypes(
        {
            LANDLINE: {
                "line_type": "business_landline",
                "carrier": "TestTel",
                "verified": False,
            }
        }
    )
    result = dial(db, p, ScriptedResponder(HAPPY), telephony=tel, line_types=types)
    assert tel.calls == []
    assert result["status"] == "consent_required"
    row = db.tables["outbound_prospects"][0]
    assert row["lane"] == "consent_capture"
    attempt = db.tables["outbound_call_attempts"][-1]
    assert attempt["dialed"] is False
    assert attempt["disposition"] == "blocked_consent_required"


def test_owner_callback_request_escalates_in_rollup():
    """Rosa M1: an owner asking for a callback is an escalation with
    transcript (ticket spec item 5), not a bare disposition."""
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    result = dial(
        db,
        p,
        ScriptedResponder(
            ["This is Kevin, speaking.", "I'm too busy right now, call me back"]
        ),
    )
    assert result["disposition"] == "callback"
    rollup = oc.daily_rollup(db, ORG, date(2026, 11, 2))
    assert len(rollup["escalations"]) == 1
    esc = rollup["escalations"][0]
    assert esc["prospect_id"] == p["id"]
    assert esc["disposition"] == "callback"
    assert esc["transcript"], "escalation must carry the transcript"


def test_texas_sunday_window_opens_at_noon():
    """Rosa M2 / HR-14: STATE_RULES TX sunday_start=12:00 must be
    enforced by _window_open, not just loaded."""
    agent = {
        "call_days": [0, 1, 2, 3, 4, 5, 6],
        "window_start": "08:00",
        "window_end": "17:00",
        "lunch_start": None,
        "lunch_end": None,
    }
    tx = {"state": "TX", "timezone": "America/Chicago"}
    sunday_11 = datetime(2026, 11, 1, 11, 0, tzinfo=CHICAGO)  # a Sunday
    assert oc._window_open(agent, tx, sunday_11) is False
    assert oc._window_open(agent, tx, sunday_11.replace(hour=12)) is True
    saturday_10 = datetime(2026, 10, 31, 10, 0, tzinfo=CHICAGO)
    assert oc._window_open(agent, tx, saturday_10) is True
    # CO has no Sunday rule: same Sunday moment is governed by the
    # plain window intersection.
    co = {"state": "CO", "timezone": "America/Denver"}
    sunday_10_denver = datetime(2026, 11, 1, 10, 0, tzinfo=DENVER)
    assert oc._window_open(agent, co, sunday_10_denver) is True


def test_recording_config_failure_lands_before_the_dial(monkeypatch):
    """Rosa M3: recording requested + voice provider unconfigured
    must fail BEFORE place_call — never a dialed call with no
    disposition row."""
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)

    def no_voice():
        raise oc.vp.ProviderNotConfigured("voice provider not configured")

    monkeypatch.setattr(oc.vp, "get_voice_agent", no_voice)
    tel = FakeTelephony()
    with pytest.raises(oc.OutboundError) as exc:
        dial(
            db,
            p,
            ScriptedResponder(HAPPY),
            telephony=tel,
            voice_agent=None,
            recording_enabled=True,
        )
    assert exc.value.status_code == 503
    assert tel.calls == [], "call was placed before the config failure"
    assert db.tables.get("ai_call_sessions", []) == []
    assert db.tables.get("outbound_call_attempts", []) == []


def test_run_dialer_isolates_per_prospect_failures():
    """Rosa M4 / self-healing rule: one prospect's provider explosion
    is a dead-letter record; the rest of the batch still dials."""
    db = make_db()
    seed_agent(db)
    p1 = seed_prospect(db)
    seed_prospect(
        db,
        company="Second Shop",
        phone=LANDLINE_2,
        rank=2,
        phone_hash=oc.hash_phone(LANDLINE_2),
        phone_masked=oc.mask_phone(LANDLINE_2),
    )

    def exploding(_history):
        raise RuntimeError("responder bridge exploded")

    def factory(prospect):
        if prospect["id"] == p1["id"]:
            return exploding
        return ScriptedResponder(HAPPY)

    out = oc.run_dialer(
        db,
        ORG,
        factory,
        now=BASE_NOW,
        telephony=FakeTelephony(),
        voice_agent=FakeVoiceAgent(),
        line_types=landline_types(LANDLINE, LANDLINE_2),
        calendar=FakeCalendar(),
    )
    assert out["attempted"] == 2
    assert out["results"][0]["status"] == "error"
    assert out["results"][0]["prospect_id"] == p1["id"]
    assert "RuntimeError" in out["results"][0]["detail"]
    assert out["results"][1]["status"] == "completed"
    assert out["dialed"] == 1


def test_recording_objection_survives_toggle_failure(monkeypatch):
    """Rosa M4: a set_recording fault on the objection path must not
    crash the call or skip the permanent no_record flag (HR-17)."""
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    real_toggle = oc.pa.set_recording

    def flaky_toggle(db_, **kwargs):
        if kwargs.get("on") is False:
            raise RuntimeError("toggle write failed")
        return real_toggle(db_, **kwargs)

    monkeypatch.setattr(oc.pa, "set_recording", flaky_toggle)
    result = dial(
        db,
        p,
        ScriptedResponder(["This is Kevin, speaking.", "Do not record this call."]),
        recording_enabled=True,
    )
    assert result["status"] == "completed"
    stored = [r for r in db.tables["outbound_prospects"] if r["id"] == p["id"]][0]
    assert stored["no_record"] is True


def test_purge_logs_every_run_even_when_nothing_expires():
    """Rosa N1 / HR-20: the purge log is the proof the job ran —
    a no-op run still writes its row."""
    db = make_db()
    seed_agent(db)
    counts = oc.purge_expired(db, ORG, now=BASE_NOW)
    assert counts["transcripts"] == 0
    log = db.tables.get("outbound_purge_log", [])
    assert len(log) == 1
    assert log[0]["purged_count"] == 0
    assert log[0]["asset_class"] == "transcripts"


def test_unverified_landline_with_pewc_consent_dials():
    """TW-890 semantics lock: the consent gate is a gate, not a wall.
    An unverified landline WITH a Twistor + AI-voice PEWC record is
    callable under HR-07, exactly like a consented wireless number."""
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    db.table("outbound_consents").insert(
        {
            "org_id": ORG,
            "phone_hash": oc.hash_phone(LANDLINE),
            "seller_name": "Twistor",
            "scope_ai_voice": True,
            "source": "web_form",
            "evidence_ref": "form-2026-10-30",
            "granted_at": "2026-10-30T15:00:00-06:00",
            "revoked_at": None,
        }
    ).execute()
    types = FakeLineTypes(
        {
            LANDLINE: {
                "line_type": "business_landline",
                "carrier": "TestTel",
                "verified": False,
            }
        }
    )
    result = dial(db, p, ScriptedResponder(["NO_ANSWER"]), line_types=types)
    assert result["status"] == "completed"
    assert result["disposition"] == "no_answer"


def test_gatekeeper_callback_request_does_not_escalate():
    """Rosa M1 guard: 'callback' alone is not an escalation — the
    owner must be the one asking (rollup spec: owner callbacks and
    demos escalate; gatekeeper brush-offs do not)."""
    db = make_db()
    seed_agent(db)
    p = seed_prospect(db)
    result = dial(db, p, ScriptedResponder(["He is not here, try again tomorrow"]))
    assert result["disposition"] == "callback"
    rollup = oc.daily_rollup(db, ORG, date(2026, 11, 2))
    assert rollup["escalations"] == []
