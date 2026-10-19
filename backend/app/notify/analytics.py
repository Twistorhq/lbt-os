"""TW-620 Twistor Notify — delivery analytics.

This is the CORE product, not a beta tab (the TW-630 wedge): delivery
rates per workflow / tenant / channel that a PM can read, plus the
silent-failure debugger — one query from "did it send?" to the full
attempt trail.
"""

from __future__ import annotations

from supabase import Client


def delivery_rates(
    db: Client, org_id: str, *, workflow: str | None = None, days: int = 30
) -> dict:
    """Per-channel delivery rates for the org. Reads the ledger only."""
    query = (
        db.table("ntf_messages")
        .select("channel, status, cost_actual_cents, workflow")
        .eq("org_id", org_id)
    )
    if workflow:
        query = query.eq("workflow", workflow)
    rows = (query.execute().data) or []
    by_channel: dict[str, dict] = {}
    for row in rows:
        ch = row.get("channel", "unknown")
        entry = by_channel.setdefault(ch, {"sent": 0, "failed": 0, "spend_cents": 0})
        if row.get("status") == "sent":
            entry["sent"] += 1
        else:
            entry["failed"] += 1
        entry["spend_cents"] += int(row.get("cost_actual_cents") or 0)
    out = {}
    for ch, entry in by_channel.items():
        total = entry["sent"] + entry["failed"]
        out[ch] = {
            **entry,
            "delivery_rate": round(entry["sent"] / total, 4) if total else 0.0,
        }
    return {"org_id": org_id, "workflow": workflow, "channels": out}


def debug_message(db: Client, org_id: str, message_id: str) -> dict:
    """Silent-failure debugger: the message plus every attempt in order."""
    msg = (
        db.table("ntf_messages")
        .select("*")
        .eq("org_id", org_id)
        .eq("id", message_id)
        .maybe_single()
        .execute()
    )
    message = msg.data if msg and msg.data else None
    if not message:
        return {"found": False, "message_id": message_id}
    # Never leak the raw recipient through the debugger to non-admins;
    # the caller (router) enforces org scoping. Hash is the safe handle.
    attempts = (
        db.table("ntf_attempts")
        .select("provider, attempt_no, status, error, latency_ms, created_at")
        .eq("message_id", message_id)
        .order("attempt_no")
        .execute()
    )
    return {
        "found": True,
        "message": {
            "id": message.get("id"),
            "channel": message.get("channel"),
            "workflow": message.get("workflow"),
            "status": message.get("status"),
            "recipient_hash": message.get("recipient_hash"),
            "provider_used": message.get("provider_used"),
            "cost_preview_cents": message.get("cost_preview_cents"),
            "cost_actual_cents": message.get("cost_actual_cents"),
            "created_at": message.get("created_at"),
        },
        "attempts": (attempts.data if attempts and attempts.data else []),
    }
