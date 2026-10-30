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

Audit rows are hash-chained per org (row_hash covers prev_hash and
the row's canonical content) so tampering or deletion breaks
verify_chain. Rows carry an args fingerprint (sha256 of canonical
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
    at = _ts(_now())
    content = {
        "org_id": org_id,
        "call_id": call_id,
        "seq": seq,
        "kind": kind,
        "tool": tool,
        "action_class": action_class,
        "actor": actor,
        "args_hash": args_hash,
        "at": at,
    }
    row = {
        **content,
        "channel": models.outcome_channel(action_class),
        "grant_id": grant_id,
        "args_keys": args_keys,
        "detail": detail or {},
        "prev_hash": prev_hash,
        "row_hash": _row_hash(prev_hash, content),
    }
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
    row's prev_hash link."""
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
        content = {
            "org_id": row["org_id"],
            "call_id": row["call_id"],
            "seq": int(row["seq"]),
            "kind": row["kind"],
            "tool": row["tool"],
            "action_class": row["action_class"],
            "actor": row["actor"],
            "args_hash": row["args_hash"],
            "at": row["at"] if isinstance(row["at"], str) else _ts(row["at"]),
        }
        if _row_hash(prev_hash, content) != row["row_hash"]:
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


def list_approvals(
    db: Any, org_id: str, *, status: str | None = "pending", limit: int = 100
) -> list[dict[str, Any]]:
    """Parked calls for the owner surface, newest first."""
    query = db.table("agt_tool_approvals").select("*").eq("org_id", org_id)
    if status is not None:
        query = query.eq("status", status)
    res = query.order("created_at", desc=True).limit(limit).execute()
    return res.data or []


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
    router as 404/409); every execution outcome is returned."""
    approval = _fetch_approval(db, org_id, approval_id)
    if approval is None:
        raise GatewayError(f"approval {approval_id!r} not found")
    if approval["status"] != "pending":
        raise GatewayError(
            f"approval {approval_id!r} already settled as {approval['status']!r}"
        )
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

    expires_at = _parse_ts(approval.get("expires_at"))
    if expires_at is not None and _now() > expires_at:
        _set_approval_status(db, approval, "expired", actor=actor)
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
        _set_approval_status(db, approval, "denied", actor=actor)
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

    _set_approval_status(db, approval, "approved", actor=actor)
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
    return GatewayOutcome(**{**outcome.model_dump(), "approval_id": approval_id})
