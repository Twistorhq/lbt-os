"""TW-954 (TW-948-B7) — the composed-revocation contract.

THIS MODULE IS THE CONTRACT, NOT THE PROPAGATION. The policy/
floor half of B7 lands here; wiring the stop into the B4 job
service ticker/execution and the B5 recovery paths is the
propagation half, which lands when B4/B5 merge. What this
module pins down — so that half is a wiring job, not a design
job — is the signal's shape, the epoch fence consumers check,
and the receipt each target writes.

The composed stop: one owner revocation (service.revoke) must
reach four targets, and the owner reads ONE stop with FOUR
confirmations in the same agt_permission_events history:

    STOP_TARGETS = ("ledger", "jobs", "triggers",
                    "delegated_children")

- ledger: EXISTS today. service.revoke() flips the grants and
  writes the system revocation_receipt event. That receipt is
  the ledger target's confirmation.
- jobs (B4): on consuming a stop signal, pause every job for
  the agent and fence in-flight fires at their next step.
- triggers (B11): disable every trigger for the agent.
- delegated_children (B10): fence queued and running children
  at their next step boundary.

Each non-ledger target confirms by writing ONE event of kind
TARGET_RECEIPT_KIND ('composed_stop_receipt') with detail
{revocation_id, target} into agt_permission_events, scoped to
the revocation's (org, agent). composed_stop_status() reads
those receipts and reports each target honestly: a target with
no receipt reads False, and 'complete' is True only when all
four stand confirmed. Nothing here claims a stop reached a
target it has not reached.

The epoch fence: every revocation covering a scope bumps that
scope's epoch (the count of covering agt_revocations rows — a
monotonic counter that needs no new state). A consumer that
queues or starts work snapshots current_epoch(); before each
step it calls assert_not_fenced(epoch_seen). A revocation that
lands while work is queued therefore fences the work's next
step, and nothing resumes after revocation without an owner
action: resumption means re-granting (the ledger layer) and
taking a FRESH epoch snapshot — an old snapshot never passes
again once a newer revocation exists. B4's ticker, B5's
recovery, and B10's children are all consumers of exactly this
check; TW-768's LedgerFence remains the per-action ledger
check and the two compose (epoch first, ledger per step).
"""

from __future__ import annotations

from typing import Any

from . import service
from .models import LedgerError, _StrictBase

STOP_TARGETS: tuple[str, ...] = ("ledger", "jobs", "triggers", "delegated_children")

# The event kind every non-ledger stop target writes to confirm.
TARGET_RECEIPT_KIND = "composed_stop_receipt"

# The existing ledger confirmation: service.revoke()'s receipt.
LEDGER_RECEIPT_KIND = "revocation_receipt"


class ComposedStopFenced(Exception):
    """assert_not_fenced() refusal: a revocation landed after
    the consumer's epoch snapshot. Permanent, like every other
    fence in this package: queued work that hits it does not
    retry into the same wall."""

    _twistor_permanent = True


class ComposedStopSignal(_StrictBase):
    """One composed stop, as issued from one revocation row.

    Consumers match on (org_id, agent_identity_id, epoch): a
    signal fences every consumer whose snapshot epoch is older.
    agent_identity_id None is the org-wide stop (every agent in
    the org is a target; per-agent signals are built per agent
    when the propagation half fans it out).
    """

    revocation_id: str
    org_id: str
    agent_identity_id: str | None = None
    epoch: int
    issued_at: str
    actor: str
    targets: tuple[str, ...] = STOP_TARGETS

    def model_post_init(self, _ctx: Any) -> None:
        if self.epoch < 1:
            raise LedgerError(f"epoch must be >= 1, got {self.epoch}")
        for target in self.targets:
            if target not in STOP_TARGETS:
                raise LedgerError(
                    f"unknown stop target {target!r}; want one of {STOP_TARGETS}"
                )


def _covers(revocation: dict[str, Any], agent_identity_id: str | None) -> bool:
    """A revocation covers a scope when it is that agent's own,
    or org-wide (agent NULL) — the TW-787 scoping rule."""
    owner = revocation.get("agent_identity_id")
    return owner is None or owner == agent_identity_id


def current_epoch(db: Any, org_id: str, agent_identity_id: str | None = None) -> int:
    """The scope's revocation epoch: how many revocations cover
    it. Monotonic by construction (revocations are append-only
    rows), so an epoch comparison is a fence."""
    return sum(
        1 for r in service.list_revocations(db, org_id) if _covers(r, agent_identity_id)
    )


def assert_not_fenced(
    db: Any, org_id: str, agent_identity_id: str | None, *, epoch_seen: int
) -> None:
    """The consumer check. Raise ComposedStopFenced when a
    revocation covering this scope landed after epoch_seen."""
    current = current_epoch(db, org_id, agent_identity_id)
    if current > epoch_seen:
        scope = (
            f"agent {agent_identity_id} in org {org_id}"
            if agent_identity_id
            else f"org {org_id}"
        )
        raise ComposedStopFenced(
            f"{scope}: revocation epoch advanced {epoch_seen} -> {current}; "
            "queued work is fenced and resumes only on owner action"
        )


def build_stop_signal(
    db: Any,
    revocation: dict[str, Any],
    *,
    agent_identity_id: str | None = None,
) -> ComposedStopSignal:
    """The composed-stop signal for one revocation row (as
    returned by service.revoke). The propagation half delivers
    this to the jobs/triggers/delegation consumers."""
    org_id = revocation["org_id"]
    return ComposedStopSignal(
        revocation_id=revocation["id"],
        org_id=org_id,
        agent_identity_id=(
            agent_identity_id
            if agent_identity_id is not None
            else revocation.get("agent_identity_id")
        ),
        epoch=current_epoch(db, org_id, agent_identity_id),
        issued_at=revocation.get("revoked_at", ""),
        actor=revocation.get("revoked_by", ""),
    )


def composed_stop_status(
    db: Any,
    org_id: str,
    revocation_id: str,
    *,
    agent_identity_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Which stop targets have confirmed this revocation, read
    from the one event history. Honest by construction: an
    unconfirmed target reads False."""
    status: dict[str, Any] = {target: False for target in STOP_TARGETS}
    for event in service.list_history(db, org_id, limit=limit):
        if agent_identity_id is not None and event.get("agent_identity_id") not in (
            None,
            agent_identity_id,
        ):
            continue
        detail = event.get("detail") or {}
        if detail.get("revocation_id") != revocation_id:
            continue
        if event["kind"] == LEDGER_RECEIPT_KIND:
            status["ledger"] = True
        elif event["kind"] == TARGET_RECEIPT_KIND:
            target = detail.get("target")
            if target in status:
                status[target] = True
    status["complete"] = all(status[t] for t in STOP_TARGETS)
    return status
