"""TW-650 Twistor Social — safety helpers (pure functions).

Three doctrines live here, and they are kept apart on purpose
(spec Sections 6.6, 6.7, 6.2):

- The MISCONDUCT ladder (Section 6.6): warning, then restriction,
  then the identity-ban decision. The rungs below a ban are
  rsn_sanctions levels; the ban itself is a separate
  identity-anchored instrument (rsn_bans, Section 6.2), so the
  ladder's top is a decision state, never a sanctions level.
- SEVERE offenses skip the ladder entirely (Section 6.6): credible
  threats or acts of violence, identity fraud, sexual misconduct,
  and the Section 6.4 emergency path go straight to a temporary
  safety suspension (maximum 72 hours without a full decision,
  Section 6.2) and the ban decision. A warning is not a
  proportionate answer to danger.
- PURGE is not a rung on the misconduct ladder. It is the terminal
  outcome of the integrity pipeline (Section 6.7): flag, liveness
  challenge, 14-day grace window, purge. A ban answers misconduct;
  a purge answers an identity the platform can no longer stand
  behind, and the two are never folded together. Purge is recorded
  as an rsn_sanctions level so appeals and audit treat it
  identically, but only the pipeline below can produce it.

Appeals (Section 6.6): acknowledged within 24 hours, decided within
7 days by a fresh reviewer.

Ambassador scope (TW-648 design): ambassadors take first-line triage
only. Serious categories (threats, sexual misconduct, identity
fraud, and anything on the Section 6.4 emergency path) bypass them
entirely and land on staff at intake. An unrecognized category fails
closed to staff: ambassadors never see a category nobody classified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

APPEAL_ACK_SLA = timedelta(hours=24)
APPEAL_DECISION_SLA = timedelta(days=7)

AMBASSADOR_BYPASS_CATEGORIES = frozenset(
    {"threats", "sexual_misconduct", "identity_fraud"}
)

# The misconduct ladder's rsn_sanctions rungs, in order. The ban is
# deliberately absent: it is an rsn_bans instrument, reached through
# the BAN_DECISION state, not a level this ladder hands out.
MISCONDUCT_LADDER = ("warning", "restriction")
BAN_DECISION = "ban_decision"

# Section 6.2: a temporary safety suspension runs at most 72 hours
# without a full decision.
SEVERE_MAX_SUSPENSION_HOURS = 72

# The Section 6.7 purge pipeline, in due-process order. The grace
# window punishes disappearance, not difficulty: a member who
# engages pauses the clock (service-layer concern; the constant is
# the window the pipeline is built around).
PURGE_PIPELINE_STAGES = ("flagged", "challenge_issued", "grace_window", "purged")
PURGE_GRACE_WINDOW = timedelta(days=14)


@dataclass(frozen=True)
class AppealSla:
    ack_due_at: datetime
    decision_due_at: datetime


def appeal_sla(created_at: datetime) -> AppealSla:
    return AppealSla(
        ack_due_at=created_at + APPEAL_ACK_SLA,
        decision_due_at=created_at + APPEAL_DECISION_SLA,
    )


@dataclass(frozen=True)
class SevereRoute:
    """Where a severe offense goes: off the ladder entirely."""

    disposition: str
    decision: str
    max_suspension_hours: int
    ladder_applies: bool


def ambassador_may_triage(category: str) -> bool:
    """True only for known, non-serious categories. Unknown values
    fail closed to staff."""
    from .models import REPORT_CATEGORIES

    if category in AMBASSADOR_BYPASS_CATEGORIES:
        return False
    return category in REPORT_CATEGORIES


def next_misconduct_outcome(current: str | None) -> str:
    """The next step on the Section 6.6 misconduct ladder.

    Returns an rsn_sanctions rung ('warning', 'restriction') or the
    BAN_DECISION state once the rungs are exhausted. It can never
    return 'purge': purge belongs to the integrity pipeline, and a
    misconduct history is not an integrity finding. A current value
    from any other doctrine (including 'purge') is rejected, so the
    two systems cannot be silently cross-wired.
    """
    if current is None:
        return MISCONDUCT_LADDER[0]
    if current == BAN_DECISION:
        return BAN_DECISION
    if current in MISCONDUCT_LADDER:
        idx = MISCONDUCT_LADDER.index(current)
        if idx + 1 < len(MISCONDUCT_LADDER):
            return MISCONDUCT_LADDER[idx + 1]
        return BAN_DECISION
    raise ValueError(f"not a misconduct ladder state: {current!r}")


def severe_offense_route() -> SevereRoute:
    """Severe offenses skip the ladder entirely (Section 6.6): a
    temporary safety suspension now, the ban decision next."""
    return SevereRoute(
        disposition="temporary_safety_suspension",
        decision=BAN_DECISION,
        max_suspension_hours=SEVERE_MAX_SUSPENSION_HOURS,
        ladder_applies=False,
    )


def next_purge_stage(current: str) -> str:
    """Advance the Section 6.7 integrity pipeline one stage. Purge
    is terminal and reachable only by walking the due-process
    stages in order; there is no shortcut input that lands here
    from the misconduct ladder."""
    if current not in PURGE_PIPELINE_STAGES:
        raise ValueError(f"unknown purge pipeline stage: {current!r}")
    idx = PURGE_PIPELINE_STAGES.index(current)
    return PURGE_PIPELINE_STAGES[min(idx + 1, len(PURGE_PIPELINE_STAGES) - 1)]
