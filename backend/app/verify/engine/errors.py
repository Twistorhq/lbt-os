"""Engine errors (TW-658). Each is an honest refusal, never a fallback."""

from __future__ import annotations


class EngineError(RuntimeError):
    """Base class for engine refusals."""


class CaptureDisabled(EngineError):
    """Production capture attempted while the D3 flag is off."""


class ConsentRequired(EngineError):
    """No current consent of the required class for this member."""


class DocumentClassNotEnabled(EngineError):
    """The document class's capture is not built in this phase."""


class UnknownSession(EngineError):
    """The session id does not exist in the verify store."""


class SessionStateError(EngineError):
    """The session is not in a state that accepts this submission."""
