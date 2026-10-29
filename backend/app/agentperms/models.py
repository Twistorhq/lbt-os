"""TW-768 Twistor Trades agent permission ledger — model + validation.

One permission ledger per org governs every action the shop's agent
may take. The vocabulary is fixed: eight action classes grouped into
channels. Revocation strength names a class, a channel, or all.

Money classes (payment.charge, purchase) are capped in cents. A grant
of a money class REQUIRES a cap — an unbounded money grant is not a
thing this ledger can express — and a non-money class must NOT carry
a cap. Both halves are enforced here, at construction, raising
LedgerError raw (never wrapped): a permission that cannot be stated
exactly fails loud at the owner surface, not silently at execution.

Fail closed is the model's posture, not just the service's: unknown
classes, unknown channels, and unknown strengths are construction
errors, so a typo'd class name can never become an implicit grant.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class LedgerError(ValueError):
    """A permission statement that cannot be represented. Fails loud."""


class _StrictBase(BaseModel):
    """Surfaces EVERY construction failure as raw LedgerError.

    Same contract as Flags' _StrictBase (TW-621): pydantic wraps
    model_post_init errors in ValidationError; __init__ normalizes
    back so callers see exactly one error type on every path.
    """

    model_config = ConfigDict(extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except LedgerError:
            raise
        except ValidationError as e:
            raise LedgerError(str(e)) from e


# The eight agent action classes (Diego's TW-764 Spec 2, verbatim).
ACTION_CLASSES: tuple[str, ...] = (
    "email.send",
    "sms.send",
    "call.outbound",
    "booking.create",
    "booking.reschedule",
    "payment.link_send",
    "payment.charge",
    "purchase",
)

# Class -> channel. Channel revocation revokes every class in it.
CLASS_CHANNEL: dict[str, str] = {
    "email.send": "email",
    "sms.send": "sms",
    "call.outbound": "call",
    "booking.create": "booking",
    "booking.reschedule": "booking",
    "payment.link_send": "payment",
    "payment.charge": "payment",
    "purchase": "purchase",
}

CHANNELS: tuple[str, ...] = tuple(sorted(set(CLASS_CHANNEL.values())))

# Classes that move money and therefore carry a cap in cents.
MONEY_CLASSES: frozenset[str] = frozenset({"payment.charge", "purchase"})

GRANT_STATUSES: tuple[str, ...] = ("granted", "denied", "revoked")
REVOKE_STRENGTHS: tuple[str, ...] = ("class", "channel", "all")


def channel_of(action_class: str) -> str:
    """Channel for a class. LedgerError on an unknown class."""
    try:
        return CLASS_CHANNEL[action_class]
    except KeyError:
        raise LedgerError(
            f"unknown action class {action_class!r}; want one of {ACTION_CLASSES}"
        ) from None


def classes_for_channel(channel: str) -> tuple[str, ...]:
    """All action classes in a channel, in ACTION_CLASSES order."""
    if channel not in CHANNELS:
        raise LedgerError(f"unknown channel {channel!r}; want one of {CHANNELS}")
    return tuple(c for c in ACTION_CLASSES if CLASS_CHANNEL[c] == channel)


def _validate_cap(action_class: str, cap_cents: int | None) -> None:
    if action_class in MONEY_CLASSES:
        if cap_cents is None:
            raise LedgerError(
                f"{action_class} moves money: a grant requires cap_cents; "
                "an unbounded money grant cannot be stated in this ledger"
            )
        if cap_cents < 0:
            raise LedgerError(f"cap_cents must be >= 0, got {cap_cents}")
    elif cap_cents is not None:
        raise LedgerError(
            f"{action_class} does not move money: cap_cents must be None, "
            f"got {cap_cents}"
        )


class GrantStatement(_StrictBase):
    """An owner's grant (or explicit deny) of one action class."""

    action_class: str
    status: str = "granted"
    scope: dict[str, Any] = {}
    cap_cents: int | None = None

    def model_post_init(self, _ctx: Any) -> None:
        channel_of(self.action_class)  # raises on unknown class
        if self.status not in GRANT_STATUSES:
            raise LedgerError(
                f"unknown grant status {self.status!r}; want one of {GRANT_STATUSES}"
            )
        if self.status == "granted":
            _validate_cap(self.action_class, self.cap_cents)
        elif self.cap_cents is not None and self.action_class not in MONEY_CLASSES:
            raise LedgerError(
                f"{self.action_class} does not move money: cap_cents must be None"
            )


class RevokeStatement(_StrictBase):
    """An owner's revocation motion: one strength, one target.

    strength 'class' targets an action class, 'channel' targets a
    channel, 'all' targets nothing (everything). A target that does
    not match the strength's vocabulary is a construction error.
    """

    strength: str
    target: str | None = None

    def model_post_init(self, _ctx: Any) -> None:
        if self.strength not in REVOKE_STRENGTHS:
            raise LedgerError(
                f"unknown revoke strength {self.strength!r}; "
                f"want one of {REVOKE_STRENGTHS}"
            )
        if self.strength == "class":
            if self.target is None:
                raise LedgerError("strength 'class' requires an action class target")
            channel_of(self.target)  # raises on unknown class
        elif self.strength == "channel":
            if self.target is None:
                raise LedgerError("strength 'channel' requires a channel target")
            classes_for_channel(self.target)  # raises on unknown channel
        elif self.target is not None:
            raise LedgerError("strength 'all' takes no target")

    def affected_classes(self) -> tuple[str, ...]:
        """The action classes this motion revokes, in canonical order."""
        if self.strength == "class":
            return (self.target,)  # type: ignore[return-value]
        if self.strength == "channel":
            return classes_for_channel(self.target)  # type: ignore[arg-type]
        return ACTION_CLASSES


class PermissionDecision(BaseModel):
    """The execution-time check result. Returned, never raised, by
    check_permission; require_permission raises it as a denial.

    grant_id is the ledger grant the decision relied on, so every
    executed action can record the grant reference it ran under
    (TW-764 Spec 4 acceptance: audit rows carry a grant reference).
    """

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: str  # 'granted' | 'no_grant' | 'denied' | 'revoked' | 'cap_missing' | 'cap_exceeded' | 'amount_required' | 'invalid_amount'
    action_class: str
    channel: str
    grant_id: str | None = None
    cap_cents: int | None = None
