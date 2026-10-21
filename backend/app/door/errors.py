"""Door Pass errors (TW-671). Each is an honest refusal, never a fallback."""

from __future__ import annotations


class DoorError(RuntimeError):
    """Base class for door service refusals."""


class ClientFlowSelection(DoorError):
    """The client tried to select a flow mode.

    TW-657 Section 2.3: purpose and flow are server derived from
    the venue location and the active state profile, never selected
    by the member app, the venue app, or a request parameter.
    """


class DoorRefused(DoorError):
    """The transaction cannot be created under the governing profile
    or the member's current standing."""


class DoorConsentRequired(DoorError):
    """No current door capture consent for this member.

    The door consent is a separate explicit instrument from the
    onboarding biometric consent (TW-647 Section 6.3, TW-654 D1).
    """


class DoorCaptureDisabled(DoorError):
    """A non-fixture enrollment was presented to the prototype.

    The D3 production capture flag stays off until counsel's
    written review is filed to TW-654; this build transacts on
    consented fixtures only.
    """


class UnknownTransaction(DoorError):
    """The transaction id does not exist in the door store."""


class TransactionStateError(DoorError):
    """The transaction is not in a state that accepts this action,
    including any attempt to run a biometric step on a lane
    transaction (the flows never blend)."""
