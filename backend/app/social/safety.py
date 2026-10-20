"""TW-650 Twistor Social — safety helpers (pure functions).

Spec Section 6.6: the consequence ladder and the appeals workflow.
Appeals are acknowledged within 24 hours and decided within 7 days
by a fresh reviewer. Sanction rungs below a ban live in rsn_sanctions
(warning, restriction, purge); bans are their own identity-anchored
instrument (rsn_bans, Section 6.2). Severe offenses skip rungs.

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

SANCTION_LADDER = ("warning", "restriction", "purge")


@dataclass(frozen=True)
class AppealSla:
    ack_due_at: datetime
    decision_due_at: datetime


def appeal_sla(created_at: datetime) -> AppealSla:
    return AppealSla(
        ack_due_at=created_at + APPEAL_ACK_SLA,
        decision_due_at=created_at + APPEAL_DECISION_SLA,
    )


def ambassador_may_triage(category: str) -> bool:
    """True only for known, non-serious categories. Unknown values
    fail closed to staff."""
    from .models import REPORT_CATEGORIES

    if category in AMBASSADOR_BYPASS_CATEGORIES:
        return False
    return category in REPORT_CATEGORIES


def next_sanction(current: str | None, *, severe: bool = False) -> str:
    """The next rung of the consequence ladder. Severe offenses skip
    one rung; purge is terminal (identity-anchored, Section 6.7)."""
    if current is None:
        idx = -1
    elif current in SANCTION_LADDER:
        idx = SANCTION_LADDER.index(current)
    else:
        raise ValueError(f"unknown sanction level: {current!r}")
    step = 2 if severe else 1
    return SANCTION_LADDER[min(idx + step, len(SANCTION_LADDER) - 1)]
