"""TW-621 Twistor Flags — service: CRUD, audit trail, scheduled flips.

Every mutation is attributed: who flipped what, when. The audit table is
append-only — no updates, no deletes. Evaluations are intentionally NOT
logged per call (TW-630 wedge: unlimited evaluations on the flat
subscription; logging each one would recreate the meter's data exhaust).

TW-208: a failed audit insert never fails the flag mutation itself —
the flag write is the source of truth, the audit is the trail.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .models import AuditEntry, FlagDefinition

log = logging.getLogger(__name__)


class FlagNotFound(Exception):
    """No flag with this key in this org."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_definition(row: dict[str, Any]) -> FlagDefinition:
    return FlagDefinition(**row["definition"])


def _record_audit(
    db: Any,
    org_id: str,
    flag_key: str,
    action: str,
    actor: str,
    summary: str,
) -> None:
    entry = AuditEntry(
        flag_key=flag_key,
        action=action,
        actor=actor,
        summary=summary[:500],
        at=_now(),
    )
    try:
        db.table("flg_audit").insert(
            {"org_id": org_id, **entry.model_dump(mode="json")}
        ).execute()
    except Exception as exc:  # TW-208: audit failure never fails the mutation
        log.warning(
            "flags: audit insert failed for %s (%s unaffected): %s",
            flag_key,
            action,
            exc,
        )


def _fetch_row(db: Any, org_id: str, key: str) -> dict[str, Any] | None:
    res = (
        db.table("flg_flags")
        .select("*")
        .eq("org_id", org_id)
        .eq("key", key)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def create_flag(
    db: Any, org_id: str, definition: dict[str, Any] | FlagDefinition, actor: str
) -> dict[str, Any]:
    """Create a flag. Raises DefinitionError on an invalid definition."""
    flag = (
        definition
        if isinstance(definition, FlagDefinition)
        else FlagDefinition(**definition)
    )
    row = {
        "org_id": org_id,
        "key": flag.key,
        "definition": flag.model_dump(mode="json"),
        "version": 1,
    }
    saved = db.table("flg_flags").insert(row).execute().data[0]
    _record_audit(
        db,
        org_id,
        flag.key,
        "create",
        actor,
        f"created v1 ({len(flag.variations)} variations)",
    )
    return saved


def get_flag(db: Any, org_id: str, key: str) -> FlagDefinition | None:
    row = _fetch_row(db, org_id, key)
    return _row_to_definition(row) if row else None


def list_flags(db: Any, org_id: str) -> list[FlagDefinition]:
    res = db.table("flg_flags").select("*").eq("org_id", org_id).order("key").execute()
    return [_row_to_definition(r) for r in (res.data or [])]


def update_flag(
    db: Any, org_id: str, key: str, patch: dict[str, Any], actor: str
) -> dict[str, Any]:
    """Merge patch into the definition, re-validate, bump version."""
    row = _fetch_row(db, org_id, key)
    if row is None:
        raise FlagNotFound(f"no flag {key!r} in org")
    merged = {**row["definition"], **patch}
    flag = FlagDefinition(**merged)  # DefinitionError on invalid result
    changed = sorted(
        k for k in patch if k in row["definition"] and row["definition"][k] != patch[k]
    )
    new_version = int(row.get("version", 1)) + 1
    updated = {
        "definition": flag.model_dump(mode="json"),
        "version": new_version,
        "updated_at": _now().isoformat(),
    }
    saved = (
        db.table("flg_flags")
        .update(updated)
        .eq("org_id", org_id)
        .eq("key", key)
        .execute()
        .data[0]
    )
    _record_audit(
        db,
        org_id,
        key,
        "update",
        actor,
        f"v{new_version} changed: {', '.join(changed) or 'no visible diff'}",
    )
    return saved


def flip_flag(
    db: Any, org_id: str, key: str, enabled: bool, actor: str
) -> dict[str, Any]:
    """Quick enable/disable. Audited as 'flip', not a generic update."""
    row = _fetch_row(db, org_id, key)
    if row is None:
        raise FlagNotFound(f"no flag {key!r} in org")
    if bool(row["definition"].get("enabled")) == enabled:
        return row  # already in the desired state — no-op, no audit noise
    saved = update_flag(db, org_id, key, {"enabled": enabled}, actor)
    # update_flag audited as 'update'; add the flip-specific entry too.
    _record_audit(
        db,
        org_id,
        key,
        "flip",
        actor,
        f"enabled {row['definition'].get('enabled')} -> {enabled}",
    )
    return saved


def delete_flag(db: Any, org_id: str, key: str, actor: str) -> None:
    row = _fetch_row(db, org_id, key)
    if row is None:
        raise FlagNotFound(f"no flag {key!r} in org")
    db.table("flg_flags").delete().eq("org_id", org_id).eq("key", key).execute()
    _record_audit(
        db, org_id, key, "delete", actor, f"deleted v{row.get('version', '?')}"
    )


def apply_due_schedules(db: Any, org_id: str, now: datetime) -> int:
    """Apply scheduled flips whose time has come. Returns count applied."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    applied = 0
    for row in (
        db.table("flg_flags").select("*").eq("org_id", org_id).execute()
    ).data or []:
        definition = dict(row["definition"])
        remaining = []
        fire: dict[str, Any] | None = None
        for sched in definition.get("scheduled", []):
            raw_at = sched["at"]
            at = (
                raw_at
                if isinstance(raw_at, datetime)
                else datetime.fromisoformat(raw_at)
            )
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at <= now and fire is None:
                fire = sched  # earliest due change wins; rest stay queued
            else:
                remaining.append(sched)
        if fire is None:
            continue
        definition["scheduled"] = remaining
        definition["enabled"] = bool(fire["enabled"])
        flag = FlagDefinition(**definition)
        new_version = int(row.get("version", 1)) + 1
        db.table("flg_flags").update(
            {
                "definition": flag.model_dump(mode="json"),
                "version": new_version,
                "updated_at": _now().isoformat(),
            }
        ).eq("org_id", org_id).eq("key", row["key"]).execute()
        _record_audit(
            db,
            org_id,
            row["key"],
            "schedule_applied",
            "scheduler",
            f"v{new_version} scheduled flip -> enabled {fire['enabled']}",
        )
        applied += 1
    return applied


def list_audit(
    db: Any, org_id: str, flag_key: str, limit: int = 50
) -> list[dict[str, Any]]:
    res = (
        db.table("flg_audit")
        .select("*")
        .eq("org_id", org_id)
        .eq("flag_key", flag_key)
        .order("at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []
