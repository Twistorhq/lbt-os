"""TW-949 Tori persistence: scoped read and write service.

Every function takes (db, org_id, agent_id) and filters on both,
plus the row's workspace key, on every query. The db argument is
the Supabase-style client the neighboring backend modules use
(db.table(name).select/insert/update with eq/order/limit), so the
tests exercise this module against a stateful in-memory fake in
exactly the house pattern (see test_agentperms_service.py).

Discipline carried from durable_turns: a message append is a
single insert that is acknowledged by the return of this
function; callers write before answering, never after.

PII: append_message stores the raw body in body_bronze ONLY.
Every read path that serves retrieval returns message_projection
dicts, which carry the masked projection and the hash and never
the bronze column. The one exception is get_message_bronze,
named for the bronze layer on purpose, for owner audit paths.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .models import (
    DuplicateFireError,
    FireCreate,
    JobCreate,
    MessageAppend,
    PersistenceError,
    SessionCreate,
    StagedWriteCreate,
    hash_body,
    mask_text,
    workspace_id_for,
)

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope(db: Any, table: str, org_id: str, agent_id: str) -> Any:
    return (
        db.table(table)
        .select("*")
        .eq("org_id", org_id)
        .eq("agent_identity_id", agent_id)
    )


def _one(db: Any, table: str, org_id: str, agent_id: str, row_id: str) -> dict | None:
    rows = _scope(db, table, org_id, agent_id).eq("id", row_id).limit(1).execute().data
    return rows[0] if rows else None


def _require_scope(org_id: str, agent_id: str) -> str:
    if not org_id or not agent_id:
        raise PersistenceError("org_id and agent_id are required on every call")
    return workspace_id_for(org_id, agent_id)


# -- sessions ---------------------------------------------------


def create_session(
    db: Any,
    org_id: str,
    agent_id: str,
    stmt: SessionCreate,
    *,
    workspace_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Create a session, optionally as a child of a parent session.

    Lineage law: a parent must exist inside the same (org, agent)
    scope. A parent id from another org or agent is refused, so a
    chain can never cross a tenant or roster boundary.
    """
    ws = workspace_id or _require_scope(org_id, agent_id)
    if stmt.parent_session_id is not None:
        parent = _one(db, "agt_sessions", org_id, agent_id, stmt.parent_session_id)
        if parent is None:
            raise PersistenceError(
                f"parent session {stmt.parent_session_id!r} not found in scope"
            )
        if parent.get("workspace_id") != ws:
            raise PersistenceError("parent session is in a different workspace")
    now = _now_iso()
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_id,
        "workspace_id": ws,
        "parent_session_id": stmt.parent_session_id,
        "channel": stmt.channel,
        "status": stmt.status,
        "title": stmt.title,
        "metadata": metadata or {},
        "started_at": now,
        "ended_at": None,
        "created_at": now,
        "updated_at": now,
    }
    return db.table("agt_sessions").insert(row).execute().data[0]


def get_session(db: Any, org_id: str, agent_id: str, session_id: str) -> dict | None:
    _require_scope(org_id, agent_id)
    return _one(db, "agt_sessions", org_id, agent_id, session_id)


def list_sessions(
    db: Any,
    org_id: str,
    agent_id: str,
    *,
    channel: str | None = None,
    limit: int = 50,
) -> list[dict]:
    _require_scope(org_id, agent_id)
    query = _scope(db, "agt_sessions", org_id, agent_id)
    if channel is not None:
        query = query.eq("channel", channel)
    return query.order("started_at", desc=True).limit(limit).execute().data


def walk_lineage(db: Any, org_id: str, agent_id: str, session_id: str) -> list[dict]:
    """The full lineage chain for a session, root first.

    Follows parent_session_id links inside (org, agent) scope and
    returns ancestors in order, ending with the session itself.
    A cycle in the links is a data defect and fails loud.
    """
    _require_scope(org_id, agent_id)
    chain: list[dict] = []
    seen: set[str] = set()
    current = get_session(db, org_id, agent_id, session_id)
    if current is None:
        raise PersistenceError(f"session {session_id!r} not found in scope")
    while current is not None:
        cid = current["id"]
        if cid in seen:
            raise PersistenceError(f"lineage cycle detected at session {cid!r}")
        seen.add(cid)
        chain.append(current)
        parent_id = current.get("parent_session_id")
        if not parent_id:
            break
        current = get_session(db, org_id, agent_id, parent_id)
        if current is None:
            raise PersistenceError(f"lineage parent {parent_id!r} not found in scope")
    chain.reverse()
    return chain


# -- messages ---------------------------------------------------


def message_projection(row: dict) -> dict:
    """The masked retrieval projection of a message row.

    Carries no bronze column, ever. This is the only shape search
    and history reads return.
    """
    return {
        "id": row.get("id"),
        "session_id": row.get("session_id"),
        "org_id": row.get("org_id"),
        "agent_identity_id": row.get("agent_identity_id"),
        "workspace_id": row.get("workspace_id"),
        "role": row.get("role"),
        "seq": row.get("seq"),
        "body_masked": row.get("body_masked", ""),
        "body_hash": row.get("body_hash"),
        "created_at": row.get("created_at"),
    }


def append_message(
    db: Any,
    org_id: str,
    agent_id: str,
    session_id: str,
    stmt: MessageAppend,
    *,
    seq: int | None = None,
) -> dict:
    """Append one message to a scoped session and return its row.

    seq defaults to the next position in the session. An explicit
    seq that is already taken is refused at this layer; the
    UNIQUE (session_id, seq) constraint refuses it at the
    database layer for writers that bypass this service.
    """
    ws = _require_scope(org_id, agent_id)
    session = get_session(db, org_id, agent_id, session_id)
    if session is None:
        raise PersistenceError(f"session {session_id!r} not found in scope")
    existing = (
        _scope(db, "agt_session_messages", org_id, agent_id)
        .eq("session_id", session_id)
        .execute()
        .data
    )
    if seq is None:
        seq = max((r.get("seq", 0) for r in existing), default=0) + 1
    elif any(r.get("seq") == seq for r in existing):
        raise PersistenceError(f"seq {seq} already exists in session {session_id!r}")
    masked = mask_text(stmt.body)
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_id,
        "workspace_id": session.get("workspace_id", ws),
        "session_id": session_id,
        "role": stmt.role,
        "seq": seq,
        "body_bronze": stmt.body,
        "body_hash": hash_body(stmt.body),
        "body_masked": masked,
        "search_text": masked,
        "created_at": _now_iso(),
    }
    return db.table("agt_session_messages").insert(row).execute().data[0]


def list_messages(db: Any, org_id: str, agent_id: str, session_id: str) -> list[dict]:
    """Masked projections for a session, in seq order."""
    _require_scope(org_id, agent_id)
    if get_session(db, org_id, agent_id, session_id) is None:
        raise PersistenceError(f"session {session_id!r} not found in scope")
    rows = (
        _scope(db, "agt_session_messages", org_id, agent_id)
        .eq("session_id", session_id)
        .order("seq")
        .execute()
        .data
    )
    return [message_projection(r) for r in rows]


def get_message_bronze(
    db: Any, org_id: str, agent_id: str, message_id: str
) -> dict | None:
    """The full row including body_bronze. Bronze layer only:
    owner audit paths call this by name; retrieval never does."""
    _require_scope(org_id, agent_id)
    return _one(db, "agt_session_messages", org_id, agent_id, message_id)


def search_messages(
    db: Any, org_id: str, agent_id: str, query_text: str, *, limit: int = 20
) -> list[dict]:
    """Token-overlap search over the masked projection.

    The in-memory seam mirrors the Core docsearch design (chunks
    ranked by term overlap); production search runs against the
    GIN index on search_text in the migration. Returns masked
    projections only, scoped to (org, agent).
    """
    _require_scope(org_id, agent_id)
    terms = {t for t in query_text.lower().split() if t}
    if not terms:
        return []
    rows = _scope(db, "agt_session_messages", org_id, agent_id).execute().data
    scored: list[tuple[int, dict]] = []
    for row in rows:
        haystack = set((row.get("search_text") or "").lower().split())
        score = len(terms & haystack)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("seq", 0)))
    return [message_projection(r) for _, r in scored[:limit]]


# -- jobs and fires ----------------------------------------------


def create_job(
    db: Any,
    org_id: str,
    agent_id: str,
    stmt: JobCreate,
    *,
    created_by: str = "",
    workspace_id: str | None = None,
) -> dict:
    ws = workspace_id or _require_scope(org_id, agent_id)
    now = _now_iso()
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_id,
        "workspace_id": ws,
        "name": stmt.name,
        "prompt": stmt.prompt,
        "skill_keys": list(stmt.skill_keys),
        "cron_expr": stmt.cron_expr,
        "timezone": stmt.timezone,
        "status": stmt.status,
        "next_fire_at": None,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }
    return db.table("agt_jobs").insert(row).execute().data[0]


def get_job(db: Any, org_id: str, agent_id: str, job_id: str) -> dict | None:
    _require_scope(org_id, agent_id)
    return _one(db, "agt_jobs", org_id, agent_id, job_id)


def list_jobs(db: Any, org_id: str, agent_id: str) -> list[dict]:
    _require_scope(org_id, agent_id)
    return _scope(db, "agt_jobs", org_id, agent_id).order("created_at").execute().data


def record_fire(
    db: Any,
    org_id: str,
    agent_id: str,
    job_id: str,
    stmt: FireCreate,
) -> dict:
    """Record one durable fire of a job.

    Duplicate identity is refused at this layer before insert: a
    fire_key that already exists anywhere, or the same job firing
    again for the same scheduled_for, raises DuplicateFireError.
    The migration's UNIQUE constraints are the second layer for
    writers that bypass this service.
    """
    ws = _require_scope(org_id, agent_id)
    job = get_job(db, org_id, agent_id, job_id)
    if job is None:
        raise PersistenceError(f"job {job_id!r} not found in scope")
    all_fires = db.table("agt_job_fires").select("*").execute().data
    for existing in all_fires:
        if existing.get("fire_key") == stmt.fire_key:
            raise DuplicateFireError(f"fire_key {stmt.fire_key!r} already recorded")
        if (
            existing.get("job_id") == job_id
            and existing.get("scheduled_for") == stmt.scheduled_for
        ):
            raise DuplicateFireError(
                f"job {job_id!r} already fired for {stmt.scheduled_for!r}"
            )
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_id,
        "workspace_id": job.get("workspace_id", ws),
        "job_id": job_id,
        "fire_key": stmt.fire_key,
        "scheduled_for": stmt.scheduled_for,
        "status": "pending",
        "task_id": None,
        "summary": "",
        "error": None,
        "started_at": None,
        "finished_at": None,
        "created_at": _now_iso(),
    }
    return db.table("agt_job_fires").insert(row).execute().data[0]


def list_fires(db: Any, org_id: str, agent_id: str, job_id: str) -> list[dict]:
    _require_scope(org_id, agent_id)
    return (
        _scope(db, "agt_job_fires", org_id, agent_id)
        .eq("job_id", job_id)
        .order("scheduled_for", desc=True)
        .execute()
        .data
    )


# -- continuity ---------------------------------------------------


def update_continuity(
    db: Any,
    org_id: str,
    agent_id: str,
    job_id: str,
    *,
    last_fire_id: str | None,
    summary: str,
    surfaced_item_ids: list[str],
) -> dict:
    """Create or update the one continuity record for a job.

    Surfaced item ids merge with order preserved and duplicates
    dropped: dedupe against prior runs is data, not memory.
    """
    ws = _require_scope(org_id, agent_id)
    job = get_job(db, org_id, agent_id, job_id)
    if job is None:
        raise PersistenceError(f"job {job_id!r} not found in scope")
    existing_rows = (
        _scope(db, "agt_job_continuity", org_id, agent_id)
        .eq("job_id", job_id)
        .limit(1)
        .execute()
        .data
    )
    now = _now_iso()
    if existing_rows:
        current = existing_rows[0]
        merged = list(current.get("surfaced_item_ids") or [])
        for item in surfaced_item_ids:
            if item not in merged:
                merged.append(item)
        payload = {
            "last_fire_id": last_fire_id,
            "last_run_summary": summary,
            "surfaced_item_ids": merged,
            "run_count": int(current.get("run_count", 0)) + 1,
            "last_run_at": now,
            "updated_at": now,
        }
        updated = (
            db.table("agt_job_continuity")
            .update(payload)
            .eq("id", current["id"])
            .execute()
            .data
        )
        return updated[0]
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_id,
        "workspace_id": job.get("workspace_id", ws),
        "job_id": job_id,
        "last_fire_id": last_fire_id,
        "last_run_summary": summary,
        "surfaced_item_ids": list(dict.fromkeys(surfaced_item_ids)),
        "run_count": 1,
        "last_run_at": now,
        "created_at": now,
        "updated_at": now,
    }
    return db.table("agt_job_continuity").insert(row).execute().data[0]


def get_continuity(db: Any, org_id: str, agent_id: str, job_id: str) -> dict | None:
    _require_scope(org_id, agent_id)
    rows = (
        _scope(db, "agt_job_continuity", org_id, agent_id)
        .eq("job_id", job_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


# -- staged writes --------------------------------------------------


def stage_write(
    db: Any,
    org_id: str,
    agent_id: str,
    stmt: StagedWriteCreate,
    *,
    staged_by: str = "",
    workspace_id: str | None = None,
) -> dict:
    """Stage a proposed memory or skill write for the gate.

    The staged body is stored in masked projection shape only;
    this table has no bronze column by design.
    """
    ws = workspace_id or _require_scope(org_id, agent_id)
    now = _now_iso()
    row = {
        "org_id": org_id,
        "agent_identity_id": agent_id,
        "workspace_id": ws,
        "kind": stmt.kind,
        "target_key": stmt.target_key,
        "target_ref": stmt.target_ref,
        "pinned_target_hash": stmt.pinned_target_hash,
        "proposed_body_masked": mask_text(stmt.proposed_body),
        "proposed_body_hash": hash_body(stmt.proposed_body),
        "status": "pending",
        "staged_by": staged_by,
        "decided_by": None,
        "decided_at": None,
        "receipt_id": None,
        "created_at": now,
        "updated_at": now,
    }
    return db.table("agt_staged_writes").insert(row).execute().data[0]


def list_staged_writes(
    db: Any,
    org_id: str,
    agent_id: str,
    *,
    status: str | None = None,
) -> list[dict]:
    _require_scope(org_id, agent_id)
    query = _scope(db, "agt_staged_writes", org_id, agent_id)
    if status is not None:
        query = query.eq("status", status)
    return query.order("created_at", desc=True).execute().data
