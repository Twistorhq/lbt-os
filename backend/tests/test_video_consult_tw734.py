"""Tests for TW-734: video consult — the customer-facing consult a shop
sells (Zeke Okafor).

Justynn's 2026-10-28 refinement, pinned as behavior:
- A homeowner video-calls their HVAC/plumbing shop from the browser via
  a tokenized link (no app, no account) and talks to a real tech:
  show the furnace, the leak, the panel. DIYers get pro help without a
  truck roll; if video can't fix it, the consult converts to a booked
  visit with the problem already seen.
- The shop charges in one of two shop-configured modes: per-minute
  micro-tolled, or one flat rate. Twistor's toll runs on the M1 ledger
  in BOTH modes — exactly one toll per session.
- AI layer: triage before the tech (disclosure first), answering
  machine when no tech joins inside the shop's window (video message +
  local transcription + summary + owner escalation), recording
  announcement BEFORE any recording starts, per-session toggle.

Tenant boundary is tested FIRST and hardest: every org-scoped entry
point must 404 cross-org (never 403 — no org existence leak), the
Rosa M1 lesson from TW-284.
"""

import hashlib
from datetime import timedelta
from unittest import mock

import pytest
from test_phone_automation import (
    AMBER,
    CALLER,
    ORG_ID,
    FakeDB,
    FakeTelephony,
    FakeVoiceAgent,
    _at,
    _seed_contacts,
    _seed_shop,
)

from app.services import consult_providers as cp
from app.services import phone_automation as pa
from app.services import video_consult as vc

OTHER_ORG = "org-someone-else"
TECH = "tech-amber"

CONSULT_TABLES = {
    "video_consult_settings": [],
    "video_consult_sessions": [],
    "video_consult_messages": [],
    "video_consult_events": [],
}


def _seed_consult_settings(
    db,
    *,
    org_id=ORG_ID,
    enabled=True,
    pricing_mode="per_minute",
    per_minute_rate_cents=200,
    flat_rate_cents=4900,
    tech_join_window_seconds=120,
):
    db._tables["video_consult_settings"].append(
        {
            "org_id": org_id,
            "enabled": enabled,
            "pricing_mode": pricing_mode,
            "per_minute_rate_cents": per_minute_rate_cents,
            "flat_rate_cents": flat_rate_cents,
            "tech_join_window_seconds": tech_join_window_seconds,
        }
    )


class FakeRoomProvider:
    """The seam the production Jitsi room provider implements at
    go-live (meet.twistor.co, TW-729 package — not deployed)."""

    def __init__(self, events):
        self.events = events
        self.rooms = []

    def create_room(self, *, org_id, session_id):
        room_name = f"consult-{session_id}"
        self.rooms.append(room_name)
        self.events.append(("room_create", room_name))
        return {
            "room_name": room_name,
            "room_url": f"https://meet.twistor.co/{room_name}",
        }

    def start_recording(self, *, room_name):
        self.events.append(("recording_start", room_name))

    def stop_recording(self, *, room_name):
        self.events.append(("recording_stop", room_name))


class FakeTranscriber:
    def __init__(self, text="The furnace short-cycles every ten minutes."):
        self.text = text
        self.calls = []

    def transcribe(self, *, recording_ref):
        self.calls.append(recording_ref)
        return self.text


@pytest.fixture()
def world():
    db = FakeDB()
    db._tables.update({k: list(v) for k, v in CONSULT_TABLES.items()})
    _seed_shop(db)
    _seed_consult_settings(db)
    events = []
    tel = FakeTelephony(events)
    agent = FakeVoiceAgent(events)
    rooms = FakeRoomProvider(events)
    return db, tel, agent, rooms, events


def _global_on():
    return mock.patch.object(vc, "_global_enabled", lambda: True)


def _create(world, **kw):
    db, tel, agent, rooms, events = world
    with _global_on():
        return vc.create_session(
            db,
            org_id=ORG_ID,
            customer_name="Kreighton's neighbor",
            customer_phone=CALLER,
            issue="Furnace short-cycling",
            room_provider=rooms,
            voice_agent=agent,
            now=_at(10),
            **kw,
        )


def _to_live(world, created, *, join_at=None):
    db, tel, agent, rooms, events = world
    return vc.tech_join(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        tech_user_id=TECH,
        now=join_at or _at(10, 1),
    )


def _tolls(db):
    return [
        t for t in db._tables["toll_events"] if t.get("event_type") == "consult.session"
    ]


# ---------------------------------------------------------------------------
# Tenant boundary FIRST (Rosa will attack exactly this)
# ---------------------------------------------------------------------------


def test_tech_join_cross_org_is_404_and_writes_nothing(world):
    created = _create(world)
    db, *_ = world
    events_before = len(db._tables["video_consult_events"])
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.tech_join(
            db,
            org_id=OTHER_ORG,
            session_id=created["id"],
            tech_user_id="intruder",
            now=_at(10, 1),
        )
    assert excinfo.value.status == 404
    row = db._tables["video_consult_sessions"][0]
    assert row["status"] == "ai_triaging"
    assert row["tech_user_id"] is None
    assert len(db._tables["video_consult_events"]) == events_before


def test_get_session_cross_org_is_404(world):
    created = _create(world)
    db, *_ = world
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.get_session(db, org_id=OTHER_ORG, session_id=created["id"])
    assert excinfo.value.status == 404


def test_end_session_cross_org_is_404_and_does_not_meter(world):
    created = _create(world)
    _to_live(world, created)
    db, *_ = world
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.end_session(db, org_id=OTHER_ORG, session_id=created["id"], now=_at(10, 30))
    assert excinfo.value.status == 404
    assert db._tables["video_consult_sessions"][0]["status"] == "live"
    assert _tolls(db) == []


def test_convert_cross_org_is_404_and_books_nothing(world):
    created = _create(world)
    _to_live(world, created)
    db, tel, *_ = world
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.convert_to_visit(
            db,
            org_id=OTHER_ORG,
            session_id=created["id"],
            telephony=tel,
            now=_at(10, 30),
        )
    assert excinfo.value.status == 404
    assert db._tables["jobs"] == []
    assert db._tables["appointments"] == []


def test_set_recording_cross_org_is_404(world):
    created = _create(world)
    db, tel, agent, rooms, events = world
    events.clear()
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.set_recording(
            db,
            org_id=OTHER_ORG,
            session_id=created["id"],
            on=True,
            actor="owner",
            room_provider=rooms,
            voice_agent=agent,
            now=_at(10, 2),
        )
    assert excinfo.value.status == 404
    assert [k for k, _ in events if k == "recording_start"] == []


def test_settings_are_org_scoped(world):
    db, *_ = world
    _seed_consult_settings(
        db, org_id=OTHER_ORG, pricing_mode="flat", flat_rate_cents=9900
    )
    mine = vc.get_consult_settings(db, ORG_ID)
    theirs = vc.get_consult_settings(db, OTHER_ORG)
    assert mine["pricing_mode"] == "per_minute"
    assert theirs["pricing_mode"] == "flat"
    assert theirs["flat_rate_cents"] == 9900


def test_customer_join_rejects_unknown_token(world):
    _create(world)
    db, *_ = world
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.customer_join(db, token="not-a-real-token")
    assert excinfo.value.status == 404


def test_customer_token_is_stored_hashed_only(world):
    created = _create(world)
    db, *_ = world
    row = db._tables["video_consult_sessions"][0]
    raw = created["join_token"]
    assert raw not in row.values()
    assert row["access_token_hash"] == hashlib.sha256(raw.encode()).hexdigest()


def test_customer_join_with_token_returns_room_not_pii(world):
    created = _create(world)
    db, *_ = world
    view = vc.customer_join(db, token=created["join_token"])
    assert view["room_url"].startswith("https://meet.twistor.co/")
    assert view["status"] == "ai_triaging"
    assert "customer_phone" not in view
    assert "access_token_hash" not in view


# ---------------------------------------------------------------------------
# Creation + AI triage (disclosure first, gates fail closed)
# ---------------------------------------------------------------------------


def test_create_session_discloses_first_and_enters_triage(world):
    created = _create(world)
    db, tel, agent, rooms, events = world
    assert created["status"] == "ai_triaging"
    row = db._tables["video_consult_sessions"][0]
    assert row["ai_disclosure_given"] is True
    assert row["disclosure_at"] is not None
    assert row["recording_enabled"] is False
    first = agent.utterances[0].lower()
    assert "ai" in first and "automated" in first
    # Disclosure is spoken before the room is announced ready: the first
    # provider event of any kind is the disclosure utterance.
    assert events[0][0] == "speak"


def test_create_session_refused_when_shop_consult_disabled(world):
    db, tel, agent, rooms, events = world
    db._tables["video_consult_settings"][0]["enabled"] = False
    with _global_on():
        with pytest.raises(vc.VideoConsultError) as excinfo:
            vc.create_session(
                db,
                org_id=ORG_ID,
                customer_name="x",
                customer_phone=CALLER,
                issue="leak",
                room_provider=rooms,
                voice_agent=agent,
                now=_at(10),
            )
    assert excinfo.value.status == 409
    assert db._tables["video_consult_sessions"] == []


def test_create_session_refused_when_global_kill_switch_off(world):
    db, tel, agent, rooms, events = world
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.create_session(
            db,
            org_id=ORG_ID,
            customer_name="x",
            customer_phone=CALLER,
            issue="leak",
            room_provider=rooms,
            voice_agent=agent,
            now=_at(10),
        )
    assert excinfo.value.status == 409


def test_settings_validation_rejects_bad_mode_and_negative_rates(world):
    db, *_ = world
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.update_consult_settings(db, ORG_ID, pricing_mode="per_second")
    assert excinfo.value.status == 400
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.update_consult_settings(db, ORG_ID, per_minute_rate_cents=-5)
    assert excinfo.value.status == 400
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.update_consult_settings(db, ORG_ID, tech_join_window_seconds=5)
    assert excinfo.value.status == 400


# ---------------------------------------------------------------------------
# State machine: tech joins, no-join answering machine, conversion
# ---------------------------------------------------------------------------


def test_tech_join_moves_to_live_and_starts_billable_clock(world):
    created = _create(world)
    joined = _to_live(world, created, join_at=_at(10, 3))
    assert joined["status"] == "live"
    assert joined["tech_user_id"] == TECH
    assert joined["live_started_at"] == _at(10, 3).isoformat()


def test_tech_join_from_wrong_state_is_409(world):
    created = _create(world)
    _to_live(world, created)
    db, *_ = world
    vc.end_session(db, org_id=ORG_ID, session_id=created["id"], now=_at(10, 10))
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.tech_join(
            db,
            org_id=ORG_ID,
            session_id=created["id"],
            tech_user_id=TECH,
            now=_at(10, 11),
        )
    assert excinfo.value.status == 409


def test_no_tech_join_within_window_lands_in_answering_machine(world):
    created = _create(world)
    db, tel, agent, rooms, events = world
    _seed_contacts(db)
    row = db._tables["video_consult_sessions"][0]
    deadline = row["triage_deadline_at"]
    from datetime import datetime

    after = datetime.fromisoformat(deadline) + timedelta(seconds=1)
    result = vc.expire_triage(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        telephony=tel,
        ack_probe=lambda contact: False,
        now=after,
    )
    assert result["status"] == "message_left"
    # Owner chain was rung (ring + SMS per contact) and is visible.
    assert tel.calls, "owner chain never rang"
    assert result["escalation_chain_id"]
    chain = db._tables["escalation_chains"][0]
    assert chain["status"] == "exhausted"  # nobody acked: loud, not lost


def test_expire_triage_before_deadline_is_409(world):
    created = _create(world)
    db, tel, *_ = world
    with pytest.raises(vc.VideoConsultError) as excinfo:
        vc.expire_triage(
            db,
            org_id=ORG_ID,
            session_id=created["id"],
            telephony=tel,
            now=_at(10, 1),
        )
    assert excinfo.value.status == 409
    assert db._tables["video_consult_sessions"][0]["status"] == "ai_triaging"


def test_leave_message_transcribes_and_summarizes(world):
    created = _create(world)
    db, tel, agent, rooms, events = world
    _seed_contacts(db)
    from datetime import datetime

    deadline = datetime.fromisoformat(
        db._tables["video_consult_sessions"][0]["triage_deadline_at"]
    )
    vc.expire_triage(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        telephony=tel,
        ack_probe=lambda contact: True,
        now=deadline + timedelta(seconds=5),
    )
    transcriber = FakeTranscriber()
    msg = vc.leave_message(
        db,
        token=created["join_token"],
        recording_ref="storage://consult/msg-1.webm",
        transcriber=transcriber,
        now=deadline + timedelta(minutes=1),
    )
    assert msg["transcript"] == transcriber.text
    assert "short-cycles" in msg["summary"]
    assert "Furnace short-cycling" in msg["summary"]
    assert msg["transcription_status"] == "complete"
    assert db._tables["video_consult_messages"][0]["session_id"] == created["id"]


def test_leave_message_survives_unwired_transcriber(world):
    created = _create(world)
    db, *_ = world
    msg = vc.leave_message(
        db,
        token=created["join_token"],
        recording_ref="storage://consult/msg-2.webm",
        transcriber=cp.LocalWhisperTranscriber(),
        now=_at(10, 5),
    )
    assert msg["transcript"] is None
    assert msg["transcription_status"] == "pending"
    assert db._tables["video_consult_messages"][0]["recording_ref"].endswith("msg-2.webm")


def test_convert_from_live_books_visit_and_confirms(world):
    created = _create(world)
    _to_live(world, created)
    db, tel, *_ = world
    result = vc.convert_to_visit(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        telephony=tel,
        requested_at=_at(14).isoformat(),
        now=_at(10, 20),
    )
    assert result["status"] == "converted_to_visit"
    job = db._tables["jobs"][0]
    assert job["source"] == "video_consult"
    assert db._tables["appointments"][0]["job_id"] == job["id"]
    assert any(to == CALLER for to, _ in tel.sms)  # customer confirmation
    assert result["job_id"] == job["id"]


def test_convert_from_message_left_books_visit(world):
    created = _create(world)
    db, tel, *_ = world
    _seed_contacts(db)
    from datetime import datetime

    deadline = datetime.fromisoformat(
        db._tables["video_consult_sessions"][0]["triage_deadline_at"]
    )
    vc.expire_triage(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        telephony=tel,
        ack_probe=lambda contact: True,
        now=deadline + timedelta(seconds=5),
    )
    result = vc.convert_to_visit(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        telephony=tel,
        now=deadline + timedelta(minutes=30),
    )
    assert result["status"] == "converted_to_visit"
    assert db._tables["jobs"], "no visit booked from the message"


# ---------------------------------------------------------------------------
# Metering: per-minute and flat, exactly one toll either way
# ---------------------------------------------------------------------------


def test_per_minute_started_minute_rule_and_one_toll(world):
    created = _create(world)
    _to_live(world, created, join_at=_at(10, 1))
    db, *_ = world
    # 61 seconds of tech time bills 2 started minutes at $2.00/min.
    ended = vc.end_session(
        db, org_id=ORG_ID, session_id=created["id"], now=_at(10, 2, ) + timedelta(seconds=1)
    )
    assert ended["billable_minutes"] == 2
    assert ended["charge_cents"] == 400
    assert ended["charge_status"] == "receivable"
    tolls = _tolls(db)
    assert len(tolls) == 1
    assert tolls[0]["quantity"] == 2  # per-minute metering record
    assert tolls[0]["amount_cents"] == 10  # Twistor toll: 5c per billable minute
    assert tolls[0]["source_id"] == created["id"]


def test_per_minute_minimum_one_minute(world):
    created = _create(world)
    _to_live(world, created, join_at=_at(10, 1))
    db, *_ = world
    ended = vc.end_session(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        now=_at(10, 1) + timedelta(seconds=20),
    )
    assert ended["billable_minutes"] == 1
    assert ended["charge_cents"] == 200


def test_flat_mode_charges_flat_regardless_of_duration(world):
    db, tel, agent, rooms, events = world
    db._tables["video_consult_settings"][0]["pricing_mode"] = "flat"
    created = _create(world)
    _to_live(world, created, join_at=_at(10, 1))
    ended = vc.end_session(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        now=_at(10, 31),  # 30 minutes: still one flat charge
    )
    assert ended["charge_cents"] == 4900
    assert ended["charge_status"] == "receivable"
    tolls = _tolls(db)
    assert len(tolls) == 1
    assert tolls[0]["amount_cents"] == 50  # flat Twistor toll per session
    assert tolls[0]["quantity"] == 1


def test_flat_mode_short_call_still_flat(world):
    db, tel, agent, rooms, events = world
    db._tables["video_consult_settings"][0]["pricing_mode"] = "flat"
    created = _create(world)
    _to_live(world, created, join_at=_at(10, 1))
    ended = vc.end_session(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        now=_at(10, 1) + timedelta(seconds=30),
    )
    assert ended["charge_cents"] == 4900


def test_finalize_is_idempotent_end_then_convert_one_toll(world):
    created = _create(world)
    _to_live(world, created, join_at=_at(10, 1))
    db, tel, *_ = world
    vc.end_session(db, org_id=ORG_ID, session_id=created["id"], now=_at(10, 11))
    vc.convert_to_visit(
        db, org_id=ORG_ID, session_id=created["id"], telephony=tel, now=_at(10, 12)
    )
    assert len(_tolls(db)) == 1
    row = db._tables["video_consult_sessions"][0]
    assert row["charge_cents"] == 2000  # 10 started minutes, computed once


def test_message_left_charges_nothing_but_tolls_once(world):
    created = _create(world)
    db, tel, *_ = world
    _seed_contacts(db)
    from datetime import datetime

    deadline = datetime.fromisoformat(
        db._tables["video_consult_sessions"][0]["triage_deadline_at"]
    )
    vc.expire_triage(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        telephony=tel,
        ack_probe=lambda contact: True,
        now=deadline + timedelta(seconds=5),
    )
    row = db._tables["video_consult_sessions"][0]
    assert row["charge_cents"] == 0
    assert row["charge_status"] == "not_charged"
    tolls = _tolls(db)
    assert len(tolls) == 1  # the answering machine ran on Twistor's meter
    assert tolls[0]["amount_cents"] == 50


# ---------------------------------------------------------------------------
# Recording: announcement BEFORE recording, toggle respected
# ---------------------------------------------------------------------------


def test_recording_on_announces_before_recording_starts(world):
    created = _create(world)
    db, tel, agent, rooms, events = world
    events.clear()
    vc.set_recording(
        db,
        org_id=ORG_ID,
        session_id=created["id"],
        on=True,
        actor="tech",
        room_provider=rooms,
        voice_agent=agent,
        now=_at(10, 4),
    )
    kinds = [k for k, _ in events]
    assert kinds == ["speak", "recording_start"]
    spoken = events[0][1].lower().strip(".")
    assert spoken == "this call is now being recorded"
    audit = [
        e
        for e in db._tables["video_consult_events"]
        if e["event"] == "recording_enabled"
    ]
    assert len(audit) == 1
    assert audit[0]["announcement_text"] == pa.RECORDING_ANNOUNCEMENT


def test_recording_never_starts_implicitly(world):
    created = _create(world)
    _to_live(world, created)
    db, tel, agent, rooms, events = world
    vc.end_session(db, org_id=ORG_ID, session_id=created["id"], now=_at(10, 9))
    assert [k for k, _ in events if k == "recording_start"] == []
    row = db._tables["video_consult_sessions"][0]
    assert row["recording_enabled"] is False


def test_recording_toggle_idempotent(world):
    created = _create(world)
    db, tel, agent, rooms, events = world
    for minute in (4, 5):
        vc.set_recording(
            db,
            org_id=ORG_ID,
            session_id=created["id"],
            on=True,
            actor="tech",
            room_provider=rooms,
            voice_agent=agent,
            now=_at(10, minute),
        )
    audit = [
        e
        for e in db._tables["video_consult_events"]
        if e["event"] == "recording_enabled"
    ]
    assert len(audit) == 1


# ---------------------------------------------------------------------------
# Production providers fail closed (go-live wiring, house posture)
# ---------------------------------------------------------------------------


def test_jitsi_room_provider_fails_closed_without_base_url():
    with mock.patch.object(cp.settings, "video_consult_jitsi_base_url", None):
        provider = cp.JitsiRoomProvider()
        with pytest.raises(cp.ProviderNotConfigured):
            provider.create_room(org_id=ORG_ID, session_id="s-1")
        with pytest.raises(cp.ProviderNotConfigured):
            provider.start_recording(room_name="consult-s-1")


def test_local_whisper_transcriber_fails_closed_until_bridged():
    with pytest.raises(cp.ProviderNotConfigured):
        cp.LocalWhisperTranscriber().transcribe(recording_ref="storage://x.webm")


def test_room_provider_factory_fails_closed_without_base_url():
    with mock.patch.object(cp.settings, "video_consult_jitsi_base_url", None):
        with pytest.raises(cp.ProviderNotConfigured):
            cp.get_room_provider()
