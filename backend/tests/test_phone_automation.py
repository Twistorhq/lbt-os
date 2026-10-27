"""Tests for TW-284 AI phone automation (Zeke Okafor).

Justynn's 2026-10-27 rulings, pinned as behavior:
- Path A (Twilio + OpenAI Realtime), built behind config; nothing live.
- Recording is a per-call toggle; switching it ON mid-call makes the agent
  speak "this call is now being recorded" at that moment; changes audit-logged.
- Coverage 24/7, on as needed: after-hours auto-mode (per-shop hours) AND
  manual on-demand activation.
- Emergency escalation: owner sets MULTIPLE emergency numbers at signup;
  they are tried in priority order until one acknowledges; unacknowledged
  escalation is bounded and lands in a final fallback state.
- The AI discloses it is an AI at call start (CO AI-disclosure law).
- Emergency intake books onto the docket (job + appointment); missed or
  double-booked appointments auto-reschedule and the customer is told why.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from app.services import phone_automation as pa

ORG_ID = "org-tw284-test"
SHOP_NUMBER = "+13035550142"
CALLER = "+13035550199"
DENVER = ZoneInfo("America/Denver")


# ---------------------------------------------------------------------------
# Fake DB (house pattern, mirrors test_missed_call.py)
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._rows = list(db._tables.get(table, []))
        self._pending_insert = None
        self._pending_update = None
        self._pending_delete = False

    def select(self, _cols="*"):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def in_(self, col, values):
        self._rows = [r for r in self._rows if r.get(col) in values]
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col)),
                        reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def insert(self, row):
        self._pending_insert = dict(row)
        return self

    def update(self, values):
        self._pending_update = dict(values)
        return self

    def delete(self):
        self._pending_delete = True
        return self

    def execute(self):
        if self._pending_insert is not None:
            row = self._pending_insert
            row.setdefault("id", f"{self._table}-{len(self._db._tables[self._table])}")
            self._db._tables[self._table].append(row)
            self._pending_insert = None
            return FakeResult([row])
        if self._pending_update is not None:
            for r in self._rows:
                r.update(self._pending_update)
            self._pending_update = None
            return FakeResult(self._rows)
        if self._pending_delete:
            doomed = {id(r) for r in self._rows}
            self._db._tables[self._table] = [
                r for r in self._db._tables[self._table] if id(r) not in doomed
            ]
            self._pending_delete = False
            return FakeResult([])
        return FakeResult(self._rows)


class FakeDB:
    def __init__(self):
        self._tables = {
            "organizations": [],
            "org_phone_numbers": [],
            "call_logs": [],
            "customers": [],
            "jobs": [],
            "appointments": [],
            "leads": [],
            "toll_events": [],
            "phone_automation_settings": [],
            "emergency_contacts": [],
            "ai_call_sessions": [],
            "call_recording_events": [],
            "escalation_chains": [],
            "escalation_events": [],
        }

    def table(self, name):
        if name not in self._tables:
            raise RuntimeError(f"table {name} does not exist")
        return FakeQuery(self, name)


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------

class FakeTelephony:
    def __init__(self, events):
        self.events = events
        self.calls = []
        self.sms = []

    def place_call(self, *, to, say_text):
        self.calls.append((to, say_text))
        self.events.append(("call", to))
        return f"CAfake{len(self.calls)}"

    def send_sms(self, *, to, body):
        self.sms.append((to, body))
        self.events.append(("sms", to))

    def start_recording(self, *, call_sid):
        self.events.append(("recording_start", call_sid))

    def stop_recording(self, *, call_sid):
        self.events.append(("recording_stop", call_sid))


class FakeVoiceAgent:
    def __init__(self, events):
        self.events = events
        self.utterances = []

    def speak(self, *, session_id, text):
        self.utterances.append(text)
        self.events.append(("speak", text))


# ---------------------------------------------------------------------------
# Fixtures — named for the family, per Zeke's habit
# ---------------------------------------------------------------------------

def _seed_shop(db, *, enabled=True, after_hours_auto=True):
    db._tables["organizations"].append({
        "id": ORG_ID, "name": "Kreighton's Test HVAC", "state": "CO",
        "plan": "trades_pro", "subscription_status": "active",
    })
    db._tables["org_phone_numbers"].append({
        "org_id": ORG_ID, "phone_number": SHOP_NUMBER,
    })
    db._tables["phone_automation_settings"].append({
        "org_id": ORG_ID,
        "enabled": enabled,
        "after_hours_auto": after_hours_auto,
        "timezone": "America/Denver",
        "open_days": [1, 2, 3, 4, 5],
        "open_time": "07:00",
        "close_time": "19:00",
        "manual_active_until": None,
    })


@pytest.fixture()
def world():
    db = FakeDB()
    _seed_shop(db)
    events = []
    return db, FakeTelephony(events), FakeVoiceAgent(events), events


def _global_on():
    return mock.patch.object(
        pa, "_global_enabled", lambda: True)


def _at(hour, minute=0, day=27):
    """A Denver-local moment in Oct 2026 (27th is a Tuesday)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=DENVER)


# ---------------------------------------------------------------------------
# Coverage: after-hours auto-mode, manual activation, toggles
# ---------------------------------------------------------------------------

def test_ai_does_not_answer_when_global_flag_off(world):
    db, tel, agent, _ = world
    with mock.patch.object(pa, "_global_enabled", lambda: False):
        decision = pa.ai_should_answer(db, ORG_ID, now=_at(22))
    assert decision["answer"] is False
    assert decision["reason"] == "global-disabled"


def test_ai_does_not_answer_when_shop_toggle_off(world):
    db, tel, agent, _ = world
    db._tables["phone_automation_settings"][0]["enabled"] = False
    with _global_on():
        decision = pa.ai_should_answer(db, ORG_ID, now=_at(22))
    assert decision["answer"] is False
    assert decision["reason"] == "shop-disabled"


def test_after_hours_auto_mode_answers(world):
    db, tel, agent, _ = world
    with _global_on():
        decision = pa.ai_should_answer(db, ORG_ID, now=_at(21, 30))
    assert decision["answer"] is True
    assert decision["mode"] == "after_hours"


def test_weekend_counts_as_after_hours(world):
    db, tel, agent, _ = world
    with _global_on():
        # Oct 31 2026 is a Saturday; shop is Mon-Fri.
        decision = pa.ai_should_answer(db, ORG_ID, now=_at(10, day=31))
    assert decision["answer"] is True
    assert decision["mode"] == "after_hours"


def test_business_hours_no_answer_without_manual(world):
    db, tel, agent, _ = world
    with _global_on():
        decision = pa.ai_should_answer(db, ORG_ID, now=_at(11))
    assert decision["answer"] is False
    assert decision["reason"] == "business-hours"


def test_manual_activation_answers_during_business_hours(world):
    db, tel, agent, _ = world
    now = _at(11)
    pa.activate_manual(db, ORG_ID, minutes=120, now=now)
    with _global_on():
        decision = pa.ai_should_answer(db, ORG_ID, now=now + timedelta(minutes=30))
    assert decision["answer"] is True
    assert decision["mode"] == "manual"


def test_manual_activation_expires(world):
    db, tel, agent, _ = world
    now = _at(11)
    pa.activate_manual(db, ORG_ID, minutes=30, now=now)
    with _global_on():
        decision = pa.ai_should_answer(db, ORG_ID, now=now + timedelta(minutes=31))
    assert decision["answer"] is False
    assert decision["reason"] == "business-hours"


# ---------------------------------------------------------------------------
# Session start: AI disclosure first, toll, refusal when inactive
# ---------------------------------------------------------------------------

def test_start_ai_call_speaks_disclosure_first_and_flags_it(world):
    db, tel, agent, _ = world
    with _global_on():
        session = pa.start_ai_call(
            db, org_id=ORG_ID, call_sid="CA-tw284-1", from_number=CALLER,
            telephony=tel, voice_agent=agent, now=_at(22),
        )
    assert session["ai_disclosure_given"] is True
    assert agent.utterances, "agent said nothing"
    first = agent.utterances[0].lower()
    assert "ai" in first and "automated" in first  # it discloses what it is
    assert "Kreighton's Test HVAC" in agent.utterances[0]
    tolls = [t for t in db._tables["toll_events"]
             if t.get("event_type") == "call.ai_handled"]
    assert len(tolls) == 1


def test_start_ai_call_refused_when_answering_inactive(world):
    db, tel, agent, _ = world
    with _global_on():
        with pytest.raises(pa.PhoneAutomationError) as excinfo:
            pa.start_ai_call(
                db, org_id=ORG_ID, call_sid="CA-tw284-2", from_number=CALLER,
                telephony=tel, voice_agent=agent, now=_at(11),
            )
    assert excinfo.value.status == 409
    assert agent.utterances == []


# ---------------------------------------------------------------------------
# Recording: per-call toggle, spoken announcement, audit log
# ---------------------------------------------------------------------------

def _started_session(world):
    db, tel, agent, events = world
    with _global_on():
        session = pa.start_ai_call(
            db, org_id=ORG_ID, call_sid="CA-tw284-rec", from_number=CALLER,
            telephony=tel, voice_agent=agent, now=_at(22),
        )
    return db, tel, agent, events, session


def test_recording_starts_off_by_default(world):
    *_, session = _started_session(world)
    assert session["recording_enabled"] is False


def test_recording_toggle_on_speaks_announcement_before_recording_starts(world):
    db, tel, agent, events, session = _started_session(world)
    events.clear()
    pa.set_recording(
        db, org_id=ORG_ID, session_id=session["id"], on=True,
        actor="owner", telephony=tel, voice_agent=agent, now=_at(22, 5),
    )
    kinds = [k for k, _ in events]
    assert kinds == ["speak", "recording_start"]
    spoken = events[0][1].lower().strip(".")
    assert spoken == "this call is now being recorded"
    row = [s for s in db._tables["ai_call_sessions"]
           if s["id"] == session["id"]][0]
    assert row["recording_enabled"] is True
    audit = db._tables["call_recording_events"]
    assert len(audit) == 1
    assert audit[0]["event"] == "recording_enabled"
    assert audit[0]["announcement_text"].lower().strip(".") == \
        "this call is now being recorded"
    assert audit[0]["actor"] == "owner"


def test_recording_toggle_off_stops_and_audits_without_announcement(world):
    db, tel, agent, events, session = _started_session(world)
    pa.set_recording(
        db, org_id=ORG_ID, session_id=session["id"], on=True,
        actor="owner", telephony=tel, voice_agent=agent, now=_at(22, 5),
    )
    events.clear()
    utterances_before = len(agent.utterances)
    pa.set_recording(
        db, org_id=ORG_ID, session_id=session["id"], on=False,
        actor="owner", telephony=tel, voice_agent=agent, now=_at(22, 9),
    )
    assert [k for k, _ in events] == ["recording_stop"]
    assert len(agent.utterances) == utterances_before  # no announcement on off
    audit = db._tables["call_recording_events"]
    assert [a["event"] for a in audit] == [
        "recording_enabled", "recording_disabled"]
    assert audit[1]["announcement_text"] is None


def test_recording_toggle_idempotent_when_already_on(world):
    db, tel, agent, events, session = _started_session(world)
    pa.set_recording(
        db, org_id=ORG_ID, session_id=session["id"], on=True,
        actor="owner", telephony=tel, voice_agent=agent, now=_at(22, 5),
    )
    events.clear()
    pa.set_recording(
        db, org_id=ORG_ID, session_id=session["id"], on=True,
        actor="owner", telephony=tel, voice_agent=agent, now=_at(22, 6),
    )
    assert events == []  # no second announcement, no duplicate audit
    assert len(db._tables["call_recording_events"]) == 1


# ---------------------------------------------------------------------------
# Emergency contacts: multiple numbers, priority order, masked output
# ---------------------------------------------------------------------------

AMBER = "+13035550101"
MARCUS_LINE = "+13035550102"
SHOP_LINE = "+13035550103"


def test_replace_and_list_contacts_masked_and_ordered(world):
    db, tel, agent, _ = world
    pa.replace_emergency_contacts(db, ORG_ID, [
        {"phone_number": SHOP_LINE, "label": "Shop line", "priority": 3},
        {"phone_number": AMBER, "label": "Amber (owner)", "priority": 1},
        {"phone_number": MARCUS_LINE, "label": "Office", "priority": 2},
    ])
    listed = pa.list_emergency_contacts(db, ORG_ID)
    assert [c["label"] for c in listed] == [
        "Amber (owner)", "Office", "Shop line"]
    for c in listed:
        assert AMBER not in str(c.values())
        assert c["phone_masked"].endswith("0101") or \
            c["phone_masked"].endswith("0102") or \
            c["phone_masked"].endswith("0103")


# ---------------------------------------------------------------------------
# Escalation: priority order, stop on ack, bounded exhaustion fallback
# ---------------------------------------------------------------------------

def _seed_contacts(db):
    pa.replace_emergency_contacts(db, ORG_ID, [
        {"phone_number": AMBER, "label": "Amber (owner)", "priority": 1},
        {"phone_number": MARCUS_LINE, "label": "Office", "priority": 2},
        {"phone_number": SHOP_LINE, "label": "Shop line", "priority": 3},
    ])


def test_escalation_attempts_in_priority_order_and_stops_on_ack(world):
    db, tel, agent, _ = world
    _seed_contacts(db)
    acked = {"done": False}

    def probe(contact):
        if contact["phone_number"] == MARCUS_LINE:
            acked["done"] = True
            return True
        return False

    chain = pa.start_escalation(
        db, org_id=ORG_ID, session_id=None,
        message="Emergency: no heat, booked 11pm",
        telephony=tel, ack_probe=probe, now=_at(22, 15),
    )
    assert chain["status"] == "acknowledged"
    assert acked["done"] is True
    called = [to for to, _ in tel.calls]
    assert called == [AMBER, MARCUS_LINE]  # priority order, stops at ack
    events = db._tables["escalation_events"]
    assert events[-1]["status"] == "acknowledged"


def test_escalation_unacknowledged_exhausts_bounded_rounds(world):
    db, tel, agent, _ = world
    _seed_contacts(db)
    chain = pa.start_escalation(
        db, org_id=ORG_ID, session_id=None,
        message="Emergency: burst pipe",
        telephony=tel, ack_probe=lambda contact: False,
        max_rounds=2, now=_at(23, 40),
    )
    assert chain["status"] == "exhausted"
    # Bounded: 3 contacts x 2 rounds, never more.
    assert len(tel.calls) == 6
    events = db._tables["escalation_events"]
    assert events[-1]["status"] == "exhausted"
    assert events[-1]["contact_id"] is None  # final fallback marker


def test_acknowledge_escalation_marks_chain(world):
    db, tel, agent, _ = world
    _seed_contacts(db)
    chain = pa.start_escalation(
        db, org_id=ORG_ID, session_id=None, message="Emergency",
        telephony=tel, ack_probe=lambda contact: False,
        max_rounds=1, now=_at(23, 50),
    )
    contacts = pa._active_contacts(db, ORG_ID)
    updated = pa.acknowledge_escalation(
        db, chain_id=chain["id"], contact_id=contacts[0]["id"],
        now=_at(23, 55),
    )
    assert updated["status"] == "acknowledged"


# ---------------------------------------------------------------------------
# Emergency intake -> docket booking
# ---------------------------------------------------------------------------

def test_emergency_intake_books_job_and_appointment_and_confirms(world):
    db, tel, agent, events, session = _started_session(world)
    _seed_contacts(db)
    result = pa.record_emergency_intake(
        db, org_id=ORG_ID, session_id=session["id"],
        details={
            "caller_name": "Amber's neighbor",
            "phone": CALLER,
            "issue": "No heat, house at 55 degrees",
            "address": "123 Test St, Denver",
            "requested_at": _at(23).astimezone(timezone.utc).isoformat(),
        },
        telephony=tel, ack_probe=lambda contact: True, now=_at(22, 20),
    )
    job = db._tables["jobs"][0]
    assert job["job_type"] == "emergency"
    assert job["status"] == "scheduled"
    appt = db._tables["appointments"][0]
    assert appt["job_id"] == job["id"]
    assert result["appointment_id"] == appt["id"]
    row = [s for s in db._tables["ai_call_sessions"]
           if s["id"] == session["id"]][0]
    assert row["appointment_id"] == appt["id"]
    # Customer got a confirmation; the owner got rung (escalation started).
    assert any(to == CALLER for to, _ in tel.sms)
    assert tel.calls, "escalation never rang the emergency contacts"


# ---------------------------------------------------------------------------
# Auto-reschedule: next free slot + customer told why
# ---------------------------------------------------------------------------

def _seed_appointment(db, *, appt_id, scheduled_at, technician_id=None,
                      customer_phone=CALLER):
    db._tables["customers"].append({
        "id": f"cust-{appt_id}", "org_id": ORG_ID,
        "name": "Test Customer", "phone": customer_phone,
    })
    db._tables["appointments"].append({
        "id": appt_id, "org_id": ORG_ID, "job_id": None,
        "customer_id": f"cust-{appt_id}",
        "technician_id": technician_id,
        "visit_type": "job_visit",
        "scheduled_at": scheduled_at, "no_show": False, "notes": None,
    })


def test_auto_reschedule_no_show_moves_and_tells_customer_why(world):
    db, tel, agent, _ = world
    tue_9am = _at(9).astimezone(timezone.utc).isoformat()
    tue_930 = _at(9, 30).astimezone(timezone.utc).isoformat()
    _seed_appointment(db, appt_id="appt-a", scheduled_at=tue_9am)
    _seed_appointment(db, appt_id="appt-busy", scheduled_at=tue_930)
    result = pa.auto_reschedule_appointment(
        db, org_id=ORG_ID, appointment_id="appt-a", reason="no_show",
        telephony=tel, now=_at(8),
    )
    # 09:30 is taken, so the next free slot is 10:00 Denver.
    new_local = datetime.fromisoformat(
        result["new_scheduled_at"]).astimezone(DENVER)
    assert (new_local.hour, new_local.minute) == (10, 0)
    row = [a for a in db._tables["appointments"] if a["id"] == "appt-a"][0]
    assert row["scheduled_at"] == result["new_scheduled_at"]
    sms = [body for to, body in tel.sms if to == CALLER]
    assert sms, "customer was never told"
    assert "missed you" in sms[0].lower()
    assert "10:00" in sms[0]


def test_auto_reschedule_double_booked_detects_conflict(world):
    db, tel, agent, _ = world
    tue_9am = _at(9).astimezone(timezone.utc).isoformat()
    _seed_appointment(db, appt_id="appt-a", scheduled_at=tue_9am,
                      technician_id="tech-1")
    _seed_appointment(db, appt_id="appt-clash", scheduled_at=tue_9am,
                      technician_id="tech-1")
    result = pa.auto_reschedule_appointment(
        db, org_id=ORG_ID, appointment_id="appt-a", reason="double_booked",
        telephony=tel, now=_at(8),
    )
    new_local = datetime.fromisoformat(
        result["new_scheduled_at"]).astimezone(DENVER)
    assert (new_local.hour, new_local.minute) == (9, 30)
    sms = [body for to, body in tel.sms if to == CALLER]
    assert sms and "scheduling conflict" in sms[0].lower()


def test_auto_reschedule_rejects_unknown_reason(world):
    db, tel, agent, _ = world
    tue_9am = _at(9).astimezone(timezone.utc).isoformat()
    _seed_appointment(db, appt_id="appt-a", scheduled_at=tue_9am)
    with pytest.raises(pa.PhoneAutomationError) as excinfo:
        pa.auto_reschedule_appointment(
            db, org_id=ORG_ID, appointment_id="appt-a", reason="vibes",
            telephony=tel, now=_at(8),
        )
    assert excinfo.value.status == 400


# ---------------------------------------------------------------------------
# Migration: tables, RLS, org isolation (static contract, house pattern)
# ---------------------------------------------------------------------------

def test_migration_creates_tables_with_rls_org_isolation():
    sql = (
        Path(__file__).resolve().parents[2]
        / "supabase" / "migration_phone_automation.sql"
    ).read_text()
    for table in (
        "phone_automation_settings",
        "emergency_contacts",
        "ai_call_sessions",
        "call_recording_events",
        "escalation_chains",
        "escalation_events",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, table
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql, table
        assert f'"{table}_org_isolation"' in sql, table
