"""TW-650/TW-673 Twistor Social — scheduler tick.

The tick (TW-625 Flows pattern) drives the session clock (spec
Section 7.6). TW-673 wires the first real job: at a published
event's midpoint, every checked-in attendee without a welfare
prompt gets one recorded (rsn_welfare_checks, response NULL until
they answer). Prompt records are production state, not sends:
Notify workflows (rsn_arrival_prompt, rsn_encounter_confirm,
rsn_welfare_check, rsn_writeup_prompt) remain the delivery layer,
so the tick also reports how many prompts of each kind are due,
which is what a delivery wiring consumes next.

Still ahead in later phases: trust recomputation sweeps on a
schedule (recompute happens inline on loop events today),
collectible derivation, and the integrity lifecycle sweeps.
"""

from __future__ import annotations

import logging
from datetime import timedelta

log = logging.getLogger(__name__)


def run_tick(db, now=None) -> dict:
    from . import service

    now = service._resolve_now(now)
    welfare_created = 0
    due = {"arrival": 0, "encounter": 0, "welfare": 0, "writeup": 0}
    for event in service._fetch(db, "rsn_events"):
        if event.get("status") not in ("published", "completed"):
            continue
        starts = service._parse_ts(event.get("starts_at"))
        ends = service._parse_ts(event.get("ends_at"))
        if starts is None or ends is None:
            continue
        checkins = service._fetch(db, "rsn_checkins", event_id=event["id"])
        checked_in = {c["user_id"] for c in checkins}
        if event["status"] == "published":
            if starts - timedelta(minutes=30) <= now < starts:
                due["arrival"] += sum(
                    1
                    for r in service._fetch(db, "rsn_rsvps", event_id=event["id"])
                    if r.get("status") == "going" and r["user_id"] not in checked_in
                )
            if starts <= now <= ends and checked_in:
                due["encounter"] += len(checked_in)
            midpoint = starts + (ends - starts) / 2
            if starts <= now <= ends and now >= midpoint:
                prompted = {
                    r["user_id"]
                    for r in service._fetch(
                        db, "rsn_welfare_checks", event_id=event["id"]
                    )
                }
                for user_id in sorted(checked_in - prompted):
                    service.prompt_welfare_check(db, event["id"], user_id, now=now)
                    welfare_created += 1
                    due["welfare"] += 1
        if event["status"] == "completed" and now >= ends + timedelta(minutes=30):
            reviewed = {
                r["reviewer_id"]
                for r in service._fetch(db, "rsn_reviews", event_id=event["id"])
            }
            due["writeup"] += len(checked_in - reviewed)
    return {"welfare_prompts_created": welfare_created, "prompts_due": due}
