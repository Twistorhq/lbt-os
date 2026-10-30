"""TW-800 Twistor Trades agent tool gateway - service behavior.

The pipeline for one MCP-style tool call:

1. Resolve policy (models.DEFAULT_RULES, first match, default deny).
2. Write the 'requested' audit row FIRST. If the audit write fails,
   the call is refused (reason audit_unavailable): a call we cannot
   log is a call we do not run. No audit, no execution.
3. A bound call asks the TW-768 ledger (agentperms.check_permission)
   at invoke time. No grant, deny, revoked, over cap: refused here,
   whatever the gateway policy says.
4. Mode allow executes; mode ask parks the call as an approval row
   and returns pending_approval; mode deny refuses.
5. decide() settles a parked call. On approval the ledger check
   runs AGAIN at execution time (the TW-768 fencing law: a
   revocation that landed while the call waited still stops it),
   then the executor runs with the stored args.
6. Every transition appends an audit row. Executor exceptions are
   caught and recorded as 'failed' outcomes: the gateway fails the
   call closed and the host keeps running (self-healing law).

Audit rows are hash-chained per org: row_hash covers prev_hash
plus the row's canonical content - org, call, seq, kind, tool,
action class, channel, actor, grant attribution, the args
fingerprint and key names, detail, and the timestamp normalized
to UTC Z form. ONE function (_hash_content) builds that content
for both the write path and verify_chain, so the two can never
drift: rewriting any covered field, or deleting a row, breaks
verification. Rows carry an args fingerprint (sha256 of canonical
JSON + the argument key names), never arg values - approval rows
hold the values, briefly, because execution needs them.

Executors: the default executor dispatches to the real in-house
surfaces - flows.mcp.call_tool for flows.* and notify.service.send
for notify.send. Tests inject executors against the same pipeline.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..agentperms import service as ledger
from . import models
from .models import GatewayError, GatewayOutcome

Executor = Callable[[str, dict[str, Any]], dict[str, Any]]

GENESIS_HASH = "GENESIS"


# -- time -----------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# -- audit (append-only, hash-chained) -------------------------------------


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def args_fingerprint(args: dict[str, Any]) -> tuple[str, list[str]]:
    """(sha256 of canonical args, sorted arg key names). Never values
    beyond the hash: the audit log proves WHAT was called without
    becoming a second copy of the call's payload."""
    return hashlib.sha256(_canonical(args).encode()).hexdigest(), sorted(args.keys())


def _row_hash(prev_hash: str, content: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(content)).encode()).hexdigest()


def _normalized_at(value: Any) -> str:
    """The one timestamp form the chain hashes: UTC, Z suffix.

    The write path inserts 'Z'; PostgREST returns TIMESTAMPTZ as
    '+00:00'; a driver may hand back a datetime. All three are the
    same instant and must hash identically, so both the write and
    verify paths normalize through here. A value that cannot be
    parsed at all falls back to its raw string form: the write path
    never produces one, so a stored row carrying one is tampered
    and its hash simply will not match."""
    try:
        parsed = _parse_ts(value)
    except (ValueError, TypeError):
        return str(value)
    return _ts(parsed) if parsed is not None else str(value)


def _hash_content(row: dict[str, Any]) -> dict[str, Any]:
    """The canonical hashed content of an audit row.

    Single source for _append_audit (write) and verify_chain
    (verify): the field set is defined exactly once, so coverage
    cannot drift between the path that seals a row and the path
    that checks it. Covers every attribution-bearing field -
    channel, grant_id, args_keys, and detail included, because
    those are the fields an insider would rewrite."""
    return {
        "org_id": row["org_id"],
        "call_id": row["call_id"],
        "seq": int(row["seq"]),
        "kind": row["kind"],
        "tool": row["tool"],
        "action_class": row.get("action_class"),
        "channel": row.get("channel"),
        "actor": row["actor"],
        "grant_id": row.get("grant_id"),
        "args_hash": row["args_hash"],
        "args_keys": row.get("args_keys") or [],
        "detail": row.get("detail") or {},
        "at": _normalized_at(row["at"]),
    }


def _last_audit_row(db: Any, org_id: str) -> dict[str, Any] | None:
    res = (
        db.table("agt_tool_calls")
        .select("*")
        .eq("org_id", org_id)
        .order("seq", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _append_audit(
    db: Any,
    org_id: str,
    call_id: str,
    kind: str,
    *,
    tool: str,
    action_class: str | None,
    actor: str,
    args_hash: str,
    args_keys: list[str],
    grant_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one chained audit row. Raises on any write failure;
    callers on the invoke path turn that into a fail-closed refusal."""
    last = _last_audit_row(db, org_id)
    seq = int(last["seq"]) + 1 if last else 1
    prev_hash = last["row_hash"] if last else GENESIS_HASH
    row = {
        "org_id": org_id,
        "call_id": call_id,
        "seq": seq,
        "kind": kind,
        "tool": tool,
        "action_class": action_class,
        "channel": models.outcome_channel(action_class),
        "actor": actor,
        "grant_id": grant_id,
        "args_hash": args_hash,
        "args_keys": args_keys,
        "detail": detail or {},
        "at": _ts(_now()),
        "prev_hash": prev_hash,
    }
    row["row_hash"] = _row_hash(prev_hash, _hash_content(row))
    return db.table("agt_tool_calls").insert(row).execute().data[0]


def _audit_best_effort(
    db: Any, org_id: str, call_id: str, kind: str, **kw: Any
) -> None:
    """Post-decision audit writes must not crash the host either: a
    failed outcome-row write is swallowed here (the pre-execution
    'requested' row already gated the call; verify_chain surfaces a
    broken chain loudly at audit time)."""
    try:
        _append_audit(db, org_id, call_id, kind, **kw)
    except Exception:
        pass


def list_calls(db: Any, org_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """The per-call audit feed for the org, newest first. This is
    Spec 6's gateway source: every call, every decision, chained."""
    res = (
        db.table("agt_tool_calls")
        .select("*")
        .eq("org_id", org_id)
        .order("seq", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def verify_chain(db: Any, org_id: str) -> dict[str, Any]:
    """Recompute the org's hash chain from genesis.

    Returns {"ok": bool, "rows": n, "broken_seq": seq|None}. A
    tampered row fails its own hash; a deleted row fails the next
    row's prev_hash link. The hashed content is _hash_content -
    the same function the write path seals with - and timestamps
    are normalized first, so a healthy chain verifies identically
    however the database serializes TIMESTAMPTZ."""
    res = (
        db.table("agt_tool_calls")
        .select("*")
        .eq("org_id", org_id)
        .order("seq", desc=False)
        .execute()
    )
    rows = res.data or []
    prev_hash = GENESIS_HASH
    for expected_seq, row in enumerate(rows, start=1):
        if int(row["seq"]) != expected_seq or row["prev_hash"] != prev_hash:
            return {"ok": False, "rows": len(rows), "broken_seq": int(row["seq"])}
        if _row_hash(prev_hash, _hash_content(row)) != row["row_hash"]:
            return {"ok": False, "rows": len(rows), "broken_seq": int(row["seq"])}
        prev_hash = row["row_hash"]
    return {"ok": True, "rows": len(rows), "broken_seq": None}


# -- executors --------------------------------------------------------------


def default_executor(db: Any, org_id: str) -> Executor:
    """Dispatch to the real in-house tool surfaces.

    Local imports keep the package importable without the flows /
    notify dependency graph loaded (same discipline as the
    agentperms router's identity import)."""

    def run(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool.startswith("flows."):
            from ..flows import mcp as flows_mcp

            return flows_mcp.call_tool(db, org_id, tool, args)
        if tool == "notify.send":
            from ..notify import service as notify_service

            return notify_service.send(
                db,
                org_id,
                channel=str(args.get("channel") or ""),
                to=str(args.get("to") or ""),
                subject=args.get("subject"),
                body=str(args.get("body") or ""),
                variables=args.get("variables"),
                template_name=args.get("template_name"),
                workflow=args.get("workflow"),
                metadata=args.get("metadata"),
            )
        raise GatewayError(f"no executor for tool {tool!r}")

    return run


# -- the pipeline -------------------------------------------------------------


def _ledger_gate(
    db: Any,
    org_id: str,
    action_class: str,
    *,
    amount_cents: int | None,
) -> tuple[bool, str, str | None]:
    """Ask the TW-768 ledger. Returns (allowed, reason, grant_id).
    A ledger error is a denial, never a pass and never a crash."""
    try:
        decision = ledger.check_permission(
            db, org_id, action_class, amount_cents=amount_cents
        )
    except Exception:
        return False, "ledger_error", None
    return decision.allowed, decision.reason, decision.grant_id


def _execute(
    db: Any,
    org_id: str,
    call_id: str,
    tool: str,
    args: dict[str, Any],
    *,
    action_class: str | None,
    actor: str,
    args_hash: str,
    args_keys: list[str],
    grant_id: str | None,
    executor: Executor,
) -> GatewayOutcome:
    """Run the executor and record the outcome. Never raises for an
    executor failure: 'failed' is an outcome, not an exception."""
    base = {
        "call_id": call_id,
        "tool": tool,
        "action_class": action_class,
        "channel": models.outcome_channel(action_class),
        "grant_id": grant_id,
    }
    try:
        result = executor(tool, args)
    except Exception as e:  # executor failure: fail the call, not the host
        _audit_best_effort(
            db,
            org_id,
            call_id,
            "failed",
            tool=tool,
            action_class=action_class,
            actor=actor,
            args_hash=args_hash,
            args_keys=args_keys,
            grant_id=grant_id,
            detail={"error_type": type(e).__name__},
        )
        return GatewayOutcome(
            status="failed", reason="execution_failed", result=None, **base
        )
    _audit_best_effort(
        db,
        org_id,
        call_id,
        "executed",
        tool=tool,
        action_class=action_class,
        actor=actor,
        args_hash=args_hash,
        args_keys=args_keys,
        grant_id=grant_id,
        detail={
            "result_keys": sorted(result.keys()) if isinstance(result, dict) else []
        },
    )
    return GatewayOutcome(
        status="executed",
        reason="executed",
        result=result if isinstance(result, dict) else {"value": result},
        **base,
    )


def invoke(
    db: Any,
    org_id: str,
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    actor: str,
    amount_cents: int | None = None,
    executor: Executor | None = None,
    rules: tuple[models.ToolRule, ...] = models.DEFAULT_RULES,
) -> GatewayOutcome:
    """Gate one tool call. Refusals are outcomes, not exceptions."""
    args = args or {}
    run = executor or default_executor(db, org_id)
    call_id = str(uuid.uuid4())
    args_hash, args_keys = args_fingerprint(args)
    policy = models.resolve_policy(tool, args, rules)
    base = {
        "call_id": call_id,
        "tool": tool,
        "action_class": policy.action_class,
        "channel": models.outcome_channel(policy.action_class),
    }

    # Step 1: the audit row gates the call. No audit, no execution.
    try:
        _append_audit(
            db,
            org_id,
            call_id,
            "requested",
            tool=tool,
            action_class=policy.action_class,
            actor=actor,
            args_hash=args_hash,
            args_keys=args_keys,
            detail={"mode": policy.mode, "rule_index": policy.rule_index},
        )
    except Exception:
        return GatewayOutcome(
            status="denied", reason="audit_unavailable", result=None, **base
        )

    def denied(reason: str, grant_id: str | None = None) -> GatewayOutcome:
        _audit_best_effort(
            db,
            org_id,
            call_id,
            "denied",
            tool=tool,
            action_class=policy.action_class,
            actor=actor,
            args_hash=args_hash,
            args_keys=args_keys,
            grant_id=grant_id,
            detail={"reason": reason},
        )
        return GatewayOutcome(
            status="denied",
            reason=reason,
            grant_id=grant_id,
            result=None,
            **base,
        )

    # Step 2: policy shape. Bind failure and deny refuse outright.
    if policy.bind_error is not None:
        return denied("unresolvable_binding")
    if policy.mode == "deny":
        return denied(
            "policy_deny" if policy.rule_index is not None else "unknown_tool"
        )

    # Step 3: the ledger decides for bound calls.
    grant_id: str | None = None
    if policy.action_class is not None:
        allowed, reason, grant_id = _ledger_gate(
            db, org_id, policy.action_class, amount_cents=amount_cents
        )
        if not allowed:
            return denied(reason, grant_id)

    # Step 4: ask parks the call for the owner's per-call decision.
    if policy.mode == "ask":
        expires = _now() + timedelta(minutes=models.APPROVAL_TTL_MINUTES)
        row = {
            "org_id": org_id,
            "call_id": call_id,
            "tool": tool,
            "action_class": policy.action_class,
            "args": args,
            "args_hash": args_hash,
            "amount_cents": amount_cents,
            "requested_by": actor,
            "status": "pending",
            "expires_at": _ts(expires),
        }
        try:
            saved = db.table("agt_tool_approvals").insert(row).execute().data[0]
        except Exception:
            return denied("approval_store_unavailable")
        _audit_best_effort(
            db,
            org_id,
            call_id,
            "approval_requested",
            tool=tool,
            action_class=policy.action_class,
            actor=actor,
            args_hash=args_hash,
            args_keys=args_keys,
            grant_id=grant_id,
            detail={"approval_id": saved.get("id"), "expires_at": _ts(expires)},
        )
        return GatewayOutcome(
            status="pending_approval",
            reason="approval_requested",
            grant_id=grant_id,
            approval_id=saved.get("id"),
            result=None,
            **base,
        )

    # Step 5: allow executes now.
    return _execute(
        db,
        org_id,
        call_id,
        tool,
        args,
        action_class=policy.action_class,
        actor=actor,
        args_hash=args_hash,
        args_keys=args_keys,
        grant_id=grant_id,
        executor=run,
    )


# -- approvals ----------------------------------------------------------------


def _fetch_approval(db: Any, org_id: str, approval_id: str) -> dict[str, Any] | None:
    res = (
        db.table("agt_tool_approvals")
        .select("*")
        .eq("org_id", org_id)
        .eq("id", approval_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _set_approval_status(
    db: Any,
    approval: dict[str, Any],
    status: str,
    *,
    actor: str | None = None,
) -> dict[str, Any]:
    patch: dict[str, Any] = {"status": status}
    if actor is not None:
        patch["decided_by"] = actor
        patch["decided_at"] = _ts(_now())
    return (
        db.table("agt_tool_approvals")
        .update(patch)
        .eq("org_id", approval["org_id"])
        .eq("id", approval["id"])
        .execute()
        .data[0]
    )


def _claim_approval(
    db: Any,
    approval: dict[str, Any],
    status: str,
    *,
    actor: str,
) -> dict[str, Any] | None:
    """Atomically move a PENDING approval to `status`.

    The status flip is a conditional update (... WHERE
    status='pending'), so exactly one of two concurrent deciders
    wins it: the winner's update matches the row, the loser's
    matches nothing and this returns None. A decide that checked a
    fetched snapshot and then updated unconditionally would let
    both racers through to execution - the claim is what makes
    decide() safe under a double-click, a client retry, or two
    admins acting at once."""
    patch = {"status": status, "decided_by": actor, "decided_at": _ts(_now())}
    res = (
        db.table("agt_tool_approvals")
        .update(patch)
        .eq("org_id", approval["org_id"])
        .eq("id", approval["id"])
        .eq("status", "pending")
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _public_approval(row: dict[str, Any]) -> dict[str, Any]:
    """An approval row with the stored arg VALUES removed.

    Approval rows are the one place values live (execution after
    approval needs them). List payloads travel to any org member,
    so they carry everything except the values: tool, status,
    fingerprint, timestamps. The values move only through
    get_approval, which the router role-gates."""
    view = dict(row)
    view.pop("args", None)
    return view


def list_approvals(
    db: Any, org_id: str, *, status: str | None = "pending", limit: int = 100
) -> list[dict[str, Any]]:
    """Parked calls for the owner surface, newest first. Arg
    values are stripped (see _public_approval)."""
    query = db.table("agt_tool_approvals").select("*").eq("org_id", org_id)
    if status is not None:
        query = query.eq("status", status)
    res = query.order("created_at", desc=True).limit(limit).execute()
    return [_public_approval(r) for r in (res.data or [])]


def get_approval(db: Any, org_id: str, approval_id: str) -> dict[str, Any] | None:
    """One approval row, arg values included. This is the read the
    router puts behind the owner/admin role gate: the values are
    recipient addresses, message bodies, and workflow inputs."""
    return _fetch_approval(db, org_id, approval_id)


def decide(
    db: Any,
    org_id: str,
    approval_id: str,
    *,
    actor: str,
    approve: bool,
    executor: Executor | None = None,
) -> GatewayOutcome:
    """Settle a parked call. Raises GatewayError for a missing or
    already-settled approval (a caller error, surfaced by the
    router as 404/409); every execution outcome is returned.

    The settle is an atomic claim, not a check-then-update: the
    fetched row is only a snapshot, so the status flip goes
    through _claim_approval (conditional on status='pending').
    Exactly one concurrent decider wins the claim and proceeds;
    the loser gets the already-settled GatewayError and never
    reaches the executor."""
    approval = _fetch_approval(db, org_id, approval_id)
    if approval is None:
        raise GatewayError(f"approval {approval_id!r} not found")
    if approval["status"] != "pending":
        raise GatewayError(
            f"approval {approval_id!r} already settled as {approval['status']!r}"
        )
    try:
        expires_at = _parse_ts(approval.get("expires_at"))
    except (ValueError, TypeError) as e:
        raise GatewayError(
            f"approval {approval_id!r} has a malformed expires_at"
        ) from e
    run = executor or default_executor(db, org_id)
    call_id = approval["call_id"]
    tool = approval["tool"]
    action_class = approval.get("action_class")
    args = approval.get("args") or {}
    args_hash, args_keys = args_fingerprint(args)
    base = {
        "call_id": call_id,
        "tool": tool,
        "action_class": action_class,
        "channel": models.outcome_channel(action_class),
        "approval_id": approval_id,
    }

    def claim(status: str) -> None:
        if _claim_approval(db, approval, status, actor=actor) is None:
            raise GatewayError(f"approval {approval_id!r} already settled")

    if expires_at is not None and _now() > expires_at:
        claim("expired")
        _audit_best_effort(
            db,
            org_id,
            call_id,
            "approval_expired",
            tool=tool,
            action_class=action_class,
            actor=actor,
            args_hash=args_hash,
            args_keys=args_keys,
        )
        return GatewayOutcome(
            status="denied", reason="approval_expired", result=None, **base
        )

    if not approve:
        claim("denied")
        _audit_best_effort(
            db,
            org_id,
            call_id,
            "approval_denied",
            tool=tool,
            action_class=action_class,
            actor=actor,
            args_hash=args_hash,
            args_keys=args_keys,
        )
        return GatewayOutcome(
            status="denied", reason="approval_denied", result=None, **base
        )

    claim("approved")
    _audit_best_effort(
        db,
        org_id,
        call_id,
        "approved",
        tool=tool,
        action_class=action_class,
        actor=actor,
        args_hash=args_hash,
        args_keys=args_keys,
    )

    # TW-806: the stored args must still be the args the owner was
    # asked about. The fingerprint sealed at park time is recomputed
    # over the stored args; a row mutated while it waited (a
    # different workflow, a different recipient) no longer matches,
    # and execution refuses. Without this check the stored hash is
    # decoration: the executor would run whatever the row says now.
    # A missing stored fingerprint fails closed the same way.
    if approval.get("args_hash") != args_hash:
        _set_approval_status(db, approval, "tampered")
        _audit_best_effort(
            db,
            org_id,
            call_id,
            "denied",
            tool=tool,
            action_class=action_class,
            actor=actor,
            args_hash=args_hash,
            args_keys=args_keys,
            detail={
                "reason": "args_hash_mismatch",
                "parked_args_hash": approval.get("args_hash"),
                "presented_args_hash": args_hash,
            },
        )
        return GatewayOutcome(
            status="denied", reason="args_hash_mismatch", result=None, **base
        )

    # Fencing: the ledger is asked again, now, at execution time. A
    # revocation that landed while the call waited stops it here.
    grant_id: str | None = None
    if action_class is not None:
        allowed, reason, grant_id = _ledger_gate(
            db,
            org_id,
            action_class,
            amount_cents=approval.get("amount_cents"),
        )
        if not allowed:
            _set_approval_status(db, approval, "fenced")
            _audit_best_effort(
                db,
                org_id,
                call_id,
                "denied",
                tool=tool,
                action_class=action_class,
                actor=actor,
                args_hash=args_hash,
                args_keys=args_keys,
                grant_id=grant_id,
                detail={"reason": reason, "fenced_after_approval": True},
            )
            return GatewayOutcome(
                status="denied",
                reason=reason,
                grant_id=grant_id,
                result=None,
                **base,
            )

    outcome = _execute(
        db,
        org_id,
        call_id,
        tool,
        args,
        action_class=action_class,
        actor=actor,
        args_hash=args_hash,
        args_keys=args_keys,
        grant_id=grant_id,
        executor=run,
    )
    if outcome.status == "executed":
        _set_approval_status(db, approval, "executed")
    elif outcome.status == "failed":
        # Terminal truth: a row left at 'approved' after a failed
        # execution reads as settled-and-ran forever. It did not.
        _set_approval_status(db, approval, "execution_failed")
    return GatewayOutcome(**{**outcome.model_dump(), "approval_id": approval_id})
