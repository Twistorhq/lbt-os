"""TW-954 (TW-948-B7) — the Twistor hard floor, as code.

Spec Sections 8.1/8.2 and build map Section 2.9: below the layered
approvals sits a floor that NO owner setting and NO agent setting
can lift. This module is that floor, and its central design choice
is what it does NOT accept: check_floor() takes facts about one
action (its class, amount, lane, consent status, metering) and
nothing else. There is no policy parameter, no settings
parameter, no database handle — a policy row, a grant scope, or
an environment flag has no path into this function, so writing
one cannot relax a rule. A stored row that claims the floor is
disabled is inert data; the check still runs.

The floor is consulted by the unattended evaluation path in
policy.py before anything is permitted. The runtime, the tool
gateway, and the job service consult the same function when the
propagation half of B7 wires them (contract in composed.py);
the rules do not change per caller.

Rules, in evaluation order (first block wins):

1. revocation_supremacy — a revoked agent acts in no context.
   Revocation is the owner's strongest motion; nothing outranks
   it, including a grant or policy written after the fact in a
   scope the revocation covers (the ledger check also denies,
   but the floor states the supremacy as its own law).
2. no_approvals_off — customer Toris have no approvals-off mode
   (Section 8.1, explicit). A caller claiming one is refused.
3. pii_bronze_only — PII doctrine (TW-552, B1): raw values live
   in the bronze layer only. A retrieval projection carrying
   raw PII never ships.
4. no_dnc_override — outbound contact (call, text) requires a
   verified-clean do-not-call / consent status under the
   TW-943 policy. Not listed-and-overridden, not unknown: clean.
5. phone_lane_only — call.outbound flows only through the
   TW-884 dialer lane, the lane the TW-885 legal gates and the
   TW-943 DNC policy are wired into. No policy opens another.
6. no_external_send_without_mandate — an external send class
   whose mandate stands removed (a grant row explicitly denied
   or revoked) does not happen, attended or not. Mere absence
   of a grant is the ledger's own no_grant denial, one layer
   down; this rule is for the adverse state.
7. no_payment_beyond_mandate — a money class needs its cap and
   an amount at or under it. Beyond mandate is beyond, in every
   context (the class/cap vocabulary itself is B7 out-of-scope
   and unchanged from TW-768).
8. toll_metering_required — a class with a named M1 toll event
   must declare exactly that event, so every tolled action is
   metered on the auditable action it performs (spec 8.4).
   Classes with no named event (calls, per the founder gap in
   Section 8.4) meter at zero; the floor invents no price.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import MONEY_CLASSES

# There is no approvals-off mode for customer Toris. This is a
# constant, not a setting: nothing reads a stored value into it.
APPROVALS_OFF_PERMITTED = False

# The one gated phone lane. TW-884 is the dialer; TW-885 legal
# gates and TW-943 DNC policy ride it. A policy cannot add a lane.
ALLOWED_PHONE_LANES: frozenset[str] = frozenset({"tw884_dialer"})

# Classes that contact a person outside the business: DNC and
# consent status govern them.
CONTACT_CLASSES: frozenset[str] = frozenset({"call.outbound", "sms.send"})

# Classes that send something external. Each requires a mandate.
EXTERNAL_SEND_CLASSES: frozenset[str] = frozenset(
    {"email.send", "sms.send", "call.outbound", "payment.link_send"}
)

# M1 toll vocabulary (TW-764/TW-774, spec Section 11): the named
# event a tolled class must declare. Classes absent here have no
# named toll event and meter at zero (spec Section 8.4).
TOLL_EVENT_FOR_CLASS: dict[str, str] = {
    "email.send": "agent.email_action",
    "booking.create": "agent.booking_confirmed",
    "booking.reschedule": "agent.booking_confirmed",
}

FLOOR_RULES: tuple[str, ...] = (
    "revocation_supremacy",
    "no_approvals_off",
    "pii_bronze_only",
    "no_dnc_override",
    "phone_lane_only",
    "no_external_send_without_mandate",
    "no_payment_beyond_mandate",
    "toll_metering_required",
)


@dataclass(frozen=True)
class FloorVerdict:
    """The floor's answer for one action. rule is None when the
    action passes; otherwise the FLOOR_RULES name that blocked it."""

    allowed: bool
    rule: str | None = None
    reason: str = ""


_PASS = FloorVerdict(allowed=True)


def _blocked(rule: str, reason: str) -> FloorVerdict:
    return FloorVerdict(allowed=False, rule=rule, reason=reason)


def check_floor(
    *,
    action_class: str,
    revoked: bool = False,
    approvals_off: bool = False,
    pii_raw_in_projection: bool = False,
    dnc_status: str | None = None,
    lane: str | None = None,
    mandate_allowed: bool = True,
    amount_cents: int | None = None,
    cap_cents: int | None = None,
    toll_event: str | None = None,
) -> FloorVerdict:
    """May this action pass the hard floor?

    Facts only, by design (see module docstring): the caller
    reports what the action IS — its class, its lane, the
    contact's verified consent status, whether a granted mandate
    stands for the class, its amount against the mandate cap, and the
    toll event it will meter under. The floor decides what no
    configuration may change.
    """
    if revoked:
        return _blocked(
            "revocation_supremacy",
            "the agent stands revoked; revocation outranks every grant and policy",
        )
    if approvals_off:
        return _blocked(
            "no_approvals_off",
            "customer Toris have no approvals-off mode (spec 8.1)",
        )
    if pii_raw_in_projection:
        return _blocked(
            "pii_bronze_only",
            "raw PII lives in the bronze layer only; projections are masked",
        )
    if action_class in CONTACT_CLASSES and dnc_status != "verified_clean":
        return _blocked(
            "no_dnc_override",
            "outbound contact requires a verified-clean DNC/consent "
            "status under the TW-943 policy; there is no override",
        )
    if action_class == "call.outbound" and lane not in ALLOWED_PHONE_LANES:
        return _blocked(
            "phone_lane_only",
            "outbound calls flow only through the TW-884 dialer lane "
            "under the TW-885 legal gates; no policy opens another lane",
        )
    if action_class in EXTERNAL_SEND_CLASSES and not mandate_allowed:
        return _blocked(
            "no_external_send_without_mandate",
            "an external send without a standing mandate does not happen",
        )
    if action_class in MONEY_CLASSES:
        if cap_cents is None or amount_cents is None or amount_cents > cap_cents:
            return _blocked(
                "no_payment_beyond_mandate",
                "payment initiation beyond the mandate cap does not happen",
            )
    expected_toll = TOLL_EVENT_FOR_CLASS.get(action_class)
    if expected_toll is not None and toll_event != expected_toll:
        return _blocked(
            "toll_metering_required",
            f"{action_class} is a tolled action and must meter as "
            f"{expected_toll}; an unmetered tolled action does not happen",
        )
    return _PASS
