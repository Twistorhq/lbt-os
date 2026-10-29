"""TW-768 — the permission gate inside the durable-turns runtime.

Spec 3's task runtime (Zeke) builds turns from StepDefs. Wrapping a
step in guarded_step() makes the ledger check part of the step
itself: the check runs inside the step attempt, immediately before
the handler, against current ledger state — never cached at intake.
A denial raises PermissionDenied, which the engine treats as a
permanent step failure (the exception opts out of retry via the
_twistor_permanent marker), so the turn fails at that step and no
later step executes. That is the fencing contract for in-flight
turns: a revocation that lands mid-run stops the turn at its next
step, which is exactly the TW-768 acceptance shape.

Queued work is fenced by the same ledger: a queued turn's first
guarded step checks before it acts, and intake additionally calls
service.assert_accepting_work() so a fully revoked agent's queue
never starts turning at all.

LedgerFence is the object form for call sites that are not
StepDef-shaped (Specs 4/5 sequence runners).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..durable_turns.engine import StepDef
from . import service


def guarded_step(
    db: Any,
    org_id: str,
    action_class: str,
    step: StepDef,
    *,
    amount_cents: int | None = None,
) -> StepDef:
    """Wrap a StepDef so its handler runs only under a live grant.

    The wrapper preserves the step's name and idempotency declaration
    (the durable-turns journal governs on resume; a wrapped step must
    journal identically to the step it wraps).
    """
    original = step.handler

    def handler(ctx: Any) -> Any:
        service.require_permission(db, org_id, action_class, amount_cents=amount_cents)
        return original(ctx)

    return replace(step, handler=handler)


class LedgerFence:
    """Execution-time fence for non-StepDef call sites."""

    def __init__(self, db: Any, org_id: str):
        self._db = db
        self._org_id = org_id

    def check(self, action_class: str, *, amount_cents: int | None = None):
        """Raise PermissionDenied unless the action is granted now."""
        return service.require_permission(
            self._db, self._org_id, action_class, amount_cents=amount_cents
        )

    def assert_accepting_work(self) -> None:
        """Raise AgentWorkRefused if the agent stands fully revoked."""
        service.assert_accepting_work(self._db, self._org_id)
