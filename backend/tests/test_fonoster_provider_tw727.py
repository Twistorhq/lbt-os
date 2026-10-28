"""Tests for TW-727: Fonoster as the default TW-284 provider (Zeke Okafor).

Justynn's 2026-10-28 directive: Fonoster (MIT, self-hosted) replaces the
paid Twilio + OpenAI voice path as the DEFAULT provider for AI phone
automation. The Twilio/OpenAI factories stay selectable but deprecated.

These tests run every provider-touching TW-284 behavior through the
Fonoster provider classes backed by a fake Fonoster client (mirrors the
TW-284 suite's fake-injection pattern), plus the TW-727 contracts:
- Fonoster is the default provider selection; legacy is opt-in only.
- Fail closed everywhere: no credentials -> ProviderNotConfigured from
  the factories; no client/bridge wired -> ProviderNotConfigured from
  the provider methods. Never a silent live call.
- The Autopilot agent YAML carries the disclosure as its first message
  and the local-first think provider (Ollama).
- The self-host compose package declares the Fonoster services.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
import yaml
from test_phone_automation import (
    AMBER,
    CALLER,
    MARCUS_LINE,
    ORG_ID,
    OTHER_ORG,
    SHOP_NUMBER,
    FakeDB,
    _at,
    _global_on,
    _seed_appointment,
    _seed_contacts,
    _seed_shop,
)

from app.config import settings
from app.services import phone_automation as pa
from app.services import voice_providers as vp

DENVER = ZoneInfo("America/Denver")
CALL_REF = "FN-tw727-1"


# ---------------------------------------------------------------------------
# Fake Fonoster client — the seam the production bridge implements at
# go-live (Fonoster API server, gRPC; official SDK is Node-only).
# Records the same shared event kinds as the TW-284 fakes so ordering
# assertions ([speak, recording_start]) carry over unchanged.
# ---------------------------------------------------------------------------


class FakeFonosterClient:
    def __init__(self, events):
        self.events = events
        self.calls = []  # (from_number, to, say_text)
        self.messages = []  # (to, body)
        self.utterances = []

    def create_call(self, *, from_number, to, say_text):
        self.calls.append((from_number, to, say_text))
        self.events.append(("call", to))
        return f"FNfake{len(self.calls)}"

    def send_message(self, *, from_number, to, body):
        self.messages.append((to, body))
        self.events.append(("sms", to))

    def start_recording(self, *, call_ref):
        self.events.append(("recording_start", call_ref))

    def stop_recording(self, *, call_ref):
        self.events.append(("recording_stop", call_ref))

    def say(self, *, session_id, text):
        self.utterances.append(text)
        self.events.append(("speak", text))


@pytest.fixture()
def fworld():
    db = FakeDB()
    _seed_shop(db)
    events = []
    client = FakeFonosterClient(events)
    tel = vp.FonosterTelephonyProvider(client=client, from_number=SHOP_NUMBER)
    agent = vp.FonosterVoiceAgent(client=client)
    return db, tel, agent, client, events


def _started_fsession(fworld):
    db, tel, agent, client, events = fworld
    with _global_on():
        session = pa.start_ai_call(
            db,
            org_id=ORG_ID,
            call_sid=CALL_REF,
            from_number=CALLER,
            telephony=tel,
            voice_agent=agent,
            now=_at(22),
        )
    return db, tel, agent, client, events, session


# ---------------------------------------------------------------------------
# TW-284 behaviors, exercised through the Fonoster provider path
# ---------------------------------------------------------------------------


def test_fonoster_start_ai_call_speaks_disclosure_first_and_tolls_once(fworld):
    db, tel, agent, client, events, session = _started_fsession(fworld)
    assert session["ai_disclosure_given"] is True
    assert client.utterances, "agent said nothing"
    first = client.utterances[0].lower()
    assert "ai" in first and "automated" in first
    assert "Kreighton's Test HVAC" in client.utterances[0]
    tolls = [
        t for t in db._tables["toll_events"] if t.get("event_type") == "call.ai_handled"
    ]
    assert len(tolls) == 1  # exactly one toll per AI-handled call


def test_fonoster_start_ai_call_refused_when_answering_inactive(fworld):
    db, tel, agent, client, _ = fworld
    with _global_on():
        with pytest.raises(pa.PhoneAutomationError) as excinfo:
            pa.start_ai_call(
                db,
                org_id=ORG_ID,
                call_sid=CALL_REF,
                from_number=CALLER,
                telephony=tel,
                voice_agent=agent,
                now=_at(11),
            )
    assert excinfo.value.status == 409
    assert client.utterances == []
    assert client.calls == []


def test_fonoster_recording_starts_off_by_default(fworld):
    *_, session = _started_fsession(fworld)
    assert session["recording_enabled"] is False


def test_fonoster_recording_on_speaks_announcement_before_recording_starts(fworld):
    db, tel, agent, client, events, session = _started_fsession(fworld)
    events.clear()
    pa.set_recording(
        db,
        org_id=ORG_ID,
        session_id=session["id"],
        on=True,
        actor="owner",
        telephony=tel,
        voice_agent=agent,
        now=_at(22, 5),
    )
    kinds = [k for k, _ in events]
    assert kinds == ["speak", "recording_start"]
    spoken = events[0][1].lower().strip(".")
    assert spoken == "this call is now being recorded"
    audit = db._tables["call_recording_events"]
    assert len(audit) == 1
    assert audit[0]["event"] == "recording_enabled"
    assert (
        audit[0]["announcement_text"].lower().strip(".")
        == "this call is now being recorded"
    )


def test_fonoster_recording_off_stops_and_audits_without_announcement(fworld):
    db, tel, agent, client, events, session = _started_fsession(fworld)
    pa.set_recording(
        db,
        org_id=ORG_ID,
        session_id=session["id"],
        on=True,
        actor="owner",
        telephony=tel,
        voice_agent=agent,
        now=_at(22, 5),
    )
    events.clear()
    utterances_before = len(client.utterances)
    pa.set_recording(
        db,
        org_id=ORG_ID,
        session_id=session["id"],
        on=False,
        actor="owner",
        telephony=tel,
        voice_agent=agent,
        now=_at(22, 9),
    )
    assert [k for k, _ in events] == ["recording_stop"]
    assert len(client.utterances) == utterances_before
    audit = db._tables["call_recording_events"]
    assert [a["event"] for a in audit] == ["recording_enabled", "recording_disabled"]


def test_fonoster_recording_toggle_idempotent_when_already_on(fworld):
    db, tel, agent, client, events, session = _started_fsession(fworld)
    for minute in (5, 6):
        pa.set_recording(
            db,
            org_id=ORG_ID,
            session_id=session["id"],
            on=True,
            actor="owner",
            telephony=tel,
            voice_agent=agent,
            now=_at(22, minute),
        )
    assert len(db._tables["call_recording_events"]) == 1


def test_fonoster_escalation_priority_order_and_stops_on_ack(fworld):
    db, tel, agent, client, _ = fworld
    _seed_contacts(db)

    def probe(contact):
        return contact["phone_number"] == MARCUS_LINE

    chain = pa.start_escalation(
        db,
        org_id=ORG_ID,
        session_id=None,
        message="Emergency: no heat, booked 11pm",
        telephony=tel,
        ack_probe=probe,
        now=_at(22, 15),
    )
    assert chain["status"] == "acknowledged"
    called = [to for _from, to, _say in client.calls]
    assert called == [AMBER, MARCUS_LINE]  # priority order, stops at ack
    # Every attempt also sends a message through the Fonoster path.
    messaged = [to for to, _body in client.messages]
    assert messaged == [AMBER, MARCUS_LINE]
    assert db._tables["escalation_events"][-1]["status"] == "acknowledged"


def test_fonoster_escalation_unacknowledged_exhausts_bounded_rounds(fworld):
    db, tel, agent, client, _ = fworld
    _seed_contacts(db)
    chain = pa.start_escalation(
        db,
        org_id=ORG_ID,
        session_id=None,
        message="Emergency: burst pipe",
        telephony=tel,
        ack_probe=lambda contact: False,
        max_rounds=2,
        now=_at(23, 40),
    )
    assert chain["status"] == "exhausted"
    assert len(client.calls) == 6  # 3 contacts x 2 rounds, never more
    events = db._tables["escalation_events"]
    assert events[-1]["status"] == "exhausted"
    assert events[-1]["contact_id"] is None  # visible fallback marker


def test_fonoster_acknowledge_escalation_marks_chain(fworld):
    db, tel, agent, client, _ = fworld
    _seed_contacts(db)
    chain = pa.start_escalation(
        db,
        org_id=ORG_ID,
        session_id=None,
        message="Emergency",
        telephony=tel,
        ack_probe=lambda contact: False,
        max_rounds=1,
        now=_at(23, 50),
    )
    contact = pa._active_contacts(db, ORG_ID)[0]
    updated = pa.acknowledge_escalation(
        db,
        org_id=ORG_ID,
        chain_id=chain["id"],
        contact_id=contact["id"],
        now=_at(23, 55),
    )
    assert updated["status"] == "acknowledged"


def test_fonoster_acknowledge_cross_org_chain_is_404_and_writes_nothing(fworld):
    db, tel, agent, client, _ = fworld
    _seed_contacts(db)
    chain = pa.start_escalation(
        db,
        org_id=ORG_ID,
        session_id=None,
        message="Emergency",
        telephony=tel,
        ack_probe=lambda contact: False,
        max_rounds=1,
        now=_at(23, 50),
    )
    contact = pa._active_contacts(db, ORG_ID)[0]
    events_before = len(db._tables["escalation_events"])
    with pytest.raises(pa.PhoneAutomationError) as excinfo:
        pa.acknowledge_escalation(
            db,
            org_id=OTHER_ORG,
            chain_id=chain["id"],
            contact_id=contact["id"],
            now=_at(23, 55),
        )
    assert excinfo.value.status == 404
    assert db._tables["escalation_chains"][0]["status"] == "exhausted"
    assert len(db._tables["escalation_events"]) == events_before


def test_fonoster_emergency_intake_books_and_confirms(fworld):
    db, tel, agent, client, events, session = _started_fsession(fworld)
    _seed_contacts(db)
    result = pa.record_emergency_intake(
        db,
        org_id=ORG_ID,
        session_id=session["id"],
        details={
            "caller_name": "Amber's neighbor",
            "phone": CALLER,
            "issue": "No heat, house at 55 degrees",
            "address": "123 Test St, Denver",
            "requested_at": _at(23).astimezone(timezone.utc).isoformat(),
        },
        telephony=tel,
        ack_probe=lambda contact: True,
        now=_at(22, 20),
    )
    job = db._tables["jobs"][0]
    assert job["job_type"] == "emergency"
    appt = db._tables["appointments"][0]
    assert appt["job_id"] == job["id"]
    assert result["appointment_id"] == appt["id"]
    assert any(to == CALLER for to, _ in client.messages)  # confirmation sent
    assert client.calls, "escalation never rang the emergency contacts"


def test_fonoster_auto_reschedule_no_show_moves_and_tells_customer_why(fworld):
    db, tel, agent, client, _ = fworld
    tue_9am = _at(9).astimezone(timezone.utc).isoformat()
    tue_930 = _at(9, 30).astimezone(timezone.utc).isoformat()
    _seed_appointment(db, appt_id="appt-a", scheduled_at=tue_9am)
    _seed_appointment(db, appt_id="appt-busy", scheduled_at=tue_930)
    result = pa.auto_reschedule_appointment(
        db,
        org_id=ORG_ID,
        appointment_id="appt-a",
        reason="no_show",
        telephony=tel,
        now=_at(8),
    )
    new_local = datetime.fromisoformat(result["new_scheduled_at"]).astimezone(DENVER)
    assert (new_local.hour, new_local.minute) == (10, 0)
    sms = [body for to, body in client.messages if to == CALLER]
    assert sms, "customer was never told"
    assert "missed you" in sms[0].lower()  # honest reason wording
    assert "10:00" in sms[0]


def test_fonoster_auto_reschedule_double_booked_detects_conflict(fworld):
    db, tel, agent, client, _ = fworld
    tue_9am = _at(9).astimezone(timezone.utc).isoformat()
    _seed_appointment(
        db, appt_id="appt-a", scheduled_at=tue_9am, technician_id="tech-1"
    )
    _seed_appointment(
        db, appt_id="appt-clash", scheduled_at=tue_9am, technician_id="tech-1"
    )
    result = pa.auto_reschedule_appointment(
        db,
        org_id=ORG_ID,
        appointment_id="appt-a",
        reason="double_booked",
        telephony=tel,
        now=_at(8),
    )
    new_local = datetime.fromisoformat(result["new_scheduled_at"]).astimezone(DENVER)
    assert (new_local.hour, new_local.minute) == (9, 30)
    sms = [body for to, body in client.messages if to == CALLER]
    assert sms and "scheduling conflict" in sms[0].lower()


# ---------------------------------------------------------------------------
# TW-727 provider selection + fail-closed contracts
# ---------------------------------------------------------------------------

_FONOSTER_CREDS = {
    "fonoster_api_endpoint": "localhost:50051",
    "fonoster_access_key_id": "WO-test-only",
    "fonoster_access_key_secret": "test-only-secret",
    "fonoster_from_number": SHOP_NUMBER,
}


def _patched_settings(**overrides):
    return mock.patch.multiple(settings, **overrides)


def test_default_provider_selection_is_fonoster():
    assert settings.phone_provider == "fonoster"
    with _patched_settings(**_FONOSTER_CREDS):
        assert isinstance(vp.get_telephony_provider(), vp.FonosterTelephonyProvider)
        assert isinstance(vp.get_voice_agent(), vp.FonosterVoiceAgent)


def test_fonoster_factories_fail_closed_without_credentials():
    with _patched_settings(
        phone_provider="fonoster",
        fonoster_api_endpoint=None,
        fonoster_access_key_id=None,
        fonoster_access_key_secret=None,
        fonoster_from_number=None,
    ):
        with pytest.raises(vp.ProviderNotConfigured):
            vp.get_telephony_provider()
        with pytest.raises(vp.ProviderNotConfigured):
            vp.get_voice_agent()


def test_fonoster_provider_methods_fail_closed_without_client():
    tel = vp.FonosterTelephonyProvider(from_number=SHOP_NUMBER)
    agent = vp.FonosterVoiceAgent()
    with pytest.raises(vp.ProviderNotConfigured):
        tel.place_call(to=AMBER, say_text="Emergency")
    with pytest.raises(vp.ProviderNotConfigured):
        tel.send_sms(to=AMBER, body="Emergency")
    with pytest.raises(vp.ProviderNotConfigured):
        tel.start_recording(call_sid=CALL_REF)
    with pytest.raises(vp.ProviderNotConfigured):
        agent.speak(session_id="s-1", text="hello")


def test_unknown_provider_name_fails_closed():
    with _patched_settings(phone_provider="carrier-pigeon"):
        with pytest.raises(vp.ProviderNotConfigured):
            vp.get_telephony_provider()
        with pytest.raises(vp.ProviderNotConfigured):
            vp.get_voice_agent()


def test_legacy_twilio_openai_still_selectable_but_not_default():
    with _patched_settings(
        phone_provider="twilio_openai",
        twilio_account_sid="AC-test-only",
        twilio_auth_token="test-only",
        twilio_from_number=SHOP_NUMBER,
        openai_api_key="sk-test-only",
    ):
        assert isinstance(vp.get_telephony_provider(), vp.TwilioTelephonyProvider)
        assert isinstance(vp.get_voice_agent(), vp.OpenAIRealtimeVoiceAgent)


# ---------------------------------------------------------------------------
# Autopilot YAML + self-host compose contracts (static, house pattern)
# ---------------------------------------------------------------------------

_DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "fonoster"


def test_autopilot_yaml_disclosure_first_local_think_and_tools():
    raw = (_DEPLOY_DIR / "autopilot" / "trades-answering.agent.yaml").read_text()
    data = yaml.safe_load(raw)
    assert data["type"] == "AUTOPILOT"
    intelligence = data["intelligence"]
    assert intelligence["productRef"] == "llm.ollama"
    convo = intelligence["config"]["conversationSettings"]
    model = intelligence["config"]["languageModel"]
    assert model["provider"] == "ollama"  # think runs local
    first = convo["firstMessage"].lower()
    assert "ai" in first and "automated" in first  # disclosure is utterance #1
    assert data["speechToText"]["productRef"] == "stt.deepgram"
    assert data["textToSpeech"]["productRef"] == "tts.deepgram"
    tool_names = {t["name"] for t in model["tools"]}
    assert {
        "book_emergency_visit",
        "escalate_to_owner",
        "set_call_recording",
    } <= tool_names
    # The recording announcement text ships verbatim in the agent config.
    assert "This call is now being recorded." in raw


def test_compose_declares_fonoster_services_config_only():
    raw = (_DEPLOY_DIR / "docker-compose.yml").read_text()
    for image in (
        "fonoster/apiserver",
        "fonoster/autopilot",
        "fonoster/routr-one",
        "fonoster/asterisk",
        "postgres",
        "nats",
    ):
        assert image in raw, image
    assert "50051" in raw  # API server gRPC
    assert "50061" in raw  # Autopilot voice server
