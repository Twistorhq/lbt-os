"""TW-800 Twistor Trades agent tool gateway - policy + validation.

Pattern adopted under MIT from mikecann/plugbear (LICENSE read live
2026-10-30, Copyright (c) 2026 Mike Cann): the gateway holds an
ordered rule list; the first rule matching a tool call wins; a rule's
mode is allow, ask, or deny; no matching rule means the default,
which here is deny. "ask" parks the call until an owner approves
that specific call.

What Twistor adds on top of the pattern: a rule may bind its tool to
a TW-768 agent action class, fixed or resolved from a call argument
(notify.send runs under email.send or sms.send depending on its
channel argument). A bound call must ALSO pass the ledger's
execution-time check - the gateway never duplicates the ledger and
never overrides it. An unresolvable binding (unknown channel,
unknown class) is a construction/decision error that fails closed,
never an implicit pass.

Fail closed is the model's posture, same as TW-768: unknown modes,
unknown classes, and malformed rules are GatewayError at
construction, so a typo'd rule can never silently widen the gate.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ..agentperms.models import ACTION_CLASSES, channel_of


class GatewayError(ValueError):
    """A gateway statement that cannot be represented. Fails loud."""


class _StrictBase(BaseModel):
    """Surfaces EVERY construction failure as raw GatewayError.

    Same contract as agentperms' _StrictBase (TW-768): pydantic
    wraps model_post_init errors in ValidationError; __init__
    normalizes back so callers see exactly one error type.
    """

    model_config = ConfigDict(extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except GatewayError:
            raise
        except ValidationError as e:
            raise GatewayError(str(e)) from e


MODES: tuple[str, ...] = ("allow", "ask", "deny")

# How long an "ask" waits for the owner before it expires unexecuted.
APPROVAL_TTL_MINUTES = 60

# Audit event kinds, mirroring the migration CHECK on agt_tool_calls.
CALL_KINDS: tuple[str, ...] = (
    "requested",
    "approval_requested",
    "approved",
    "approval_denied",
    "approval_expired",
    "executed",
    "denied",
    "failed",
)

APPROVAL_STATUSES: tuple[str, ...] = (
    "pending",
    "approved",
    "denied",
    "expired",
    "executed",
    "fenced",
    # Terminal truth for an approved call whose execution failed:
    # without it the row sits at 'approved' forever and reads as
    # settled-and-ran when it did not run.
    "execution_failed",
    # Terminal state for a parked row whose stored args no longer
    # match the fingerprint sealed at park time (TW-806): execution
    # refused, the mismatch is on the audit chain.
    "tampered",
)


class ToolRule(_StrictBase):
    """One ordered policy rule for a tool name glob.

    Exactly one binding shape: action_class (fixed), class_by_arg
    (resolve the class from an argument through class_map), or
    neither (unbound - the ledger is not consulted). A rule with
    both shapes is a construction error: a binding that can be read
    two ways will be read the wrong way eventually.
    """

    tool: str
    mode: str
    action_class: str | None = None
    class_by_arg: str | None = None
    class_map: dict[str, str] = {}

    def model_post_init(self, _ctx: Any) -> None:
        if self.mode not in MODES:
            raise GatewayError(f"unknown mode {self.mode!r}; want one of {MODES}")
        if self.action_class is not None:
            channel_of(self.action_class)  # raises LedgerError on unknown
        if self.class_by_arg is not None:
            if self.action_class is not None:
                raise GatewayError(
                    "a rule binds by action_class or by class_by_arg, never both"
                )
            if not self.class_map:
                raise GatewayError("class_by_arg requires a non-empty class_map")
            for cls in self.class_map.values():
                channel_of(cls)  # raises on unknown class in the map
        elif self.class_map:
            raise GatewayError("class_map without class_by_arg binds nothing")

    def resolve_class(self, args: dict[str, Any]) -> str | None:
        """The action class this call runs under, or None if unbound.

        Raises GatewayError when a class_by_arg binding cannot
        resolve (argument missing or value unmapped): the caller
        turns that into a denial, never a pass.
        """
        if self.action_class is not None:
            return self.action_class
        if self.class_by_arg is None:
            return None
        value = args.get(self.class_by_arg)
        cls = self.class_map.get(str(value)) if value is not None else None
        if cls is None:
            raise GatewayError(
                f"tool bound by {self.class_by_arg!r} cannot resolve a class "
                f"from value {value!r}"
            )
        return cls


# The shipped policy. Order matters: first match wins, default deny.
#
# - notify.send is the Trades agent's outbound voice: bound to the
#   ledger by channel, so the owner's standing grant (and its caps
#   and revocations) is the whole gate - mode allow, ledger decides.
# - flows.trigger starts work, so it asks: a grant vocabulary has no
#   class for "run an arbitrary workflow", and plugbear's answer for
#   consequential unbound calls is a per-call owner approval.
# - flows reads are allow: in-house, org-scoped, read-only - but
#   still audited, like every call through the gate.
DEFAULT_RULES: tuple[ToolRule, ...] = (
    ToolRule(
        tool="notify.send",
        mode="allow",
        class_by_arg="channel",
        class_map={"email": "email.send", "sms": "sms.send"},
    ),
    ToolRule(tool="flows.trigger", mode="ask"),
    ToolRule(tool="flows.list", mode="allow"),
    ToolRule(tool="flows.run_status", mode="allow"),
    ToolRule(tool="flows.preview", mode="allow"),
)


class ResolvedPolicy(BaseModel):
    """The policy outcome for one call, before the ledger is asked."""

    model_config = ConfigDict(extra="forbid")

    mode: str  # 'allow' | 'ask' | 'deny'
    action_class: str | None = None
    rule_index: int | None = None  # None when the default applied
    bind_error: str | None = None  # set when class resolution failed


def resolve_policy(
    tool: str,
    args: dict[str, Any],
    rules: tuple[ToolRule, ...] = DEFAULT_RULES,
) -> ResolvedPolicy:
    """First matching rule wins; no match is the deny default.

    A rule whose binding cannot resolve still matches (it owns this
    tool) and yields mode 'deny' with bind_error set: a later, wider
    rule must never catch a call the owning rule failed to bind.
    """
    for i, rule in enumerate(rules):
        if not fnmatch.fnmatch(tool, rule.tool):
            continue
        try:
            cls = rule.resolve_class(args)
        except GatewayError as e:
            return ResolvedPolicy(mode="deny", rule_index=i, bind_error=str(e))
        return ResolvedPolicy(mode=rule.mode, action_class=cls, rule_index=i)
    return ResolvedPolicy(mode="deny")


class GatewayOutcome(BaseModel):
    """One invoke/decide result. Returned, never raised, for every
    refusal: a denied call is a normal outcome of a gate, not an
    exception in the host (self-healing law - the gateway failing a
    call must never crash the process that called it)."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool: str
    status: str  # 'executed' | 'denied' | 'pending_approval' | 'failed'
    reason: str
    action_class: str | None = None
    channel: str | None = None
    grant_id: str | None = None
    approval_id: str | None = None
    result: dict[str, Any] | None = None


def outcome_channel(action_class: str | None) -> str | None:
    """Channel for an outcome/audit row; None for unbound calls."""
    if action_class is None:
        return None
    if action_class not in ACTION_CLASSES:
        raise GatewayError(f"unknown action class {action_class!r}")
    return channel_of(action_class)
