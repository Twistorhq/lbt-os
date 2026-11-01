"""Rex retest round 3 — TW-891 re-verification, past the acceptance tests.

Round 2 found the compensation's failure paths were silent
(TW-891); the fix (RED 0d93ec7 -> GREEN 1595f5b) claims: only
delete_event(...) is True counts as voided, failed voids record
booking_void_failed + orphaned_event_id on the attempt row, and
daily_rollup surfaces orphaned_events {count, event_ids}.

These probes go past Zeke's acceptance surface:
P1  truthy-but-not-True delete returns (1, "ok", a list, an object)
    must NOT count as voided — the strict `is True` must hold
    against provider responses a truthiness check would misread.
P2  the orphan record lives on the attempt ROW itself (discoverable
    even if no rollup ever runs), and rollup surfacing is scoped
    by day AND by org.
P3  a second call to the same prospect after a failed void: the
    orphan stays put and stays listed, the new booking lands
    alongside it, never over it, and never claims it.
P4  compensation attempted twice is idempotent: exactly one delete
    call, one stable record, no exception, no flip — for the
    failed-void, confirmed-void, and raising-delete shapes.
P5  two orphans on the same day both surface, each exactly once.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_outbound_rex_retest2 import (
    BASE_NOW,
    HAPPY,
    LANDLINE,
    LANDLINE_2,
    ORG,
    ORG2,
    WIRELESS,
    FakeDB,
    RaceCalendar,
    ScriptedResponder,
    dial,
    last_attempt,
    oc,
    seed_agent,
    seed_prospect,
)

from app.config import settings


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    """Same settings bootstrap as the round-2 suite (an imported
    autouse fixture does not register in this module, so it is
    declared here)."""
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


class TruthyDeleteCalendar(RaceCalendar):
    """delete_event returns a truthy value that is NOT True and does
    not remove the event — a provider 'ok-ish' response that a
    truthiness check would misread as a confirmed void."""

    def __init__(self, on_create=None, truthy=None):
        super().__init__(on_create=on_create, delete_mode="ok")
        self.truthy = truthy

    def delete_event(self, event_id):
        self.delete_calls.append(event_id)
        return self.truthy


# ---------------------------------------------------------------------------
# P1: truthy non-True delete returns are failed voids
# ---------------------------------------------------------------------------


def test_p1_truthy_non_true_delete_is_a_failed_void():
    for truthy in (1, "ok", ["evt-1"], object()):
        db = FakeDB()
        seed_agent(db)
        p = seed_prospect(db)
        cal = TruthyDeleteCalendar(
            on_create=lambda: oc.revoke_agent(db, ORG, now=BASE_NOW),
            truthy=truthy,
        )
        result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
        assert result["status"] == "aborted"
        assert cal.events, f"premise broken for {truthy!r}: event should survive"
        answers = last_attempt(db).get("audit_answers") or {}
        assert answers.get("booking_voided") is not True, (
            f"delete returning {truthy!r} was counted as a void"
        )
        assert answers.get("booking_void_failed") is True
        assert answers.get("orphaned_event_id") == cal.events[0]["id"]


# ---------------------------------------------------------------------------
# P2: orphan recorded on the row; rollup scoping by day and org
# ---------------------------------------------------------------------------


def test_p2_orphan_on_row_and_rollup_scoping():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    cal = RaceCalendar(
        on_create=lambda: oc.revoke_agent(db, ORG, now=BASE_NOW),
        delete_mode="raise",
    )
    result = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert result["status"] == "aborted"
    orphan_id = cal.events[0]["id"]
    # The raw attempt row, read directly — no rollup involved yet.
    # If the rollup read path fails or never runs, the orphan is
    # still discoverable here.
    row = db.tables["outbound_call_attempts"][-1]
    answers = row.get("audit_answers") or {}
    assert answers.get("booking_void_failed") is True
    assert answers.get("orphaned_event_id") == orphan_id
    assert answers.get("booking_voided") is not True
    # Same org, wrong day: not surfaced.
    other_day = oc.daily_rollup(db, ORG, BASE_NOW.date() + timedelta(days=1))
    assert other_day["orphaned_events"] == {"count": 0, "event_ids": []}
    # Other org, right day: not surfaced — org isolation on the new
    # surface (another tenant's cleanup list must not leak ours).
    seed_agent(db, org=ORG2)
    other_org = oc.daily_rollup(db, ORG2, BASE_NOW.date())
    assert other_org["orphaned_events"] == {"count": 0, "event_ids": []}
    # Owning org, right day: surfaced exactly once.
    mine = oc.daily_rollup(db, ORG, BASE_NOW.date())
    assert mine["orphaned_events"] == {"count": 1, "event_ids": [orphan_id]}


# ---------------------------------------------------------------------------
# P3: second call to the same prospect after a failed void
# ---------------------------------------------------------------------------


def test_p3_second_call_after_failed_void():
    db = FakeDB()
    seed_agent(db)
    p = seed_prospect(db)
    cal = RaceCalendar(
        on_create=lambda: oc.revoke_agent(db, ORG, now=BASE_NOW),
        delete_mode="raise",
    )
    r1 = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
    assert r1["status"] == "aborted"
    orphan = cal.events[0]
    orphan_id = orphan["id"]

    # Later the same day the agent is live again (the round-2 race
    # revoked it; revive the row) and the calendar provider is
    # healthy. The second call must book ALONGSIDE the orphan.
    cal.on_create = None
    cal.delete_mode = "ok"
    row = db.tables["outbound_agent_settings"][-1]
    row["revoked_at"] = None
    row["enabled"] = True
    later = BASE_NOW + timedelta(hours=3, minutes=30)  # Mon 13:30 Denver
    r2 = dial(db, p, ScriptedResponder(HAPPY), calendar=cal, now=later)
    assert r2["status"] != "aborted", r2
    assert len(cal.events) == 2, "second call must book alongside, not over"
    new_event = [e for e in cal.events if e["id"] != orphan_id][0]
    assert not (
        new_event["start"] < orphan["end"] and orphan["start"] < new_event["end"]
    ), "double-booked over the orphaned event"
    a2 = last_attempt(db)
    assert a2.get("demo_event_id") == new_event["id"]
    answers2 = a2.get("audit_answers") or {}
    assert "booking_void_failed" not in answers2
    assert "orphaned_event_id" not in answers2
    # The orphan is still surfaced exactly once, unchanged.
    roll = oc.daily_rollup(db, ORG, BASE_NOW.date())
    assert roll["orphaned_events"] == {"count": 1, "event_ids": [orphan_id]}


# ---------------------------------------------------------------------------
# P4: compensation attempted twice — idempotent or noisy?
# ---------------------------------------------------------------------------


def _bare_conv(cal, event_id):
    conv = oc._Conversation.__new__(oc._Conversation)
    conv.demo_event_id = event_id
    conv._booked_calendar = cal
    conv.answers = {}
    return conv


def _seeded_cal(delete_mode):
    cal = RaceCalendar(delete_mode=delete_mode)
    cal.events.append(
        {
            "id": "evt-9",
            "title": "t",
            "start": BASE_NOW,
            "end": BASE_NOW,
            "description": "",
        }
    )
    return cal


def test_p4_double_compensation_failed_void_is_idempotent():
    cal = _seeded_cal("false")
    conv = _bare_conv(cal, "evt-9")
    conv._void_booking()
    conv._void_booking()
    assert cal.delete_calls == ["evt-9"], "second compensation re-deleted"
    assert conv.answers == {
        "booking_void_failed": True,
        "orphaned_event_id": "evt-9",
    }


def test_p4_double_compensation_confirmed_void_is_idempotent():
    cal = _seeded_cal("ok")
    conv = _bare_conv(cal, "evt-9")
    conv._void_booking()
    conv._void_booking()
    assert cal.delete_calls == ["evt-9"]
    assert cal.events == []
    assert conv.answers == {"booking_voided": True}


def test_p4_double_compensation_raising_delete_never_raises():
    cal = _seeded_cal("raise")
    conv = _bare_conv(cal, "evt-9")
    conv._void_booking()
    conv._void_booking()  # must not raise, must not flip the record
    assert conv.answers == {
        "booking_void_failed": True,
        "orphaned_event_id": "evt-9",
    }


# ---------------------------------------------------------------------------
# P5: two orphans, same day — both surface, each exactly once
# ---------------------------------------------------------------------------


class PrefixedRaceCalendar(RaceCalendar):
    """Namespaces minted event ids — real calendar providers return
    globally unique ids; two fresh fakes would both mint 'evt-1'
    and the probe would test the fake, not the rollup."""

    def __init__(self, *args, prefix="", **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix = prefix

    def create_event(self, **kwargs):
        event = super().create_event(**kwargs)
        event["id"] = f"{self.prefix}{event['id']}"
        return event


def test_p5_two_orphans_same_day_both_surface_once():
    db = FakeDB()
    seed_agent(db)
    ids = []
    for phone, prefix in ((LANDLINE, ""), (LANDLINE_2, "b-")):
        cal = PrefixedRaceCalendar(
            on_create=lambda: oc.revoke_agent(db, ORG, now=BASE_NOW),
            delete_mode="false",
            prefix=prefix,
        )
        p = seed_prospect(db, phone=phone)
        r = dial(db, p, ScriptedResponder(HAPPY), calendar=cal)
        assert r["status"] == "aborted"
        ids.append(cal.events[0]["id"])
        row = db.tables["outbound_agent_settings"][-1]
        row["revoked_at"] = None
        row["enabled"] = True
    roll = oc.daily_rollup(db, ORG, BASE_NOW.date())
    assert roll["orphaned_events"]["count"] == 2
    assert sorted(roll["orphaned_events"]["event_ids"]) == sorted(ids)
    assert len(set(roll["orphaned_events"]["event_ids"])) == 2
