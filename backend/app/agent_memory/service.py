"""TW-951 agent memory: bounded curated memory service.

Every function takes (db, org_id, agent_id) and filters on all
three scope keys (org, agent, and the derived workspace key),
exactly as the B1 persistence service does. A read from another
org or another agent returns nothing; that is the isolation
contract, enforced here and pinned by the tests.

Write path, in order, with no step skippable:

1. propose_batch screens every body (screening.screen) BEFORE
   anything is staged, validates the batch against the live
   collection (duplicates, missing targets), and enforces the
   collection bound on the BATCH RESULT. Overflow is refused
   with the current entries and the exact overflow; nothing
   is staged and nothing is written.
2. Each surviving op is staged through the B1 staging table
   (persistence.stage_write, kind 'memory'), pinning the full
   target entry content by its keyed hash (for an add, the pin
   is the hash of the absent-entry marker).
3. approve_write re-checks the pin against the live entry and
   refuses stale (the staged row is marked refused_stale),
   re-runs screening and the B1 masking pipeline as defense
   in depth, re-checks the bound against live state, applies
   the write, records an audit event, and returns a receipt.
   The receipt id is a keyed digest over the staged write, so
   the agent path cannot forge one (B1 hash key discipline).
4. claim_saved_memory is the runtime-facing claim path: a
   claim of saved memory verifies only against a receipt id
   that recomputes from the audit trail inside the caller's
   scope. No receipt, no claim.

Gate: memory writes are approval-gated by default. The owner
may relax the gate per Tori (set_gate_mode, owner actors
only); a relaxed gate auto-applies staged writes through the
same approve path, receipts included. There is deliberately
no agent-callable path to the setting: a non-owner actor is
refused for every change, relax or tighten alike, so the
agent can never remove a tightening the owner put in place.
Every successful gate change records a
memory_gate_mode_changed audit event (old mode, new mode,
actor), so a relax-then-re-tighten cycle leaves a trace in
the append-only trail even when no write auto-applied.

Snapshots: assemble_snapshot reads the approved entries once,
freezes copies in a per-session registry, and returns the same
frozen snapshot for the life of the session. A write approved
mid-session is invisible to that session's snapshot and
visible to the next session's.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..persistence import service as persistence_service
from ..persistence.models import (
    StagedWriteCreate,
    hash_body,
    mask_text,
    workspace_id_for,
)
from . import screening
from .models import (
    AUDIT_EVENTS,
    COLLECTION_LIMITS,
    COLLECTIONS,
    EVENT_GATE_MODE_CHANGED,
    GATE_GATED,
    GATE_MODES,
    GATE_RELAXED,
    OP_ADD,
    OP_REMOVE,
    OP_UPDATE,
    DuplicateEntryError,
    EntryNotFoundError,
    GatePermissionError,
    MemoryClaimError,
    MemoryError,
    MemoryWriteOp,
    OverflowRefused,
    StaleTargetError,
    require_collection,
)

log = logging.getLogger(__name__)

_ABSENT_MARKER = "\x00agt-memory-absent\x00"
_APPLY_ORDER = {OP_REMOVE: 0, OP_UPDATE: 1, OP_ADD: 2}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_scope(org_id: str, agent_id: str) -> str:
    if not org_id or not agent_id:
        raise MemoryError("org_id and agent_id are required on every call")
    return workspace_id_for(org_id, agent_id)


def _scope(db: Any, table: str, org_id: str, agent_id: str) -> Any:
    return (
        db.table(table)
        .select("*")
        .eq("org_id", org_id)
        .eq("agent_identity_id", agent_id)
        .eq("workspace_id", workspace_id_for(org_id, agent_id))
    )


def _one(db: Any, table: str, org_id: str, agent_id: str, row_id: str) -> dict | None:
    rows = _scope(db, table, org_id, agent_id).eq("id", row_id).limit(1).execute().data
    return rows[0] if rows else None


def _canonical_key(entry_key: str) -> str:
    """The stored entry key: the caller's key after the B1
    masking pipeline, so the entries table, the staged target
    key, and every later lookup agree byte for byte."""
    if ":" in entry_key:
        raise MemoryError("entry_key must not contain ':'")
    key = mask_text(entry_key.strip())
    if not key:
        raise MemoryError("entry_key must be non-empty")
    return key


def _target_key(collection: str, entry_key: str) -> str:
    return f"{collection}:{entry_key}"


def _parse_target(staged: dict) -> tuple[str, str]:
    target_key = staged.get("target_key") or ""
    collection, _, entry_key = target_key.partition(":")
    require_collection(collection)
    if not entry_key:
        raise MemoryError(f"staged target {target_key!r} names no entry key")
    return collection, entry_key


def _pin_for(entry: dict | None, org_id: str, agent_id: str) -> str:
    """The pin over the full target entry content (or its
    absence), keyed and scope-separated by the B1 hash."""
    if entry is None:
        return hash_body(_ABSENT_MARKER, org_id=org_id, agent_id=agent_id)
    return hash_body(entry["body"], org_id=org_id, agent_id=agent_id)


def _receipt_id(
    org_id: str, agent_id: str, staged_id: str, body_hash: str, version: int
) -> str:
    digest = hash_body(
        f"agt-memory-receipt-v1\x00{staged_id}\x00{body_hash}\x00{version}",
        org_id=org_id,
        agent_id=agent_id,
    )
    return "mem_" + digest.split(":", 1)[1][:32]


# -- entries (the live curated collections) -----------------------


def _entry_projection(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "collection": row.get("collection"),
        "entry_key": row.get("entry_key"),
        "body": row.get("body_masked", ""),
        "version": row.get("version", 0),
        "receipt_id": row.get("receipt_id"),
        "staged_write_id": row.get("staged_write_id"),
        "updated_at": row.get("updated_at"),
    }


def list_entries(db: Any, org_id: str, agent_id: str, collection: str) -> list[dict]:
    """The current approved entries of one collection, in key
    order. Scoped: another org or agent sees an empty list."""
    _require_scope(org_id, agent_id)
    require_collection(collection)
    rows = (
        _scope(db, "agt_memory_entries", org_id, agent_id)
        .eq("collection", collection)
        .order("entry_key")
        .execute()
        .data
    )
    return [_entry_projection(r) for r in rows]


def _get_entry(
    db: Any, org_id: str, agent_id: str, collection: str, entry_key: str
) -> dict | None:
    rows = (
        _scope(db, "agt_memory_entries", org_id, agent_id)
        .eq("collection", collection)
        .eq("entry_key", entry_key)
        .limit(1)
        .execute()
        .data
    )
    return _entry_projection(rows[0]) if rows else None


def _check_bounds(
    collection: str,
    current: list[dict],
    result: dict[str, str],
    new_keys: list[str],
) -> None:
    """Enforce the hard bound on a would-be collection state.

    result maps entry_key to body; new_keys names the keys the
    proposed write adds, in proposal order, so the refusal can
    name exactly which of them overflow. Raises OverflowRefused
    with the current entries and the exact overflow when the
    result exceeds either limit; never trims to fit.
    """
    limits = COLLECTION_LIMITS[collection]
    result_entries = len(result)
    result_chars = sum(len(body) for body in result.values())
    excess_entries = max(0, result_entries - limits["max_entries"])
    excess_chars = max(0, result_chars - limits["max_chars"])
    if not excess_entries and not excess_chars:
        return
    raise OverflowRefused(
        {
            "collection": collection,
            "current_entries": current,
            "result_entries": result_entries,
            "result_chars": result_chars,
            "max_entries": limits["max_entries"],
            "max_chars": limits["max_chars"],
            "excess_entries": excess_entries,
            "excess_chars": excess_chars,
            "excess_keys": new_keys[len(new_keys) - excess_entries :]
            if excess_entries
            else [],
        }
    )


def _apply_ops_to_state(
    current: list[dict], ops: list[MemoryWriteOp]
) -> dict[str, str]:
    """Validate a batch against the live collection and return
    the resulting key-to-body state. Pure: writes nothing."""
    state = {e["entry_key"]: e["body"] for e in current}
    seen_in_batch: set[str] = set()
    for op in ops:
        key = _canonical_key(op.entry_key)
        if key in seen_in_batch:
            raise DuplicateEntryError(f"entry key {key!r} appears twice in one batch")
        seen_in_batch.add(key)
        if op.op == OP_ADD:
            if key in state:
                raise DuplicateEntryError(
                    f"entry key {key!r} already exists in this collection"
                )
            state[key] = op.body
        elif op.op == OP_UPDATE:
            if key not in state:
                raise EntryNotFoundError(f"no entry {key!r} to update")
            state[key] = op.body
        elif op.op == OP_REMOVE:
            if key not in state:
                raise EntryNotFoundError(f"no entry {key!r} to remove")
            del state[key]
    return state


# -- propose: screen, validate, stage ------------------------------


def propose_batch(
    db: Any,
    org_id: str,
    agent_id: str,
    collection: str,
    ops: list[dict],
    *,
    staged_by: str = "",
) -> list[dict]:
    """Screen, validate, and stage one batch of memory writes.

    Screening runs on every body before anything is staged;
    the bound is enforced on the batch result. Under a relaxed
    gate the staged writes are applied immediately through the
    approve path (removes first, so a swap inside the bound
    never trips the approve-time re-check) and each returned
    row carries its receipt.
    """
    _require_scope(org_id, agent_id)
    require_collection(collection)
    parsed = [MemoryWriteOp(**op) for op in ops]
    if not parsed:
        raise MemoryError("a memory batch needs at least one op")
    screened_bodies: list[str] = []
    for op in parsed:
        if op.op == OP_REMOVE:
            screened_bodies.append("")
        else:
            # ScreeningBlocked raises here, before any staging.
            screened_bodies.append(screening.screen(op.body))
    current = list_entries(db, org_id, agent_id, collection)
    result = _apply_ops_to_state(
        current,
        [
            MemoryWriteOp(op=op.op, entry_key=op.entry_key, body=body)
            for op, body in zip(parsed, screened_bodies, strict=True)
        ],
    )
    current_keys = {e["entry_key"] for e in current}
    _check_bounds(
        collection,
        current,
        result,
        [k for k in result if k not in current_keys],
    )
    by_key = {e["entry_key"]: e for e in current}
    staged_rows: list[tuple[MemoryWriteOp, dict]] = []
    for op, body in zip(parsed, screened_bodies, strict=True):
        key = _canonical_key(op.entry_key)
        live = by_key.get(key)
        row = persistence_service.stage_write(
            db,
            org_id,
            agent_id,
            StagedWriteCreate(
                kind="memory",
                target_key=_target_key(collection, key),
                target_ref=collection,
                proposed_body=body,
                pinned_target_hash=_pin_for(live, org_id, agent_id),
            ),
            staged_by=staged_by,
        )
        staged_rows.append((op, row))
    if get_gate_mode(db, org_id, agent_id) == GATE_RELAXED:
        out: list[dict] = []
        ordered = sorted(staged_rows, key=lambda pair: _APPLY_ORDER[pair[0].op])
        receipts = {
            row["id"]: approve_write(
                db, org_id, agent_id, row["id"], decided_by="gate-relaxed"
            )
            for _, row in ordered
        }
        for _, row in staged_rows:
            applied = dict(row)
            applied["status"] = "applied"
            applied["receipt"] = receipts[row["id"]]
            out.append(applied)
        return out
    return [row for _, row in staged_rows]


def propose_write(
    db: Any,
    org_id: str,
    agent_id: str,
    collection: str,
    op: str,
    entry_key: str,
    body: str = "",
    *,
    staged_by: str = "",
) -> dict:
    """One proposed write; the single-op form of propose_batch."""
    rows = propose_batch(
        db,
        org_id,
        agent_id,
        collection,
        [{"op": op, "entry_key": entry_key, "body": body}],
        staged_by=staged_by,
    )
    return rows[0]


# -- approve / reject ------------------------------------------------


def _record_audit(
    db: Any,
    org_id: str,
    agent_id: str,
    *,
    event_type: str,
    staged: dict,
    receipt_id: str | None,
    collection: str,
    entry_key: str,
    version: int | None,
    decided_by: str,
) -> dict:
    if event_type not in AUDIT_EVENTS:
        raise MemoryError(f"unknown audit event {event_type!r}")
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_id,
        "workspace_id": workspace_id_for(org_id, agent_id),
        "event_type": event_type,
        "staged_write_id": staged["id"],
        "receipt_id": receipt_id,
        "collection": collection,
        "entry_key": entry_key,
        "detail": {"version": version, "decided_by": decided_by},
        "created_at": _now_iso(),
    }
    return db.table("agt_memory_audit_events").insert(row).execute().data[0]


def _mark_staged(
    db: Any, org_id: str, agent_id: str, staged: dict, payload: dict
) -> None:
    payload = {**payload, "updated_at": _now_iso()}
    (
        db.table("agt_staged_writes")
        .update(payload)
        .eq("id", staged["id"])
        .eq("org_id", org_id)
        .eq("agent_identity_id", agent_id)
        .eq("workspace_id", workspace_id_for(org_id, agent_id))
        .execute()
    )


def _pending_memory_write(db: Any, org_id: str, agent_id: str, staged_id: str) -> dict:
    _require_scope(org_id, agent_id)
    staged = _one(db, "agt_staged_writes", org_id, agent_id, staged_id)
    if staged is None:
        raise MemoryError(f"staged write {staged_id!r} not found in scope")
    if staged.get("kind") != "memory":
        raise MemoryError(f"staged write {staged_id!r} is not a memory write")
    if staged.get("status") != "pending":
        raise MemoryError(
            f"staged write {staged_id!r} is {staged.get('status')!r}, not pending"
        )
    return staged


def approve_write(
    db: Any, org_id: str, agent_id: str, staged_id: str, *, decided_by: str
) -> dict:
    """Apply one staged memory write and return its receipt.

    Refuses when the live entry no longer matches the pin
    (the staged row is marked refused_stale and the refusal
    is audited), when the re-run screening blocks the body,
    or when applying would break the collection bound against
    live state. On success the staged row is marked applied
    with the receipt id and an audit event is recorded.
    """
    ws = _require_scope(org_id, agent_id)
    staged = _pending_memory_write(db, org_id, agent_id, staged_id)
    collection, entry_key = _parse_target(staged)
    live = _get_entry(db, org_id, agent_id, collection, entry_key)
    current_pin = _pin_for(live, org_id, agent_id)
    if staged.get("pinned_target_hash") != current_pin:
        _mark_staged(
            db,
            org_id,
            agent_id,
            staged,
            {
                "status": "refused_stale",
                "decided_by": decided_by,
                "decided_at": _now_iso(),
            },
        )
        _record_audit(
            db,
            org_id,
            agent_id,
            event_type="memory_write_refused_stale",
            staged=staged,
            receipt_id=None,
            collection=collection,
            entry_key=entry_key,
            version=None,
            decided_by=decided_by,
        )
        raise StaleTargetError(
            f"entry {entry_key!r} changed since this write was staged; approval refused"
        )
    target_ref = staged.get("target_ref") or ""
    if target_ref != collection:
        raise MemoryError("staged target_ref does not match the target collection")
    now = _now_iso()
    if live is None:
        op = OP_ADD
    elif not (staged.get("proposed_body_masked") or ""):
        op = OP_REMOVE
    else:
        op = OP_UPDATE
    if live is None and not (staged.get("proposed_body_masked") or ""):
        raise MemoryError("staged write has no body and no live entry to remove")
    body = ""
    if op in (OP_ADD, OP_UPDATE):
        # Defense in depth (B1 fix round, Rex F5): screening and
        # masking run again at approval, so a staged body that
        # reached the table by any other path is never applied
        # raw.
        screened = screening.screen(staged.get("proposed_body_masked") or "")
        body = mask_text(screened)
        current = list_entries(db, org_id, agent_id, collection)
        state = {e["entry_key"]: e["body"] for e in current}
        state[entry_key] = body
        _check_bounds(
            collection, current, state, [] if live is not None else [entry_key]
        )
    version = (live["version"] if live else 0) + 1
    receipt_id = _receipt_id(
        org_id,
        agent_id,
        staged["id"],
        staged.get("proposed_body_hash") or "",
        version,
    )
    if op == OP_REMOVE:
        (
            db.table("agt_memory_entries")
            .delete()
            .eq("id", live["id"])
            .eq("org_id", org_id)
            .eq("agent_identity_id", agent_id)
            .eq("workspace_id", ws)
            .execute()
        )
    elif live is None:
        db.table("agt_memory_entries").insert(
            {
                "org_id": org_id,
                "agent_identity_id": agent_id,
                "workspace_id": ws,
                "collection": collection,
                "entry_key": entry_key,
                "body_masked": body,
                "body_hash": hash_body(body, org_id=org_id, agent_id=agent_id),
                "version": version,
                "staged_write_id": staged["id"],
                "receipt_id": receipt_id,
                "created_at": now,
                "updated_at": now,
            }
        ).execute()
    else:
        (
            db.table("agt_memory_entries")
            .update(
                {
                    "body_masked": body,
                    "body_hash": hash_body(body, org_id=org_id, agent_id=agent_id),
                    "version": version,
                    "staged_write_id": staged["id"],
                    "receipt_id": receipt_id,
                    "updated_at": now,
                }
            )
            .eq("id", live["id"])
            .eq("org_id", org_id)
            .eq("agent_identity_id", agent_id)
            .eq("workspace_id", ws)
            .execute()
        )
    _mark_staged(
        db,
        org_id,
        agent_id,
        staged,
        {
            "status": "applied",
            "decided_by": decided_by,
            "decided_at": now,
            "receipt_id": receipt_id,
        },
    )
    _record_audit(
        db,
        org_id,
        agent_id,
        event_type="memory_write_applied",
        staged=staged,
        receipt_id=receipt_id,
        collection=collection,
        entry_key=entry_key,
        version=version,
        decided_by=decided_by,
    )
    return {
        "receipt_id": receipt_id,
        "staged_write_id": staged["id"],
        "collection": collection,
        "entry_key": entry_key,
        "op": op,
        "version": version,
    }


def reject_write(
    db: Any, org_id: str, agent_id: str, staged_id: str, *, decided_by: str
) -> dict:
    """Reject one staged memory write. Nothing is applied and
    no receipt is issued; the decision is audited."""
    _require_scope(org_id, agent_id)
    staged = _pending_memory_write(db, org_id, agent_id, staged_id)
    collection, entry_key = _parse_target(staged)
    _mark_staged(
        db,
        org_id,
        agent_id,
        staged,
        {
            "status": "rejected",
            "decided_by": decided_by,
            "decided_at": _now_iso(),
        },
    )
    _record_audit(
        db,
        org_id,
        agent_id,
        event_type="memory_write_rejected",
        staged=staged,
        receipt_id=None,
        collection=collection,
        entry_key=entry_key,
        version=None,
        decided_by=decided_by,
    )
    return {"staged_write_id": staged["id"], "status": "rejected"}


# -- audit and claims --------------------------------------------------


def list_audit_events(
    db: Any, org_id: str, agent_id: str, *, limit: int = 100
) -> list[dict]:
    """The scoped audit trail, newest first."""
    _require_scope(org_id, agent_id)
    return (
        _scope(db, "agt_memory_audit_events", org_id, agent_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def claim_saved_memory(
    db: Any, org_id: str, agent_id: str, *, receipt_id: str | None
) -> dict:
    """The runtime-facing memory claim path.

    "I saved it" counts only with a receipt: the receipt id
    must exist in this scope's audit trail on an applied
    staged write, and it must recompute from the staged write
    and the audited version under the server hash key. A
    missing, unknown, transplanted, or forged receipt fails.
    """
    _require_scope(org_id, agent_id)
    if not receipt_id:
        raise MemoryClaimError("a memory claim requires a receipt id")
    events = (
        _scope(db, "agt_memory_audit_events", org_id, agent_id)
        .eq("receipt_id", receipt_id)
        .eq("event_type", "memory_write_applied")
        .limit(1)
        .execute()
        .data
    )
    if not events:
        raise MemoryClaimError(
            f"no applied memory write carries receipt {receipt_id!r}"
        )
    event = events[0]
    staged = _one(db, "agt_staged_writes", org_id, agent_id, event["staged_write_id"])
    if staged is None or staged.get("receipt_id") != receipt_id:
        raise MemoryClaimError("receipt does not match its staged write")
    if staged.get("status") != "applied":
        raise MemoryClaimError("the write behind this receipt was never applied")
    version = (event.get("detail") or {}).get("version")
    expected = _receipt_id(
        org_id,
        agent_id,
        staged["id"],
        staged.get("proposed_body_hash") or "",
        version,
    )
    if expected != receipt_id:
        raise MemoryClaimError("receipt does not verify against the audit trail")
    return {
        "verified": True,
        "receipt_id": receipt_id,
        "staged_write_id": staged["id"],
        "collection": event.get("collection"),
        "entry_key": event.get("entry_key"),
        "version": version,
    }


# -- snapshots -----------------------------------------------------------

_SNAPSHOTS: dict[tuple[str, str, str], dict] = {}


def assemble_snapshot(db: Any, org_id: str, agent_id: str, session_id: str) -> dict:
    """Assemble (once) and return the session's frozen snapshot.

    Reads the current approved entries a single time and keeps
    frozen copies keyed by (org, agent, session): later calls
    in the same session return the identical snapshot, so a
    write approved mid-session stays invisible until the next
    session assembles its own.
    """
    _require_scope(org_id, agent_id)
    if not session_id:
        raise MemoryError("session_id is required to assemble a snapshot")
    key = (org_id, agent_id, session_id)
    if key in _SNAPSHOTS:
        return _SNAPSHOTS[key]
    collections = {
        collection: [dict(e) for e in list_entries(db, org_id, agent_id, collection)]
        for collection in COLLECTIONS
    }
    canonical = repr(
        {
            c: [(e["entry_key"], e["body"], e["version"]) for e in entries]
            for c, entries in collections.items()
        }
    )
    snapshot = {
        "org_id": org_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "taken_at": _now_iso(),
        "collections": collections,
        "content_hash": hash_body(canonical, org_id=org_id, agent_id=agent_id),
    }
    _SNAPSHOTS[key] = snapshot
    return snapshot


def release_snapshot(org_id: str, agent_id: str, session_id: str) -> None:
    """Drop a session's frozen snapshot (session end)."""
    _SNAPSHOTS.pop((org_id, agent_id, session_id), None)


# -- gate setting ----------------------------------------------------------


def get_gate_mode(db: Any, org_id: str, agent_id: str) -> str:
    """The per-Tori approval gate mode. Default is gated: no
    settings row means the gate is on."""
    _require_scope(org_id, agent_id)
    rows = (
        _scope(db, "agt_memory_gate_settings", org_id, agent_id).limit(1).execute().data
    )
    if not rows:
        return GATE_GATED
    return rows[0].get("gate_mode", GATE_GATED)


def _record_gate_audit(
    db: Any,
    org_id: str,
    agent_id: str,
    *,
    old_mode: str,
    new_mode: str,
    actor_role: str,
    actor_id: str,
) -> dict:
    """Append the gate-change event to the audit trail. Gate
    events carry no staged write, collection, entry, or
    receipt; the transition itself (old mode, new mode,
    actor) is the detail."""
    if EVENT_GATE_MODE_CHANGED not in AUDIT_EVENTS:
        raise MemoryError(f"unknown audit event {EVENT_GATE_MODE_CHANGED!r}")
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_id,
        "workspace_id": workspace_id_for(org_id, agent_id),
        "event_type": EVENT_GATE_MODE_CHANGED,
        "staged_write_id": None,
        "receipt_id": None,
        "collection": None,
        "entry_key": None,
        "detail": {
            "old_mode": old_mode,
            "new_mode": new_mode,
            "actor": actor_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
        },
        "created_at": _now_iso(),
    }
    return db.table("agt_memory_audit_events").insert(row).execute().data[0]


def set_gate_mode(
    db: Any,
    org_id: str,
    agent_id: str,
    mode: str,
    *,
    actor_role: str,
    actor_id: str,
) -> dict:
    """Change the gate mode. Owner actors only, for every
    change: the agent has no path to this setting, so it can
    neither relax the gate itself nor remove a tightening.
    Every successful change is recorded in the audit trail;
    a refused attempt changes nothing and records nothing."""
    ws = _require_scope(org_id, agent_id)
    if mode not in GATE_MODES:
        raise MemoryError(f"unknown gate mode {mode!r}; want one of {GATE_MODES}")
    if actor_role != "owner":
        raise GatePermissionError("only the owner may change the memory gate setting")
    old_mode = get_gate_mode(db, org_id, agent_id)
    now = _now_iso()
    rows = (
        _scope(db, "agt_memory_gate_settings", org_id, agent_id).limit(1).execute().data
    )
    if rows:
        result = (
            db.table("agt_memory_gate_settings")
            .update({"gate_mode": mode, "updated_by": actor_id, "updated_at": now})
            .eq("id", rows[0]["id"])
            .eq("org_id", org_id)
            .eq("agent_identity_id", agent_id)
            .eq("workspace_id", ws)
            .execute()
            .data
        )[0]
    else:
        row = {
            "org_id": org_id,
            "agent_identity_id": agent_id,
            "workspace_id": ws,
            "gate_mode": mode,
            "updated_by": actor_id,
            "created_at": now,
            "updated_at": now,
        }
        result = db.table("agt_memory_gate_settings").insert(row).execute().data[0]
    _record_gate_audit(
        db,
        org_id,
        agent_id,
        old_mode=old_mode,
        new_mode=mode,
        actor_role=actor_role,
        actor_id=actor_id,
    )
    return result
