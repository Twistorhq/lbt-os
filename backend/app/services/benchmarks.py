"""
Benchmark aggregation — TW-201. Powers "shops shaped like yours close 34%;
you're at 22%".

Privacy design (from day one):
- `organizations.benchmark_consent` is explicit opt-in. No consent, no
  aggregation. Ever.
- `benchmark_org_metrics` is per-org and private: raw material, never
  exposed cross-org.
- `benchmark_cohort_stats` is the ONLY cross-org table, and a cohort row is
  written only when >= 5 consenting orgs contribute (k-anonymity). Until
  then the API honestly reports `insufficient_cohort_data`.

v1 ships: schema + consent + per-org capture + cohort read. The scheduled
cross-org aggregation job lands post-launch.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from .. import self_healing as sh

K_ANONYMITY = 5

log = logging.getLogger("twistor.self_healing")

# Metrics the cohort API will compare. Unknown names are rejected at the edge.
METRIC_ALLOWLIST = frozenset({
    "leak_findings",
    "dollars_at_stake",
    "quote_close_rate",
    "quote_resurrection_value",
    "plan_churn_rate",
    "equipment_replacement_pipeline",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_org_metrics(
    db, org_id: str, vertical: str, metrics: dict[str, float], period: str = "30d"
) -> dict[str, Any]:
    """Store this org's private leak-metric snapshot. Requires consent.

    TW-208: never raises. A transient DB failure retries with backoff; a
    persistent failure degrades to {"recorded": 0, "reason": "db_error"} and
    logs loudly. The brief endpoint calls this inline — a benchmark write
    must never 500 the morning brief.
    """
    try:
        org = sh.retry_with_backoff(
            lambda: (
                db.table("organizations")
                .select("benchmark_consent")
                .eq("id", org_id)
                .limit(1)
                .execute()
                .data
                or []
            ),
            attempts=2, base_delay=0.2,
        )
    except Exception as exc:  # noqa: BLE001 - degrade, don't raise
        log.error("self-healing: benchmark consent read failed after retries: %s", exc)
        return {"recorded": 0, "reason": "db_error"}
    if not org or not org[0].get("benchmark_consent"):
        return {"recorded": 0, "reason": "no_consent"}
    rows = [
        {
            "org_id": org_id,
            "vertical": vertical,
            "metric_name": name,
            "metric_value": float(value),
            "period": period,
            "computed_at": _now_iso(),
        }
        for name, value in metrics.items()
    ]
    if not rows:
        return {"recorded": 0}
    try:
        sh.retry_with_backoff(
            lambda: db.table("benchmark_org_metrics").insert(rows).execute(),
            attempts=3, base_delay=0.3,
        )
    except Exception as exc:  # noqa: BLE001 - degrade, don't raise
        log.error("self-healing: benchmark metric write failed after retries: %s", exc)
        return {"recorded": 0, "reason": "db_error"}
    return {"recorded": len(rows)}


def get_cohort_comparison(
    db, org_id: str, vertical: str, metric_name: str
) -> dict[str, Any]:
    """Compare this org against its anonymized cohort. Never leaks per-org data."""
    mine = (
        db.table("benchmark_org_metrics")
        .select("metric_value")
        .eq("org_id", org_id)
        .eq("metric_name", metric_name)
        .order("computed_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    cohort = (
        db.table("benchmark_cohort_stats")
        .select("p50, mean, n_orgs, period")
        .eq("cohort_key", f"{vertical}:smb")
        .eq("metric_name", metric_name)
        .order("period", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not cohort or (cohort[0].get("n_orgs") or 0) < K_ANONYMITY:
        return {
            "metric": metric_name,
            "status": "insufficient_cohort_data",
            "detail": (
                "Cohort benchmarks appear once at least "
                f"{K_ANONYMITY} consenting shops contribute. The engine gets "
                "smarter with every customer."
            ),
        }
    c = cohort[0]
    mine_value = float(mine[0]["metric_value"]) if mine else None
    return {
        "metric": metric_name,
        "status": "ok",
        "yours": mine_value,
        "cohort_median": float(c["p50"]),
        "cohort_mean": float(c["mean"]),
        "cohort_size": int(c["n_orgs"]),
        "period": c.get("period"),
    }
