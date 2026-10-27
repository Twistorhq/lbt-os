"""Provider ports for TW-284 AI phone automation (Path A: Twilio + OpenAI
Realtime, approved by Justynn 2026-10-27).

The orchestration in phone_automation.py talks only to these two ports, so
tests inject fakes and production injects the real adapters. Adapters are
stubbed for credentials in the house posture (same as M2): when the Twilio
or OpenAI credentials are absent, the factory raises ProviderNotConfigured
and callers fail closed — nothing half-live, nothing silently skipped.

The OpenAI Realtime media bridge itself (Twilio Media Streams websocket
relay) is the go-live step: it lands with the Twilio number purchase
(Justynn's manual step). Until a transport is wired, the voice agent's
speak() raises ProviderNotConfigured rather than pretending to talk.
"""
import logging
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)


class ProviderNotConfigured(RuntimeError):
    """Raised when a voice/telephony provider lacks credentials or wiring."""


def _twilio_configured() -> bool:
    from ..config import settings
    return all([settings.twilio_account_sid, settings.twilio_auth_token,
                settings.twilio_from_number])


class TwilioTelephonyProvider:
    """Real Twilio adapter: outbound ring/SMS + in-call recording control."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        from ..config import settings
        if not _twilio_configured():
            raise ProviderNotConfigured("twilio-not-configured")
        if self._client is None:
            from twilio.rest import Client
            self._client = Client(settings.twilio_account_sid,
                                  settings.twilio_auth_token)
        return self._client

    def place_call(self, *, to: str, say_text: str) -> str:
        from ..config import settings
        client = self._get_client()
        twiml = f"<Response><Say>{escape(say_text)}</Say></Response>"
        call = client.calls.create(
            to=to, from_=settings.twilio_from_number, twiml=twiml)
        return call.sid

    def send_sms(self, *, to: str, body: str) -> None:
        from ..config import settings
        client = self._get_client()
        client.messages.create(
            to=to, from_=settings.twilio_from_number, body=body)

    def start_recording(self, *, call_sid: str) -> None:
        client = self._get_client()
        client.calls(call_sid).recordings.create()

    def stop_recording(self, *, call_sid: str) -> None:
        client = self._get_client()
        try:
            client.calls(call_sid).recordings(
                "in-progress").update(status="stopped")
        except Exception as exc:  # no live recording is not an error path
            log.warning("stop_recording no-op for %s: %s", call_sid, exc)


class OpenAIRealtimeVoiceAgent:
    """Voice-agent port for the OpenAI Realtime session.

    speak() delegates to the media-stream transport. The transport is the
    go-live bridge; until it exists, speak() fails closed.
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
            raise ProviderNotConfigured(
                "openai-realtime-bridge-not-configured")
        self._transport.say(session_id, text)


def get_telephony_provider() -> TwilioTelephonyProvider:
    if not _twilio_configured():
        raise ProviderNotConfigured("twilio-not-configured")
    return TwilioTelephonyProvider()


def get_voice_agent() -> OpenAIRealtimeVoiceAgent:
    from ..config import settings
    if not settings.openai_api_key or settings.openai_api_key == "not-needed":
        raise ProviderNotConfigured("openai-not-configured")
    return OpenAIRealtimeVoiceAgent()
