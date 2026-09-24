"""
Leak-engine endpoints — the morning brief (question ladder) and detector registry.
All endpoints require a valid auth token (any plan). The brief is read-only
for customer data; it additionally records the org's own private benchmark
metrics (consent-gated server-side) so the cohort engine gets smarter.
"""
from typing import Annotated

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..leak_engine import all_detectors, run_leak_scan, vertical_for_industry
from ..limiter import enforce_user_limit, limiter
from ..services import analytics as usage
from ..services import benchmarks as bm

router = APIRouter(prefix="/leaks", tags=["leaks"])


def _org_vertical(db, org_id: str) -> str:
    """Resolve the org's leak-engine vertical (for analytics tagging)."""
    try:
        rows = (
            db.table("organizations")
            .select("industry")
            .eq("id", org_id)
            .limit(1)
            .execute()
            .data
            or [{}]
        )
        return vertical_for_industry(rows[0].get("industry"))
    except Exception:
        return "unknown"


def should_record_benchmarks(brief: dict) -> bool:
    """TW-204 (Rosa): never record benchmark metrics when a detector errored.

    An errored detector reports leak_findings: 0, and writing that row would
    quietly pollute future cohort aggregates.
    """
    return not brief.get("data_status", {}).get("errors")


@router.get("/brief")
@limiter.limit("30/hour")
def leak_brief(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit("30/hour"))],  # TW-078: per-verified-user
):
    """Morning brief: findings grouped by question-ladder rung."""
    db = get_db()
    started = time.perf_counter()
    brief = run_leak_scan(db, auth.org_id)
    duration_ms = int((time.perf_counter() - started) * 1000)
    # TW-209: usage metadata only — no finding contents, no dollar amounts.
    usage.emit_event(
        db,
        org_id=auth.org_id,
        vertical=brief.get("vertical") or "hvac",
        feature_key="leak_brief.viewed",
        context={"result": "ok", "duration_ms": duration_ms, "source": "api"},
    )
    # Feed the benchmark pipeline (no-op without the org's explicit consent).
    # Skipped when a detector errored so a partial brief never seeds cohort
    # aggregates with leak_findings: 0 (TW-204).
    if should_record_benchmarks(brief):
        bm.record_org_metrics(
            db,
            auth.org_id,
            brief.get("vertical") or "hvac",
            {
                "leak_findings": float(brief["totals"]["findings"]),
                "dollars_at_stake": float(brief["totals"]["dollars_at_stake"]),
            },
        )
    return brief


@router.get("/detectors")
@limiter.limit("60/hour")
def leak_detectors(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit("60/hour"))],  # TW-078: per-verified-user
):
    """Registry listing — which leak patterns are live for this org's vertical."""
    db = get_db()
    usage.emit_event(
        db,
        org_id=auth.org_id,
        vertical=_org_vertical(db, auth.org_id),
        feature_key="detectors.listed",
        context={"result": "ok", "source": "api", "count": len(all_detectors())},
    )
    return {
        "detectors": [
            {"name": d.name, "vertical": d.vertical, "inputs": d.requires}
            for d in all_detectors()
        ]
    }


@router.get("/benchmarks/compare")
@limiter.limit("60/hour")
def benchmark_compare(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth)],
    _user_limit: Annotated[None, Depends(enforce_user_limit("60/hour"))],  # TW-078: per-verified-user
    metric: str = Query(..., min_length=1, max_length=64),
):
    """Compare this org against its anonymized cohort (k-anonymity enforced)."""
    if metric not in bm.METRIC_ALLOWLIST:
        raise HTTPException(status_code=400, detail=f"unknown metric '{metric}'")
    db = get_db()
    vertical = _org_vertical(db, auth.org_id)
    result = bm.get_cohort_comparison(db, auth.org_id, vertical, metric)
    # TW-209: usage metadata only — metric NAME (allowlisted), never values.
    usage.emit_event(
        db,
        org_id=auth.org_id,
        vertical=vertical,
        feature_key="benchmark.compare",
        context={"result": "ok", "source": "api", "metric": metric},
    )
    return result
