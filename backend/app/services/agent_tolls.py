"""Agent action toll emission helpers (TW-950, TW-948-B3).

One helper per Section 11 agent event, at the rates already decided
in the Tori product spec (Sections 8.4 and 11). Every helper goes
through services/toll_service.record_toll_event, and every helper
takes the fire id or task id that owns the action as its idempotency
root: the ledger key is "<event>:<root>", so a retried emission for
the same fire or task returns the existing row instead of tolling
twice, and distinct fires or tasks toll separately. Callers that run
several distinct actions of one kind under a single root pass
idempotency_suffix to separate those keys.

This module wires no emission points into agent actions; the tickets
that build each action (job fires, delivery, delegation) call these
helpers. Two Section 11 gaps stay unnamed and unpriced here, exactly
as the spec leaves them: there is no per-call toll helper and no
standalone background-review toll helper.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

from typing import Any

from ..config import (
    BOOKING_RECOVERED_ANCHOR_PERCENT,
    BOOKING_RECOVERED_EVENT,
)
from .toll_service import record_toll_event

EMAIL_ACTION_EVENT = "agent.email_action"
TASK_COMPLETED_EVENT = "agent.task_completed"
BOOKING_CONFIRMED_EVENT = "agent.booking_confirmed"
INVOICE_CHASE_STEP_EVENT = "agent.invoice_chase_step"
ESTIMATE_CHASE_STEP_EVENT = "agent.estimate_chase_step"


def _idempotency_key(event_type: str, root_id: str, suffix: str | None) -> str:
    if not root_id:
        raise ValueError(
            "root_id (fire or task id) is required as the idempotency root"
        )
    key = f"{event_type}:{root_id}"
    return f"{key}:{suffix}" if suffix else key


def _emit_fixed(
    db: Any,
    org_id: str,
    event_type: str,
    root_id: str,
    *,
    source_type: str | None,
    source_id: str | None,
    idempotency_suffix: str | None,
) -> dict:
    return record_toll_event(
        db,
        org_id,
        event_type,
        quantity=1,
        idempotency_key=_idempotency_key(event_type, root_id, idempotency_suffix),
        source_type=source_type or "agent_task",
        source_id=source_id or root_id,
    )


def emit_email_action(
    db: Any,
    org_id: str,
    root_id: str,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    idempotency_suffix: str | None = None,
) -> dict:
    """Toll one agent email action at $0.05 (agent.email_action)."""
    return _emit_fixed(
        db,
        org_id,
        EMAIL_ACTION_EVENT,
        root_id,
        source_type=source_type,
        source_id=source_id,
        idempotency_suffix=idempotency_suffix,
    )


def emit_task_completed(
    db: Any,
    org_id: str,
    root_id: str,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    idempotency_suffix: str | None = None,
) -> dict:
    """Toll one completed agent task at $0.25 (agent.task_completed).

    The trigger source (conversation, schedule, or event) does not
    change the toll, per spec Section 8.4.
    """
    return _emit_fixed(
        db,
        org_id,
        TASK_COMPLETED_EVENT,
        root_id,
        source_type=source_type,
        source_id=source_id,
        idempotency_suffix=idempotency_suffix,
    )


def emit_booking_confirmed(
    db: Any,
    org_id: str,
    root_id: str,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    idempotency_suffix: str | None = None,
) -> dict:
    """Toll one confirmed booking at $0.50 (agent.booking_confirmed)."""
    return _emit_fixed(
        db,
        org_id,
        BOOKING_CONFIRMED_EVENT,
        root_id,
        source_type=source_type,
        source_id=source_id,
        idempotency_suffix=idempotency_suffix,
    )


def emit_invoice_chase_step(
    db: Any,
    org_id: str,
    root_id: str,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    idempotency_suffix: str | None = None,
) -> dict:
    """Toll one invoice chase step at $0.05 (agent.invoice_chase_step)."""
    return _emit_fixed(
        db,
        org_id,
        INVOICE_CHASE_STEP_EVENT,
        root_id,
        source_type=source_type,
        source_id=source_id,
        idempotency_suffix=idempotency_suffix,
    )


def emit_estimate_chase_step(
    db: Any,
    org_id: str,
    root_id: str,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    idempotency_suffix: str | None = None,
) -> dict:
    """Toll one estimate chase step at $0.05 (agent.estimate_chase_step)."""
    return _emit_fixed(
        db,
        org_id,
        ESTIMATE_CHASE_STEP_EVENT,
        root_id,
        source_type=source_type,
        source_id=source_id,
        idempotency_suffix=idempotency_suffix,
    )


def emit_booking_recovered(
    db: Any,
    org_id: str,
    root_id: str,
    *,
    recovered_value_cents: int,
    percent: float | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    idempotency_suffix: str | None = None,
) -> dict:
    """Toll a recovered booking as a percent of its recovered value.

    The percent defaults to the Section 11 anchor of 3 percent and
    must sit inside the 2 to 4 percent band. The amount is computed
    here and re-validated against the band by record_toll_event, so
    an out-of-band percent raises ValueError and tolls nothing.
    """
    pct = BOOKING_RECOVERED_ANCHOR_PERCENT if percent is None else percent
    amount_cents = int(round(recovered_value_cents * pct / 100))
    return record_toll_event(
        db,
        org_id,
        BOOKING_RECOVERED_EVENT,
        quantity=1,
        amount_cents=amount_cents,
        base_value_cents=recovered_value_cents,
        idempotency_key=_idempotency_key(
            BOOKING_RECOVERED_EVENT, root_id, idempotency_suffix
        ),
        source_type=source_type or "agent_task",
        source_id=source_id or root_id,
    )
