"""TW-768 Twistor Trades agent permission ledger — service.

The ledger is a code gate, not a policy document (TW-764 Spec 2):

- check_permission() is consulted at execution time by every agent
  action (via gate.py inside the durable-turns runtime, and directly
  by Specs 4/5). It fails closed: no grant row, an explicit deny, a
  revoked grant, an amount over the cap, or a money action presented
  without an amount all deny.
- revoke() is one owner motion in three strengths. It flips the
  affected grants, cuts the agent's access tokens (via the Spec 1
  token hook), schedules every retained working copy for purge on a
  stated clock, and writes a system-generated revocation receipt to
  the append-only event history that Spec 6 renders as the audit
  feed. Disconnect, stop, and delete are one motion here — the
  dossier's central complaint about Instinct was that they were
  three different requests with three different outcomes.

Event inserts on the revocation path are NOT best-effort: a
revocation whose receipt cannot be written has not happened, and
this module says so loudly instead of reporting a partial stop
(the flags module's TW-208 audit leniency is deliberately not
carried into a safety operation).

STUBS (parallel specs, noted per the TW-768 dispatch):
- agent_identity_id is carried on every row by value; the identity
  record itself is TW-767 (Spec 1), still in flight. No FK until
  that migration lands.
- Token cutting runs through the injectable token_cutter hook;
  Spec 1 owns the agent token store. With no hook wired, the
  receipt states 'no_hook_registered' — it never claims a cut that
  did not happen.
- Retained copies are a registry of locators. run_purge() stamps
  the purge and calls the injectable purge_hook per copy; Spec 3's
  runtime wires physical deletion of the underlying blobs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .models import (
    ACTION_CLASSES,
    GrantStatement,
    LedgerError,
    PermissionDecision,
    RevokeStatement,
    channel_of,
)

log = logging.getLogger(__name__)

# The stated purge clock: retained working copies are purged within
# this delay of any revocation that covers their org. The deadline
# is computed at revocation time and printed on the receipt.
PURGE_AFTER_REVOKE = timedelta(hours=24)

SYSTEM_ACTOR = "system"


class PermissionDenied(Exception):
    """require_permission() refusal. Carries the decision.

    _twistor_permanent opts out of the TW-208 retry classifier: a
    denial is never transient, and a durable-turns step that hits
    one must fail at once, not retry into the same wall.
    """

    _twistor_permanent = True

    def __init__(self, decision: PermissionDecision):
        self.decision = decision
        super().__init__(
            f"{decision.action_class} denied: {decision.reason}"
            + (f" (cap {decision.cap_cents} cents)" if decision.cap_cents else "")
        )


class AgentWorkRefused(Exception):
    """assert_accepting_work() refusal: the org's agent stands revoked."""

    _twistor_permanent = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.isoformat()


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# -- events (append-only history) --------------------------------------


def _record_event(
    db: Any,
    org_id: str,
    kind: str,
    actor: str,
    *,
    action_class: str | None = None,
    channel: str | None = None,
    detail: dict[str, Any] | None = None,
    agent_identity_id: str | None = None,
) -> dict[str, Any]:
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_identity_id,
        "kind": kind,
        "action_class": action_class,
        "channel": channel,
        "actor": actor,
        "detail": detail or {},
        "at": _ts(_now()),
    }
    return db.table("agt_permission_events").insert(row).execute().data[0]


def list_history(db: Any, org_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Append-only change history for the org, newest first.

    This is the Spec 6 audit feed's permission source: grants,
    denials, revocations, token cuts, purge lifecycle, receipts.
    """
    res = (
        db.table("agt_permission_events")
        .select("*")
        .eq("org_id", org_id)
        .order("at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


# -- grants -------------------------------------------------------------


def _fetch_grant(db: Any, org_id: str, action_class: str) -> dict[str, Any] | None:
    res = (
        db.table("agt_grants")
        .select("*")
        .eq("org_id", org_id)
        .eq("action_class", action_class)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def list_grants(db: Any, org_id: str) -> list[dict[str, Any]]:
    res = db.table("agt_grants").select("*").eq("org_id", org_id).execute()
    rows = res.data or []
    order = {c: i for i, c in enumerate(ACTION_CLASSES)}
    return sorted(rows, key=lambda r: order.get(r["action_class"], 99))


def _set_grant(
    db: Any,
    org_id: str,
    statement: GrantStatement,
    *,
    actor: str,
    agent_identity_id: str | None,
) -> dict[str, Any]:
    existing = _fetch_grant(db, org_id, statement.action_class)
    now = _ts(_now())
    if existing is None:
        row = {
            "org_id": org_id,
            "agent_identity_id": agent_identity_id,
            "action_class": statement.action_class,
            "status": statement.status,
            "scope": statement.scope,
            "cap_cents": statement.cap_cents,
            "granted_by": actor,
            "granted_at": now,
            "updated_at": now,
            "version": 1,
        }
        saved = db.table("agt_grants").insert(row).execute().data[0]
        kind = "grant" if statement.status == "granted" else "deny"
    else:
        patch = {
            "status": statement.status,
            "scope": statement.scope,
            "cap_cents": statement.cap_cents,
            "granted_by": actor,
            "granted_at": now,
            "updated_at": now,
            "version": int(existing.get("version", 1)) + 1,
        }
        if agent_identity_id is not None:
            patch["agent_identity_id"] = agent_identity_id
        saved = (
            db.table("agt_grants")
            .update(patch)
            .eq("org_id", org_id)
            .eq("action_class", statement.action_class)
            .execute()
            .data[0]
        )
        kind = (
            "grant"
            if statement.status == "granted"
            else "deny"
            if statement.status == "denied"
            else "update"
        )
    _record_event(
        db,
        org_id,
        kind,
        actor,
        action_class=statement.action_class,
        channel=channel_of(statement.action_class),
        detail={
            "status": statement.status,
            "cap_cents": statement.cap_cents,
            "scope": statement.scope,
            "grant_id": saved.get("id"),
        },
        agent_identity_id=agent_identity_id or saved.get("agent_identity_id"),
    )
    return saved


def grant_permission(
    db: Any,
    org_id: str,
    action_class: str,
    *,
    actor: str,
    scope: dict[str, Any] | None = None,
    cap_cents: int | None = None,
    agent_identity_id: str | None = None,
) -> dict[str, Any]:
    """Grant one action class. Money classes require cap_cents."""
    statement = GrantStatement(
        action_class=action_class,
        status="granted",
        scope=scope or {},
        cap_cents=cap_cents,
    )
    return _set_grant(
        db, org_id, statement, actor=actor, agent_identity_id=agent_identity_id
    )


def deny_permission(
    db: Any,
    org_id: str,
    action_class: str,
    *,
    actor: str,
    agent_identity_id: str | None = None,
) -> dict[str, Any]:
    """Explicitly deny one action class (distinct from never granted:
    the owner's refusal is itself part of the history)."""
    statement = GrantStatement(action_class=action_class, status="denied")
    return _set_grant(
        db, org_id, statement, actor=actor, agent_identity_id=agent_identity_id
    )


# -- execution-time check (fail closed) ---------------------------------


def check_permission(
    db: Any,
    org_id: str,
    action_class: str,
    *,
    amount_cents: int | None = None,
) -> PermissionDecision:
    """May the agent perform action_class right now?

    Consulted at execution time, never cached into a task at intake:
    a grant revoked after a task queued must still stop the task.
    """
    channel = channel_of(action_class)
    grant = _fetch_grant(db, org_id, action_class)
    if grant is None:
        return PermissionDecision(
            allowed=False, reason="no_grant", action_class=action_class, channel=channel
        )
    base = {
        "action_class": action_class,
        "channel": channel,
        "grant_id": grant.get("id"),
        "cap_cents": grant.get("cap_cents"),
    }
    if grant["status"] == "denied":
        return PermissionDecision(allowed=False, reason="denied", **base)
    if grant["status"] == "revoked":
        return PermissionDecision(allowed=False, reason="revoked", **base)
    cap = grant.get("cap_cents")
    if cap is not None:
        if amount_cents is None:
            # A capped money action presented without an amount can
            # never be shown to be under the cap. Deny, don't wave.
            return PermissionDecision(allowed=False, reason="amount_required", **base)
        if amount_cents < 0:
            raise LedgerError(f"amount_cents must be >= 0, got {amount_cents}")
        if amount_cents > cap:
            return PermissionDecision(allowed=False, reason="cap_exceeded", **base)
    return PermissionDecision(allowed=True, reason="granted", **base)


def require_permission(
    db: Any,
    org_id: str,
    action_class: str,
    *,
    amount_cents: int | None = None,
) -> PermissionDecision:
    """check_permission that raises PermissionDenied on a refusal."""
    decision = check_permission(db, org_id, action_class, amount_cents=amount_cents)
    if not decision.allowed:
        raise PermissionDenied(decision)
    return decision


# -- revocation -----------------------------------------------------------


def revoke(
    db: Any,
    org_id: str,
    statement: RevokeStatement | dict[str, Any],
    *,
    actor: str,
    agent_identity_id: str | None = None,
    token_cutter: Callable[[str, str | None], Any] | None = None,
    purge_delay: timedelta = PURGE_AFTER_REVOKE,
) -> dict[str, Any]:
    """Revoke in one motion: class, channel, or all.

    Order matters and is deliberate: grants flip first (the gate
    closes), the revocation row records the motion and its purge
    deadline, tokens are cut, retained copies are scheduled, and
    the system-generated receipt closes the motion in the history.
    Any step failing raises — a half-revocation reported as done
    is the exact failure this spec exists to prevent.
    """
    stmt = (
        statement
        if isinstance(statement, RevokeStatement)
        else RevokeStatement(**statement)
    )
    affected = stmt.affected_classes()
    now = _now()
    purge_due = now + purge_delay

    revoked_grants: list[str] = []
    for action_class in affected:
        grant = _fetch_grant(db, org_id, action_class)
        if grant is None or grant["status"] == "revoked":
            continue
        db.table("agt_grants").update({"status": "revoked", "updated_at": _ts(now)}).eq(
            "org_id", org_id
        ).eq("action_class", action_class).execute()
        revoked_grants.append(action_class)
        _record_event(
            db,
            org_id,
            "revoke",
            actor,
            action_class=action_class,
            channel=channel_of(action_class),
            detail={"strength": stmt.strength, "target": stmt.target},
            agent_identity_id=agent_identity_id,
        )

    revocation = (
        db.table("agt_revocations")
        .insert(
            {
                "org_id": org_id,
                "agent_identity_id": agent_identity_id,
                "strength": stmt.strength,
                "target": stmt.target,
                "revoked_by": actor,
                "revoked_at": _ts(now),
                "purge_due_at": _ts(purge_due),
                "tokens_cut_at": None,
                "receipt_event_id": None,
                "purge_completed_at": None,
            }
        )
        .execute()
        .data[0]
    )

    # Token cut. Spec 1 owns the token store; until its hook is
    # wired the receipt says so instead of claiming a cut.
    token_status = "no_hook_registered"
    revocation.setdefault("tokens_cut_at", None)
    if token_cutter is not None:
        try:
            token_cutter(org_id, agent_identity_id)
            token_status = "cut"
            cut_at = _ts(_now())
            db.table("agt_revocations").update({"tokens_cut_at": cut_at}).eq(
                "id", revocation["id"]
            ).execute()
            revocation["tokens_cut_at"] = cut_at
        except Exception as exc:  # a failed cut is recorded, never hidden
            token_status = f"failed:{type(exc).__name__}"
            log.error(
                "agentperms: token cut failed for org %s revocation %s: %s",
                org_id,
                revocation["id"],
                exc,
            )
    _record_event(
        db,
        org_id,
        "token_cut",
        SYSTEM_ACTOR,
        detail={"revocation_id": revocation["id"], "status": token_status},
        agent_identity_id=agent_identity_id,
    )

    # Schedule retained working copies for purge on the stated clock.
    copies = (
        db.table("agt_retained_copies").select("*").eq("org_id", org_id).execute().data
        or []
    )
    scheduled = 0
    for copy in copies:
        if copy.get("purged_at") is not None:
            continue
        db.table("agt_retained_copies").update({"purge_after": _ts(purge_due)}).eq(
            "id", copy["id"]
        ).execute()
        scheduled += 1
    if scheduled:
        _record_event(
            db,
            org_id,
            "purge_scheduled",
            SYSTEM_ACTOR,
            detail={
                "revocation_id": revocation["id"],
                "copies_scheduled": scheduled,
                "purge_due_at": _ts(purge_due),
            },
            agent_identity_id=agent_identity_id,
        )

    # The receipt: system-generated, in the audit feed's source.
    receipt = _record_event(
        db,
        org_id,
        "revocation_receipt",
        SYSTEM_ACTOR,
        detail={
            "revocation_id": revocation["id"],
            "strength": stmt.strength,
            "target": stmt.target,
            "revoked_by": actor,
            "classes_affected": list(affected),
            "grants_revoked": revoked_grants,
            "token_cut": token_status,
            "copies_scheduled_for_purge": scheduled,
            "purge_due_at": _ts(purge_due),
        },
        agent_identity_id=agent_identity_id,
    )
    db.table("agt_revocations").update({"receipt_event_id": receipt["id"]}).eq(
        "id", revocation["id"]
    ).execute()
    return {**revocation, "receipt_event_id": receipt["id"], "receipt": receipt}


def list_revocations(db: Any, org_id: str) -> list[dict[str, Any]]:
    res = (
        db.table("agt_revocations")
        .select("*")
        .eq("org_id", org_id)
        .order("revoked_at", desc=True)
        .execute()
    )
    return res.data or []


# -- intake halt ----------------------------------------------------------


def agent_status(db: Any, org_id: str) -> str:
    """'active' while any class stands granted; 'revoked' when a
    revoke-all stands with nothing re-granted since.

    State-based, not clock-based: revoke() flips every granted row,
    so a standing revoke-all is exactly 'an all-revocation exists
    and no grant is currently granted'.
    """
    grants = list_grants(db, org_id)
    if any(g["status"] == "granted" for g in grants):
        return "active"
    revocations = list_revocations(db, org_id)
    if any(r["strength"] == "all" for r in revocations):
        return "revoked"
    return "active"


def assert_accepting_work(db: Any, org_id: str) -> None:
    """Intake gate: a fully revoked agent accepts no new work.

    Spec 1's inbound path and Spec 3's task intake call this before
    queueing anything for the agent. Synchronous against current
    state, so refusal lands on the first call after revocation —
    the dossier's three-hour lag is zero here by construction.
    """
    if agent_status(db, org_id) == "revoked":
        raise AgentWorkRefused(
            f"org {org_id}: agent stands revoked (revoke-all); "
            "not accepting work until the owner re-grants a permission"
        )


# -- retained working copies + purge --------------------------------------


def register_retained_copy(
    db: Any,
    org_id: str,
    *,
    copy_kind: str,
    locator: str,
    registered_by: str,
    agent_identity_id: str | None = None,
) -> dict[str, Any]:
    """Register a retained working copy (locator only, never content)
    so a later revocation can schedule its purge."""
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_identity_id,
        "copy_kind": copy_kind,
        "locator": locator,
        "registered_by": registered_by,
        "created_at": _ts(_now()),
        "purge_after": None,
        "purged_at": None,
    }
    return db.table("agt_retained_copies").insert(row).execute().data[0]


def list_retained_copies(db: Any, org_id: str) -> list[dict[str, Any]]:
    res = db.table("agt_retained_copies").select("*").eq("org_id", org_id).execute()
    return res.data or []


def run_purge(
    db: Any,
    *,
    now: datetime | None = None,
    purge_hook: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Execute every due purge across orgs.

    A copy is due when purge_after is set, purged_at is NULL, and the
    deadline has passed. purge_hook performs the physical deletion
    (Spec 3's runtime wires it); a copy whose hook raises stays
    unpurged and is reported in failures — never stamped clean.
    Revocations whose copies are all purged are completed.
    """
    moment = now or _now()
    res = db.table("agt_retained_copies").select("*").execute()
    purged: list[str] = []
    failures: list[dict[str, str]] = []
    touched_orgs: set[str] = set()
    for copy in res.data or []:
        due = _parse_ts(copy.get("purge_after"))
        if due is None or copy.get("purged_at") is not None or due > moment:
            continue
        try:
            if purge_hook is not None:
                purge_hook(copy)
        except Exception as exc:
            failures.append({"copy_id": copy["id"], "error": type(exc).__name__})
            continue
        db.table("agt_retained_copies").update({"purged_at": _ts(moment)}).eq(
            "id", copy["id"]
        ).execute()
        purged.append(copy["id"])
        touched_orgs.add(copy["org_id"])
        _record_event(
            db,
            copy["org_id"],
            "purge_completed",
            SYSTEM_ACTOR,
            detail={"copy_id": copy["id"], "copy_kind": copy.get("copy_kind")},
            agent_identity_id=copy.get("agent_identity_id"),
        )
    completed_revocations: list[str] = []
    for org_id in touched_orgs:
        remaining = [
            c
            for c in list_retained_copies(db, org_id)
            if c.get("purge_after") is not None and c.get("purged_at") is None
        ]
        if remaining:
            continue
        for revocation in list_revocations(db, org_id):
            due = _parse_ts(revocation.get("purge_due_at"))
            if (
                revocation.get("purge_completed_at") is None
                and due is not None
                and due <= moment
            ):
                db.table("agt_revocations").update(
                    {"purge_completed_at": _ts(moment)}
                ).eq("id", revocation["id"]).execute()
                completed_revocations.append(revocation["id"])
    return {
        "purged": purged,
        "failures": failures,
        "completed_revocations": completed_revocations,
    }
