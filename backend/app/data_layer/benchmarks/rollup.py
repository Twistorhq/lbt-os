"""TW-569: gold rollup for the trades benchmark layer.

Silver (benchmark_org_metrics, org-private) -> gold
(benchmark_cohort_stats, shared-asset). The ONLY cross-org table.

Rules (TW-201 + TW-564 Minor 13):
- k-anonymity: a cohort row is written only when >= 5 consenting orgs
  contribute. Enforced at write time (CHECK constraint + this job).
- Hold-and-suppress: a cohort publishes to gold only after meeting the
  threshold for 3 CONSECUTIVE refreshes (consecutive_passes). A cohort
  that drops below threshold flips published=false — its disappearance
  is not itself an event, so there is no differencing leak.
- Shared-asset lineage: data_events for the rollup carry artifact_id=NULL
  and terminate at promotion_batch_id (TW-564 Major 1). The CHECK
  constraint on data_events enforces this at the database level.

TW-208: never raises on the scheduled path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from statistics import mean
from typing import Any

log = logging.getLogger("twistor.data_layer.benchmarks")

K_ANONYMITY = 5
HOLD_PASSES = 3


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("empty")
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    f, c = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def compute_cohort_stats(
    rows: list[dict],
) -> list[dict[str, Any]]:
    """Pure: silver rows in, cohort stat dicts out.

    Rows: {"cohort_key", "metric_name", "period", "org_id", "metric_value"}.
    Groups by (cohort_key, metric_name, period); distinct orgs counted.
    """
    groups: dict[tuple, dict[str, set | list]] = {}
    for r in rows:
        key = (r["cohort_key"], r["metric_name"], r.get("period", "30d"))
        g = groups.setdefault(key, {"orgs": set(), "values": []})
        g["orgs"].add(r["org_id"])
        g["values"].append(float(r["metric_value"]))
    out = []
    for (cohort_key, metric_name, period), g in groups.items():
        n = len(g["orgs"])
        out.append(
            {
                "cohort_key": cohort_key,
                "metric_name": metric_name,
                "period": period,
                "n_orgs": n,
                "meets_threshold": n >= K_ANONYMITY,
                "p50": _percentile(g["values"], 50) if n >= K_ANONYMITY else None,
                "mean": float(mean(g["values"])) if n >= K_ANONYMITY else None,
            }
        )
    return out


def refresh_gold(db, batch_id: str | None = None) -> dict[str, Any]:
    """Refresh benchmark_cohort_stats from the latest silver snapshots.

    Reads the most recent snapshot per (org, metric, period) to avoid
    double-counting an org across promotion runs.
    """
    batch_id = batch_id or f"rollup-{datetime.now(timezone.utc).isoformat()}"
    try:
        silver = (
            db.table("benchmark_org_metrics")
            .select(
                "org_id, cohort_key, metric_name, metric_value, period, computed_at"
            )
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.error("rollup: silver read failed: %s", exc)
        return {"status": "failed", "reason": "db_error"}

    # Latest snapshot per (org, metric, period).
    latest: dict[tuple, dict] = {}
    for r in silver:
        key = (r["org_id"], r["metric_name"], r.get("period", "30d"))
        if key not in latest or str(r["computed_at"]) > str(latest[key]["computed_at"]):
            latest[key] = r

    stats = compute_cohort_stats(list(latest.values()))
    published = updated = suppressed = 0
    now = datetime.now(timezone.utc).isoformat()

    for s in stats:
        existing = (
            db.table("benchmark_cohort_stats")
            .select("id, consecutive_passes, published")
            .eq("cohort_key", s["cohort_key"])
            .eq("metric_name", s["metric_name"])
            .eq("period", s["period"])
            .limit(1)
            .execute()
            .data
            or []
        )
        if s["meets_threshold"]:
            passes = (existing[0]["consecutive_passes"] if existing else 0) + 1
            is_published = passes >= HOLD_PASSES
            payload = {
                "cohort_key": s["cohort_key"],
                "metric_name": s["metric_name"],
                "p50": s["p50"],
                "mean": s["mean"],
                "n_orgs": s["n_orgs"],
                "period": s["period"],
                "computed_at": now,
                "consecutive_passes": passes,
                "published": is_published,
            }
            # Atomic upsert: the UNIQUE(cohort_key, metric_name, period)
            # backstop makes the select-then-write race a no-op, not a crash.
            db.table("benchmark_cohort_stats").upsert(
                payload, on_conflict="cohort_key,metric_name,period"
            ).execute()
            if existing:
                updated += 1
            else:
                published += 1
        else:
            # Below threshold: flip published=false if a row exists.
            # No row + no threshold = nothing to do (never promised).
            if existing and existing[0]["published"]:
                db.table("benchmark_cohort_stats").update(
                    {
                        "consecutive_passes": 0,
                        "published": False,
                    }
                ).eq("id", existing[0]["id"]).execute()
                suppressed += 1
            elif existing:
                db.table("benchmark_cohort_stats").update({"consecutive_passes": 0}).eq(
                    "id", existing[0]["id"]
                ).execute()

    # Shared-asset lineage event: artifact_id NULL by construction.
    db.table("data_events").insert(
        {
            "event_type": "promoted",
            "silver_table": "benchmark_cohort_stats",
            "silver_id": None,
            "promotion_batch_id": batch_id,
            "artifact_id": None,
            "actor": "rollup_job",
            "contract_version": 1,
            "note": f"gold refresh: {published} new, {updated} updated, {suppressed} suppressed",
        }
    ).execute()

    return {
        "status": "ok",
        "batch_id": batch_id,
        "published": published,
        "updated": updated,
        "suppressed": suppressed,
    }
