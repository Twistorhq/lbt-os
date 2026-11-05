"""TW-953 (TW-948-B5): the durable tick heartbeat (Rex F7 tail).

TW-952's fix round made a scan failure log before it raised.
That still left the caller's bare 500 as the only signal that
the scheduler had died: nothing IN the system recorded that
ticks had stopped. This module records every tick's outcome in
one durable singleton row (agt_job_tick_heartbeats): when the
last tick ran, whether it succeeded, and its headline counts.
A dead scheduler is then visible to any in-system reader (the
operator surface, a health check, the next operator who looks)
instead of only to whoever happened to call the tick endpoint.

check_scheduler_health turns staleness into an alert, exactly
once per stale episode: the alert marker is stored on the
heartbeat row keyed to the last_tick_at it fired for, so a
scheduler that stays dead does not re-alert every check, and a
scheduler that recovers and dies again alerts again.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..persistence.models import mask_text

log = logging.getLogger(__name__)

TABLE = "agt_job_tick_heartbeats"
SINGLETON_ID = "agent-jobs-ticker"

__all__ = [
    "SINGLETON_ID",
    "TABLE",
    "check_scheduler_health",
    "get_tick_health",
    "record_tick_heartbeat",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _get_row(db: Any) -> dict | None:
    rows = db.table(TABLE).select("*").eq("id", SINGLETON_ID).limit(1).execute().data
    return rows[0] if rows else None


def record_tick_heartbeat(
    db: Any,
    *,
    now: datetime,
    status: str,
    summary: dict | None = None,
    error: str | None = None,
) -> bool:
    """Upsert the singleton heartbeat row. Never raises: a
    heartbeat write failure is logged loudly (it is itself a
    scheduler-health signal) but must not kill the tick whose
    outcome it records. Returns True when the row landed."""
    summary = summary or {}
    payload = {
        "last_tick_at": now.isoformat(),
        "status": status,
        "fired_count": len(summary.get("fired") or []),
        "failed_count": len(summary.get("failed") or []),
        "blocked_count": len(summary.get("blocked") or []),
        "error": mask_text(error) if error else None,
        "updated_at": now.isoformat(),
    }
    try:
        if _get_row(db) is not None:
            db.table(TABLE).update(payload).eq("id", SINGLETON_ID).execute()
        else:
            db.table(TABLE).insert({"id": SINGLETON_ID, **payload}).execute()
    except Exception as exc:
        log.error("agent_jobs: tick heartbeat write failed: %s", exc)
        return False
    return True


def get_tick_health(
    db: Any,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=15),
) -> dict:
    """The scheduler's durable health: last tick, its outcome,
    and whether it is stale (no successful-or-failed tick
    recorded within stale_after). No row at all reads as
    status 'never' and stale: a scheduler that has never ticked
    is indistinguishable from a dead one, on purpose."""
    now = now or _now()
    row = _get_row(db)
    if row is None:
        return {
            "status": "never",
            "last_tick_at": None,
            "age_seconds": None,
            "stale": True,
            "fired_count": 0,
            "failed_count": 0,
            "blocked_count": 0,
            "error": None,
        }
    last = _parse_dt(row.get("last_tick_at"))
    age = (now - last).total_seconds() if last is not None else None
    return {
        "status": row.get("status"),
        "last_tick_at": row.get("last_tick_at"),
        "age_seconds": age,
        "stale": bool(age is None or age > stale_after.total_seconds()),
        "fired_count": row.get("fired_count", 0),
        "failed_count": row.get("failed_count", 0),
        "blocked_count": row.get("blocked_count", 0),
        "error": row.get("error"),
    }


def check_scheduler_health(
    db: Any,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=15),
    health_alerter: Callable[[dict], str] | None = None,
) -> dict:
    """Health plus the dead-scheduler alert. When the heartbeat
    is stale and a health_alerter is wired, it fires exactly
    once per stale episode (keyed to the last_tick_at on the
    row); the returned dict gains 'alerted' and 'alert_receipt'.
    """
    now = now or _now()
    health = get_tick_health(db, now=now, stale_after=stale_after)
    health["alerted"] = False
    health["alert_receipt"] = None
    if not health["stale"] or health_alerter is None:
        return health
    row = _get_row(db)
    marker = row.get("health_alerted_for") if row else None
    episode = health["last_tick_at"] or "never"
    if marker == episode:
        return health
    try:
        receipt = health_alerter(health)
    except Exception as exc:
        log.error("agent_jobs: scheduler health alert failed: %s", exc)
        return health
    health["alerted"] = True
    health["alert_receipt"] = receipt
    if row is not None:
        try:
            db.table(TABLE).update(
                {"health_alerted_for": episode, "updated_at": now.isoformat()}
            ).eq("id", SINGLETON_ID).execute()
        except Exception as exc:
            log.error("agent_jobs: health alert marker write failed: %s", exc)
    return health
