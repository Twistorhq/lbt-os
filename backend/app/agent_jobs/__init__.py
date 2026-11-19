"""TW-952 (TW-948-B4) Tori persistent job service.

Scheduled work on the B1 persistence tables (agt_jobs,
agt_job_fires, agt_job_continuity): definitions with a
self-contained prompt, an ordered skill list, a per-job IANA
timezone, full five-field cron plus one-shot schedules, and a
catch-up window; a ticker that fires due jobs as agent_runtime
tasks; management operations create, list, pause, resume, edit,
trigger now, remove, and cancel run; and a per-job continuity
record every fire reads before running and updates after.

The execution context is constructed with no job-management
capability, so no-job-creates-job is a construction property.
Ships behind a per-org enable flag defaulting OFF
(settings.agent_jobs_enabled_orgs).

TW-953 (TW-948-B5) adds the safety layer on top: preflight
(definition quarantine plus credential, skill, delivery, and
ledger checks; a failure blocks the job with exactly one owner
alert and creates no task), the durable step journal and
exactly-once reconciliation, the stranded-fire reaper with
pause-for-review recovery, durable delivery handoff receipts,
and the durable tick heartbeat.
"""

from . import (
    cron,
    execution,
    heartbeat,
    models,
    preflight,
    reconciliation,
    recovery,
    service,
    ticker,
)

__all__ = [
    "cron",
    "execution",
    "heartbeat",
    "models",
    "preflight",
    "reconciliation",
    "recovery",
    "service",
    "ticker",
]
