"""TW-569: byproduct metric emitters for the trades benchmark layer.

Each emitter computes one org's 30d metric snapshot from the operational
tables. Consent-gated: organizations.benchmark_consent must be true, or the
org contributes nothing. Ever.

PII note (TW-552): emitters aggregate only. No phone numbers, emails,
names, or addresses are selected. The one identifier in play is org_id
(internal UUID, org-private silver), which the redaction spec HMACs before
any dead-letter write.

TW-208: emitters never raise on the scheduled path. A transient failure
retries with backoff; a persistent failure returns an empty snapshot and
logs loudly. One bad org never kills the promotion run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any

from ... import self_healing as sh

log = logging.getLogger("twistor.data_layer.benchmarks")

PERIOD_DAYS = 30

# New M1/M2 metric names (extend benchmarks.METRIC_ALLOWLIST at the edge).
METRIC_NAMES = frozenset(
    {
        "missed_call_recovery_rate",
        "time_to_first_response_min",
        "toll_spend_per_booked_job_cents",
        "estimate_close_rate",
        "avg_job_value_cents",
    }
)


def _window_start() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS)).isoformat()


def shop_size_bucket(active_tech_count: int) -> str:
    """Shop-size segment for the cohort key. Deterministic, no PII."""
    if active_tech_count <= 1:
        return "solo"
    if active_tech_count <= 10:
        return "smb"
    if active_tech_count <= 50:
        return "mid"
    return "large"


def cohort_key(trade: str, region: str, size_bucket: str) -> str:
    """Cohort key: trade:region:shopsize, e.g. hvac:co:smb.

    Region is the state code only — never city, never address.
    """
    return f"{(trade or 'unknown').lower()}:{(region or 'xx').lower()}:{size_bucket}"


def _median_or_none(values: list[float]) -> float | None:
    return float(median(values)) if values else None


def compute_snapshot(
    org_id: str,
    calls: list[dict],
    recoveries: list[dict],
    quotes: list[dict],
    jobs: list[dict],
    toll_cents: int,
) -> dict[str, Any]:
    """Pure computation: rows in, metric snapshot out. No DB, no network.

    - calls: call_logs rows (started_at, sms timing via recoveries)
    - recoveries: missed_call_recoveries rows (sms_sent_at, recovered_at, started via call)
    - quotes: quotes rows (status, sent_at)
    - jobs: jobs rows (status, total)
    - toll_cents: sum of toll_events.amount_cents for the period
    """
    metrics: dict[str, float] = {}

    # M2: recovery rate + time to first response.
    total_missed = len(recoveries)
    if total_missed:
        recovered = sum(1 for r in recoveries if r.get("recovered_at"))
        metrics["missed_call_recovery_rate"] = recovered / total_missed
        response_mins = []
        for r in recoveries:
            started = r.get("call_started_at")
            sms_at = r.get("sms_sent_at")
            if started and sms_at:
                try:
                    delta = (
                        datetime.fromisoformat(str(sms_at))
                        - datetime.fromisoformat(str(started))
                    ).total_seconds() / 60.0
                    if 0 <= delta < 60 * 24 * 7:  # sanity: within a week
                        response_mins.append(delta)
                except (ValueError, TypeError):
                    continue
        med = _median_or_none(response_mins)
        if med is not None:
            metrics["time_to_first_response_min"] = med

    # Quotes: estimate close rate.
    decided = [q for q in quotes if q.get("status") in ("won", "lost")]
    if decided:
        won = sum(1 for q in decided if q.get("status") == "won")
        metrics["estimate_close_rate"] = won / len(decided)

    # Jobs: average job value + toll spend per booked job.
    completed = [j for j in jobs if j.get("status") == "completed"]
    if completed:
        totals = [float(j.get("total") or 0) for j in completed]
        metrics["avg_job_value_cents"] = mean(totals) * 100.0
        metrics["toll_spend_per_booked_job_cents"] = float(toll_cents) / len(completed)

    out = []
    for name, value in metrics.items():
        out.append(
            {
                "metric_name": name,
                "metric_value": float(value),
                "sample_size": {
                    "missed_call_recovery_rate": total_missed,
                    "time_to_first_response_min": len(response_mins)
                    if total_missed
                    else 0,
                    "estimate_close_rate": len(decided),
                    "avg_job_value_cents": len(completed),
                    "toll_spend_per_booked_job_cents": len(completed),
                }.get(name, 0),
            }
        )
    return {"metrics": out, "metric_count": len(out)}


def emit_org_snapshot(db, org_id: str) -> dict[str, Any]:
    """Build one org's snapshot from the operational tables.

    Returns {"org_id", "cohort_key", "period", "computed_at", "metrics"}
    or {"recorded": 0, "reason": ...} on consent/DB failure. Never raises.
    """
    try:
        org_rows = sh.retry_with_backoff(
            lambda: (
                db.table("organizations")
                .select("id, industry, state, benchmark_consent")
                .eq("id", org_id)
                .limit(1)
                .execute()
                .data
                or []
            ),
            attempts=2,
            base_delay=0.2,
        )
    except Exception as exc:  # noqa: BLE001 - degrade, don't raise
        log.error("benchmark emitter: org read failed: %s", exc)
        return {"recorded": 0, "reason": "db_error"}
    if not org_rows or not org_rows[0].get("benchmark_consent"):
        return {"recorded": 0, "reason": "no_consent"}
    org = org_rows[0]

    since = _window_start()

    def _q(table, cols):
        try:
            return sh.retry_with_backoff(
                lambda: (
                    db.table(table)
                    .select(cols)
                    .eq("org_id", org_id)
                    .gte("created_at", since)
                    .execute()
                    .data
                    or []
                ),
                attempts=2,
                base_delay=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("benchmark emitter: %s read failed: %s", table, exc)
            return []

    recoveries = _q(
        "missed_call_recoveries",
        "sms_sent_at, recovered_at, call_logs!inner(started_at)",
    )
    # Flatten the joined call start into each recovery row.
    flat_recoveries = []
    for r in recoveries:
        call = r.get("call_logs") or {}
        flat_recoveries.append(
            {
                "sms_sent_at": r.get("sms_sent_at"),
                "recovered_at": r.get("recovered_at"),
                "call_started_at": call.get("started_at"),
            }
        )

    quotes = _q("quotes", "status")
    # Jobs: window on completed_at (a job completed in-window counts even
    # if it was created earlier).
    try:
        jobs = sh.retry_with_backoff(
            lambda: (
                db.table("jobs")
                .select("status, total")
                .eq("org_id", org_id)
                .eq("status", "completed")
                .gte("completed_at", since)
                .execute()
                .data
                or []
            ),
            attempts=2,
            base_delay=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("benchmark emitter: jobs read failed: %s", exc)
        jobs = []
    toll_rows = _q("toll_events", "amount_cents")
    toll_cents = sum(int(t.get("amount_cents") or 0) for t in toll_rows)

    tech_rows = _q("technicians", "id")
    size_bucket = shop_size_bucket(len(tech_rows))

    snap = compute_snapshot(org_id, [], flat_recoveries, quotes, jobs, toll_cents)
    return {
        "org_id": org_id,
        "cohort_key": cohort_key(org.get("industry"), org.get("state"), size_bucket),
        "period": "30d",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": snap["metrics"],
    }
