"""TW-884 REX RETEST ROUND 2: sign-off attacks on the fix round.

Rex Ryder, post-patch re-verification of TW-888 / TW-889 / TW-890
(branch zeke/tw-884-outbound-calls @ 815ac5d). Round 1 (44 vectors)
proved the slice-1 build broken in three places; this round attacks
the FIXES, harder than the original repros:

- TW-888: the compensation path itself. Revocation landing inside
  create_event is only half the race — what happens when the void
  FAILS (delete raises), LIES (delete returns False, event stays),
  or DOESN'T EXIST (provider with no delete_event)? A phantom event
  that survives AND leaves no trace in the audit trail is the
  original finding wearing a new coat.
- TW-889: the global kill switch flipped at every seam of the close:
  with the final yes, during the last audit answer, inside slot
  search, inside create_event, and on the review-only booking path.
- TW-890: the new consent-capture route as a laundering path. An
  unverified landline label must reach a dial ONLY through a PEWC
  record that exactly names Twistor + AI-voice scope for THIS org
  and THIS number, not revoked, not suppressed.

A passing test = KILL CONFIRMED. A failing test = the finding stands.
Harness fakes are copied per house pattern; only the service under
test is imported.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.services import outbound_calls as oc

DENVER = ZoneInfo("America/Denver")
ORG = "org-rex2-a"
ORG2 = "org-rex2-b"
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


class FakeCalendar:
    """Working calendar: create/delete behave (the control)."""

    def __init__(self):
        self.events = []
        self.create_calls = 0
        self.delete_calls = []

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
        self.create_calls += 1
        return event

    def delete_event(self, event_id):
        self.delete_calls.append(event_id)
        before = len(self.events)
        self.events = [e for e in self.events if e["id"] != event_id]
        return len(self.events) < before


class RaceCalendar(FakeCalendar):
    """create_event lands the event, THEN fires a side effect (the
    revocation / kill flip becomes visible only after the booking
    exists) — the create-time race, with a configurable delete:

    delete_mode: "ok" (control) | "raise" (provider 500s on delete)
    | "false" (provider reports nothing removed, event stays)
    """

    def __init__(self, on_create=None, delete_mode="ok"):
        super().__init__()
        self.on_create = on_create
        self.delete_mode = delete_mode

    def create_event(self, **kwargs):
        event = super().create_event(**kwargs)
        if self.on_create is not None:
            self.on_create()
        return event

    def delete_event(self, event_id):
        self.delete_calls.append(event_id)
        if self.delete_mode == "raise":
            raise RuntimeError("calendar provider 500 during delete")
        if self.delete_mode == "false":
            return False
        before = len(self.events)
        self.events = [e for e in self.events if e["id"] != event_id]
        return len(self.events) < before


class NoDeleteCalendar:
    """A calendar adapter with NO delete_event at all — the shape a
    pre-TW-888 third-party adapter would have. Duck-typed: the
    service must not silently strand a booking on it."""

    def __init__(self, on_create=None):
        self.events = []
        self.on_create = on_create
        self.create_calls = 0

    def list_events(self, start, end):
        return [e for e in self.events if e["start"] < end and start < e["end"]]

    def create_event(self, *, title, start, end, description, location=None):
        event = {
            "id": f"evt-{len(self.events) + 1}",
            "title": title,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
        }
        self.events.append(event)
        self.create_calls += 1
        if self.on_create is not None:
            self.on_create()
        return event


class KillDuringSearchCalendar(FakeCalendar):
    """Flips the GLOBAL kill switch during slot search (list_events),
    before any create: the booking must die in _find_demo_slot."""

    def list_events(self, start, end):
        settings.outbound_calls_enabled = False
        return super().list_events(start, end)


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
HAPPY = [*TO_CLOSE, "Yeah, book it."]


def last_attempt(db):
    return db.tables["outbound_call_attempts"][-1]


def orphan_recorded(attempt, event_id):
    """Does the audit trail identify the surviving event anywhere?
    (An orphan nobody can find is a phantom; an orphan carrying its
    event id is a cleanup work-order.)"""
    answers = attempt.get("audit_answers") or {}
    if attempt.get("demo_event_id") == event_id:
        return True
    return any(event_id in str(v) for v in answers.values())


def assert_no_silent_phantom(db, cal):
    """The TW-888 invariant, compensation-failure edition: after an
    aborted call, either the calendar holds nothing, or the attempt
    row identifies the surviving event — and the trail never claims
    a void that did not happen."""
    attempt = last_attempt(db)
    assert attempt["disposition"] == "aborted"
    assert attempt["demo_event_id"] is None or cal.events == []
    if cal.events:
        surviving = cal.events[0]["id"]
        answers = attempt.get("audit_answers") or {}
        assert answers.get("booking_voided") is not True, (
            "audit trail claims booking_voided while the event lives"
        )
        assert orphan_recorded(attempt, surviving), (
            "phantom booking survived with NO trace in the audit trail"
        )


# ---------------------------------------------------------------------------
# R2-A: TW-888 — the compensation path under fire
# ---------------------------------------------------------------------------


def test_race_control_revoke_in_create_working_delete_voids():
    """Control: revocation lands inside create_event; a working
    delete must void the event and the trail must say so."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    voice = FakeVoiceAgent()
    cal = RaceCalendar(on_create=lambda: oc.revoke_agent(db, ORG, now=BASE_NOW))
    result = dial(db, p, ScriptedResponder(HAPPY), voice_agent=voice, calendar=cal)
    assert result["status"] == "aborted"
    assert cal.create_calls == 1  # the race really happened
    assert cal.events == [], "created-then-revoked event survived"
    assert cal.delete_calls, "compensation never attempted the delete"
    assert oc.BOOKED_LINE not in voice.spoken
    attempt = last_attempt(db)
    assert attempt["demo_event_id"] is None
    assert (attempt["audit_answers"] or {}).get("booking_voided") is True


def test_race_delete_raises_orphan_must_be_recorded():
    """The provider 500s during the compensating delete. The event
    physically survives — that part is the provider's fault. What
    must NOT happen: the abort completes with the phantom invisible
    (no event id, no failure marker anywhere in the trail)."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    cal = RaceCalendar(
        on_create=lambda: oc.revoke_agent(db, ORG, now=BASE_NOW),
        delete_mode="raise",
    )
    result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert result["status"] == "aborted"
    assert cal.create_calls == 1
    assert_no_silent_phantom(db, cal)


def test_race_delete_returns_false_must_not_claim_voided():
    """The provider's delete reports 'nothing removed' (returns
    False, per the port contract 'True when one was removed') and
    the event stays. The trail must not claim booking_voided for a
    void that never happened."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    cal = RaceCalendar(
        on_create=lambda: oc.revoke_agent(db, ORG, now=BASE_NOW),
        delete_mode="false",
    )
    result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert result["status"] == "aborted"
    assert cal.events, "test premise broken: the false-delete must keep the event"
    assert_no_silent_phantom(db, cal)


def test_race_calendar_without_delete_event_must_not_strand_silently():
    """A calendar adapter with no delete_event at all (the pre-TW-888
    adapter shape). Booking into the race must not strand a live
    event with zero trace."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    cal = NoDeleteCalendar(on_create=lambda: oc.revoke_agent(db, ORG, now=BASE_NOW))
    result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert result["status"] == "aborted"
    assert cal.create_calls == 1
    assert_no_silent_phantom(db, cal)


# ---------------------------------------------------------------------------
# R2-B: TW-889 — global kill switch flipped at every seam of the close
# ---------------------------------------------------------------------------


def _kill():
    settings.outbound_calls_enabled = False


def test_kill_flip_with_final_yes_books_nothing():
    """The flip and the prospect's 'yes' arrive in the same breath:
    book()'s pre-create liveness check must refuse the booking."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    voice = FakeVoiceAgent()
    cal = FakeCalendar()

    def kill_then_yes(_history):
        _kill()
        return "Yeah, book it."

    result = dial(
        db,
        p,
        ScriptedResponder([*TO_CLOSE, kill_then_yes]),
        voice_agent=voice,
        calendar=cal,
    )
    assert result["status"] == "aborted"
    assert cal.events == []
    assert cal.create_calls == 0
    assert oc.BOOKED_LINE not in voice.spoken


def test_kill_flip_during_last_audit_answer_stops_the_close():
    """Flip lands while the prospect answers audit Q2 — the close
    pitch itself is the next utterance and must never fire."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    voice = FakeVoiceAgent()
    cal = FakeCalendar()

    def kill_then_answer(_history):
        _kill()
        return "Maybe 2 get a call back within the hour."

    script = [*TO_CLOSE[:-1], kill_then_answer, "Yeah, book it."]
    result = dial(db, p, ScriptedResponder(script), voice_agent=voice, calendar=cal)
    assert result["status"] == "aborted"
    assert oc.CLOSE_LINE not in voice.spoken
    assert cal.events == []


def test_kill_flip_inside_create_event_compensates():
    """Flip lands inside create_event (after the event exists):
    the compensation must void it — the global brake gets the same
    TW-888 treatment as per-agent revocation."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    cal = RaceCalendar(on_create=_kill)
    result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert result["status"] == "aborted"
    assert cal.create_calls == 1
    assert cal.events == [], "kill-switch race left a phantom booking"
    attempt = last_attempt(db)
    assert attempt["demo_event_id"] is None
    assert (attempt["audit_answers"] or {}).get("booking_voided") is True


def test_kill_flip_during_slot_search_prevents_create():
    """Flip lands during the slot search, before any free slot is
    confirmed: require_alive must abort BEFORE create_event runs."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    cal = KillDuringSearchCalendar()
    result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert result["status"] == "aborted"
    assert cal.create_calls == 0, "create_event ran after the kill flip"
    assert cal.events == []


def test_kill_flip_on_review_only_booking_path():
    """The SEND_INFO path books a review-only audit through the same
    close_booking: a flip riding the 'yes' must book nothing."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    cal = FakeCalendar()

    def kill_then_yes(_history):
        _kill()
        return "Sure, the free review works."

    script = ["This is Kevin, speaking.", "Just send me some info.", kill_then_yes]
    result = dial(db, p, ScriptedResponder(script), calendar=cal)
    assert result["status"] == "aborted"
    assert cal.events == []
    assert cal.create_calls == 0


# ---------------------------------------------------------------------------
# R2-C: TW-890 — consent-capture route must not launder unverified lines
# ---------------------------------------------------------------------------


def _unverified(db, prospect, responder, **kw):
    kw.setdefault(
        "line_types",
        FakeLineTypes(
            {
                prospect["phone"]: {
                    "line_type": "business_landline",
                    "carrier": "TestTel",
                    "verified": False,
                }
            }
        ),
    )
    return dial(db, prospect, responder, **kw)


def test_unverified_no_consent_stays_undialed_control():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    result = _unverified(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert tel.calls == []
    assert result["status"] == "consent_required"
    assert db.tables["outbound_prospects"][0]["lane"] == "consent_capture"
    assert last_attempt(db)["dialed"] is False


def test_unverified_valid_pewc_dials_control():
    """Control: the gate is the consent, not a blanket block — a
    valid Twistor + AI-voice PEWC makes the number callable."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    seed_consent(db, LANDLINE)
    tel = FakeTelephony()
    result = _unverified(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert len(tel.calls) == 1
    assert result["status"] == "completed"


@pytest.mark.parametrize(
    "seller", ["twistor", "Twistor ", "Twistor Holdings", "Twistor LLC"]
)
def test_unverified_consent_seller_near_misses_refuse(seller):
    """PEWC must name Twistor (or Twistor Holdings LLC) EXACTLY.
    Case, padding, and near-name variants are not consent."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    seed_consent(db, LANDLINE, seller_name=seller)
    tel = FakeTelephony()
    result = _unverified(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert tel.calls == [], f"seller {seller!r} unlocked a dial"
    assert result["status"] == "consent_required"


def test_unverified_consent_without_ai_voice_scope_refuses():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    seed_consent(db, LANDLINE, scope_ai_voice=False)
    tel = FakeTelephony()
    result = _unverified(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert tel.calls == []
    assert result["status"] == "consent_required"


def test_unverified_consent_revoked_refuses():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    seed_consent(db, LANDLINE, revoked_at=BASE_NOW.isoformat())
    tel = FakeTelephony()
    result = _unverified(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert tel.calls == []
    assert result["status"] == "consent_required"


def test_unverified_consent_from_other_org_refuses():
    db = FakeDB()
    seed_agent(db)
    seed_agent(db, org=ORG2)
    p = seed_prospect(db)
    seed_consent(db, LANDLINE, org=ORG2)
    tel = FakeTelephony()
    result = _unverified(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert tel.calls == []
    assert result["status"] == "consent_required"


def test_unverified_valid_consent_but_suppressed_refuses():
    """Consent never outranks suppression: a DNC'd number with a
    valid PEWC on file is still undialable (HR-08/HR-09 sit above
    the line-type gate in the chain)."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    seed_consent(db, LANDLINE)
    oc.record_suppression(db, ORG, LANDLINE, source="dnc_request", now=BASE_NOW)
    tel = FakeTelephony()
    result = _unverified(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert tel.calls == []
    assert result["status"] == "skipped_dnc"


def test_unverified_consent_hash_of_raw_format_refuses():
    """Consent recorded against the number as the human wrote it
    ('(303) 555-0101') hashes differently from the E.164 prospect
    number. Matching is hash-exact or it is nothing: no dial."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    seed_consent(db, "(303) 555-0101")
    tel = FakeTelephony()
    result = _unverified(db, p, ScriptedResponder(HAPPY), telephony=tel)
    assert tel.calls == []
    assert result["status"] == "consent_required"


@pytest.mark.parametrize("bogus_verified", [1, "true", "yes"])
def test_verified_truthy_non_true_fails_closed(bogus_verified):
    """The verified check is `is True`, not truthiness: a carrier
    payload carrying 1 / 'true' / 'yes' is NOT verification."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    types = FakeLineTypes(
        {
            LANDLINE: {
                "line_type": "business_landline",
                "carrier": "TestTel",
                "verified": bogus_verified,
            }
        }
    )
    result = dial(db, p, ScriptedResponder(HAPPY), telephony=tel, line_types=types)
    assert tel.calls == [], f"verified={bogus_verified!r} unlocked a cold dial"
    assert result["status"] == "consent_required"


@pytest.mark.parametrize("spoofed_type", ["Business_Landline", "business_landline "])
def test_line_type_case_and_padding_spoofs_refuse(spoofed_type):
    """Carrier labels are matched exactly: a capitalized or padded
    'business_landline' with verified=True is not on the cold-dial
    allowlist and must fall closed, never dial."""
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    tel = FakeTelephony()
    types = FakeLineTypes(
        {
            LANDLINE: {
                "line_type": spoofed_type,
                "carrier": "TestTel",
                "verified": True,
            }
        }
    )
    result = dial(db, p, ScriptedResponder(HAPPY), telephony=tel, line_types=types)
    assert tel.calls == [], f"line_type {spoofed_type!r} unlocked a cold dial"
    assert result["status"] != "completed"
