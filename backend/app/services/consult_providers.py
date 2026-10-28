"""Provider ports for TW-734 video consult.

The consult room rides Twistor's own Jitsi Meet package (TW-729,
twistor-infra services/jitsi-meet, target host meet.twistor.co).
Jitsi rooms are implicit — a room exists when the first participant
opens its URL — so the production room provider is a URL builder, not
an API client. The TW-729 posture applies: only an authenticated host
account creates/opens rooms, guests join through the lobby, so the
tech is the host and the customer is the lobby guest.

Recording: the TW-729 package ships no Jibri (Jitsi's recording
service), so production start/stop_recording fails closed with
ProviderNotConfigured until Jibri is wired at go-live. Nothing
half-records a consult.

Transcription: the local faster-whisper path exists in the workspace
(TTS/video lanes), but the lbt-os backend has no bridge to it yet, so
the production transcriber fails closed the same way the TW-727
Fonoster bridge does. The answering machine stores the message with
transcription_status="pending" rather than pretending (TW-208).

Fonoster note (research, 2026-10-28): the self-hosted Fonoster core
(TW-727) is a programmable VOICE stack — its verbs (Say/Gather/Play/
Record/Stream) are audio-only; SIP/WebRTC is its transport, but no
video verb or video recording exists in the core API. Fonoster's
video product (Goodtok, PeerJS widget + front-office) is a separate
project that is not part of the fonoster/fonoster compose we ship.
Video therefore rides Jitsi; Fonoster/Autopilot carries the audio AI
side (disclosure, announcements) through the TW-284 voice-agent port.
"""

import logging

from ..config import settings
from .voice_providers import ProviderNotConfigured

log = logging.getLogger(__name__)

__all__ = [
    "JitsiRoomProvider",
    "LocalWhisperTranscriber",
    "ProviderNotConfigured",
    "get_room_provider",
    "get_transcriber",
]


class JitsiRoomProvider:
    """Production room provider: builds room URLs on the Twistor Meet
    base URL. Fails closed until the base URL is configured (the
    TW-729 package is not deployed; meet.twistor.co is go-live)."""

    def __init__(self, base_url: str | None = None):
        self._base_url = base_url

    def _base(self) -> str:
        base = self._base_url or settings.video_consult_jitsi_base_url
        if not base:
            raise ProviderNotConfigured("jitsi-room-not-configured")
        return base.rstrip("/")

    def create_room(self, *, org_id: str, session_id: str) -> dict:
        base = self._base()
        room_name = f"twistor-consult-{session_id}"
        return {"room_name": room_name, "room_url": f"{base}/{room_name}"}

    def start_recording(self, *, room_name: str) -> None:
        self._base()  # unconfigured stack fails closed before anything
        raise ProviderNotConfigured("jitsi-recording-not-configured")

    def stop_recording(self, *, room_name: str) -> None:
        self._base()
        raise ProviderNotConfigured("jitsi-recording-not-configured")


class LocalWhisperTranscriber:
    """Production transcriber: the local faster-whisper bridge seam.

    Until the bridge is wired (go-live), transcribe() fails closed —
    the same posture as the TW-727 Fonoster SDK bridge. Callers store
    the message as transcription-pending instead of dropping it.
    """

    def transcribe(self, *, recording_ref: str) -> str:
        raise ProviderNotConfigured("faster-whisper-bridge-not-configured")


def get_room_provider() -> JitsiRoomProvider:
    if not settings.video_consult_jitsi_base_url:
        raise ProviderNotConfigured("jitsi-room-not-configured")
    return JitsiRoomProvider()


def get_transcriber() -> LocalWhisperTranscriber:
    return LocalWhisperTranscriber()
