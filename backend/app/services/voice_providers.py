"""Provider ports for AI phone automation.

TW-727 (Justynn, 2026-10-28): Fonoster (fonoster/fonoster, MIT,
self-hosted) is the DEFAULT provider. It replaces the paid Twilio +
OpenAI Realtime path (TW-284 Path A), which stays implemented and
selectable via settings.phone_provider = "twilio_openai" but is
DEPRECATED for AI answering and no longer the default.

The orchestration in phone_automation.py talks only to these ports, so
tests inject fakes and production injects the real adapters. The house
posture holds for every provider: missing credentials or wiring raise
ProviderNotConfigured and callers fail closed — nothing half-live,
nothing silently skipped.

Fonoster wiring notes (the go-live step, same posture as the TW-284
media bridge): Fonoster's API server speaks gRPC (port 50051) and its
only official SDK is Node (@fonoster/sdk) — there is no official
Python SDK. From this backend, the Fonoster providers delegate to an
injected client; the production client is the SDK bridge deployed
with the self-host stack (deploy/fonoster/), and until that bridge is
wired its methods raise ProviderNotConfigured rather than pretending
to call. The in-call conversation itself is the Autopilot agent
defined in deploy/fonoster/autopilot/trades-answering.agent.yaml.
"""

import logging
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)


class ProviderNotConfigured(RuntimeError):
    """Raised when a voice/telephony provider lacks credentials or wiring."""


# ---------------------------------------------------------------------------
# Fonoster (TW-727) — the default provider
# ---------------------------------------------------------------------------


def _fonoster_configured() -> bool:
    from ..config import settings

    return all(
        [
            settings.fonoster_api_endpoint,
            settings.fonoster_access_key_id,
            settings.fonoster_access_key_secret,
            settings.fonoster_from_number,
        ]
    )


class FonosterSdkBridgeClient:
    """Production Fonoster client: the bridge seam.

    The Fonoster API server is gRPC with a Node-only official SDK, so
    the live binding runs as the SDK bridge in the self-host stack
    (deploy/fonoster/). Until that bridge is deployed and pointed at
    this backend, every method fails closed with ProviderNotConfigured
    — the same posture as the TW-284 OpenAI media-stream transport.
    Tests inject a fake client exposing this same surface.
    """

    def _not_wired(self):
        raise ProviderNotConfigured("fonoster-bridge-not-configured")

    def create_call(self, *, from_number: str, to: str, say_text: str) -> str:
        self._not_wired()

    def send_message(self, *, from_number: str, to: str, body: str) -> None:
        self._not_wired()

    def start_recording(self, *, call_ref: str) -> None:
        self._not_wired()

    def stop_recording(self, *, call_ref: str) -> None:
        self._not_wired()

    def say(self, *, session_id: str, text: str) -> None:
        self._not_wired()


class FonosterTelephonyProvider:
    """Fonoster adapter: outbound ring/message + in-call recording control.

    place_call opens an outbound call through the shop's Fonoster
    application; say_text is the announcement the callee hears first
    (used for escalation rings). Recording control maps to the voice
    session for the call ref (the session's provider call reference,
    stored on ai_call_sessions — the column name predates TW-727).
    """

    def __init__(self, client=None, from_number: str | None = None):
        self._client = client
        self._from_number = from_number

    def _get_client(self):
        if self._client is None:
            if not _fonoster_configured():
                raise ProviderNotConfigured("fonoster-not-configured")
            self._client = FonosterSdkBridgeClient()
        return self._client

    def _from(self) -> str:
        if self._from_number:
            return self._from_number
        from ..config import settings

        return settings.fonoster_from_number or ""

    def place_call(self, *, to: str, say_text: str) -> str:
        client = self._get_client()
        return client.create_call(from_number=self._from(), to=to, say_text=say_text)

    def send_sms(self, *, to: str, body: str) -> None:
        client = self._get_client()
        client.send_message(from_number=self._from(), to=to, body=body)

    def start_recording(self, *, call_sid: str) -> None:
        client = self._get_client()
        client.start_recording(call_ref=call_sid)

    def stop_recording(self, *, call_sid: str) -> None:
        client = self._get_client()
        try:
            client.stop_recording(call_ref=call_sid)
        except ProviderNotConfigured:
            raise  # the fail-closed signal is never swallowed
        except Exception as exc:  # no live recording is not an error path
            log.warning("stop_recording no-op for %s: %s", call_sid, exc)


class FonosterVoiceAgent:
    """Voice-agent port for the Fonoster Autopilot session.

    In production the Autopilot application speaks on the call; this
    port carries the backend-driven utterances the orchestration owns
    (the AI disclosure at session start, the recording announcement).
    The Autopilot YAML's firstMessage carries the same disclosure for
    calls that land on the agent directly over SIP.
    """

    def __init__(self, client=None):
        self._client = client

    @staticmethod
    def build_instructions(shop_name: str) -> str:
        return (
            f"You are the phone assistant for {shop_name}. You have already "
            "disclosed that you are an AI assistant. Be brief and calm. "
            "For emergencies (no heat, no water, flooding, gas smell), "
            "collect the caller's name, callback number, address, and what "
            "is happening, then book the emergency visit and confirm the "
            "time out loud. Never invent prices or arrival guarantees."
        )

    def speak(self, *, session_id: str, text: str) -> None:
        if self._client is None:
            if not _fonoster_configured():
                raise ProviderNotConfigured("fonoster-not-configured")
            self._client = FonosterSdkBridgeClient()
        self._client.say(session_id=session_id, text=text)


# ---------------------------------------------------------------------------
# DEPRECATED legacy path (TW-284 Path A: Twilio + OpenAI Realtime).
# Kept intact and selectable via settings.phone_provider =
# "twilio_openai" for rollback only; not the default since TW-727.
# Note: the twilio_* config keys also serve the M2 missed-call layer
# (call_service.py), which is NOT deprecated — this deprecation covers
# the AI-answering provider factories only.
# ---------------------------------------------------------------------------


def _twilio_configured() -> bool:
    from ..config import settings

    return all(
        [
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_from_number,
        ]
    )


class TwilioTelephonyProvider:
    """DEPRECATED (TW-727): real Twilio adapter — outbound ring/SMS +
    in-call recording control. Legacy AI-answering path only."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        from ..config import settings

        if not _twilio_configured():
            raise ProviderNotConfigured("twilio-not-configured")
        if self._client is None:
            from twilio.rest import Client

            self._client = Client(
                settings.twilio_account_sid, settings.twilio_auth_token
            )
        return self._client

    def place_call(self, *, to: str, say_text: str) -> str:
        from ..config import settings

        client = self._get_client()
        twiml = f"<Response><Say>{escape(say_text)}</Say></Response>"
        call = client.calls.create(
            to=to, from_=settings.twilio_from_number, twiml=twiml
        )
        return call.sid

    def send_sms(self, *, to: str, body: str) -> None:
        from ..config import settings

        client = self._get_client()
        client.messages.create(to=to, from_=settings.twilio_from_number, body=body)

    def start_recording(self, *, call_sid: str) -> None:
        client = self._get_client()
        client.calls(call_sid).recordings.create()

    def stop_recording(self, *, call_sid: str) -> None:
        client = self._get_client()
        try:
            client.calls(call_sid).recordings("in-progress").update(status="stopped")
        except Exception as exc:  # no live recording is not an error path
            log.warning("stop_recording no-op for %s: %s", call_sid, exc)


class OpenAIRealtimeVoiceAgent:
    """DEPRECATED (TW-727): voice-agent port for the OpenAI Realtime
    session (legacy TW-284 Path A).

    speak() delegates to the media-stream transport. The transport is
    the go-live bridge; until it exists, speak() fails closed.
    """

    def __init__(self, transport=None):
        self._transport = transport

    @staticmethod
    def build_instructions(shop_name: str) -> str:
        return (
            f"You are the phone assistant for {shop_name}. You have already "
            "disclosed that you are an AI assistant. Be brief and calm. "
            "For emergencies (no heat, no water, flooding, gas smell), "
            "collect the caller's name, callback number, address, and what "
            "is happening, then book the emergency visit and confirm the "
            "time out loud. Never invent prices or arrival guarantees."
        )

    def speak(self, *, session_id: str, text: str) -> None:
        if self._transport is None:
            raise ProviderNotConfigured("openai-realtime-bridge-not-configured")
        self._transport.say(session_id, text)


# ---------------------------------------------------------------------------
# Factories — dispatch on settings.phone_provider (default: fonoster)
# ---------------------------------------------------------------------------


def _provider_name() -> str:
    from ..config import settings

    return (settings.phone_provider or "fonoster").strip().lower()


def get_telephony_provider():
    name = _provider_name()
    if name == "fonoster":
        if not _fonoster_configured():
            raise ProviderNotConfigured("fonoster-not-configured")
        from ..config import settings

        return FonosterTelephonyProvider(from_number=settings.fonoster_from_number)
    if name == "twilio_openai":  # DEPRECATED legacy path (TW-727)
        if not _twilio_configured():
            raise ProviderNotConfigured("twilio-not-configured")
        return TwilioTelephonyProvider()
    raise ProviderNotConfigured(f"unknown-phone-provider:{name}")


def get_voice_agent():
    name = _provider_name()
    if name == "fonoster":
        if not _fonoster_configured():
            raise ProviderNotConfigured("fonoster-not-configured")
        return FonosterVoiceAgent()
    if name == "twilio_openai":  # DEPRECATED legacy path (TW-727)
        from ..config import settings

        if not settings.openai_api_key or settings.openai_api_key == "not-needed":
            raise ProviderNotConfigured("openai-not-configured")
        return OpenAIRealtimeVoiceAgent()
    raise ProviderNotConfigured(f"unknown-phone-provider:{name}")
