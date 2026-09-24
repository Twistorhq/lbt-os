"""
Leak-engine endpoints — the morning brief (question ladder) and detector registry.
All endpoints require a valid auth token (any plan). Read-only: the engine
never writes customer data.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..auth import AuthContext, get_auth
from ..database import get_db
from ..leak_engine import all_detectors, run_leak_scan
from ..limiter import limiter
from ..services import benchmarks as bm

router = APIRouter(prefix="/leaks", tags=["leaks"])


@router.get("/brief")
@limiter.limit("30/hour")
def leak_brief(request: Request, auth: Annotated[AuthContext, Depends(get_auth)]):
    """Morning brief: findings grouped by question-ladder rung."""
    return run_leak_scan(get_db(), auth.org_id)


@router.get("/detectors")
@limiter.limit("60/hour")
def leak_detectors(request: Request, auth: Annotated[AuthContext, Depends(get_auth)]):
    """Registry listing — which leak patterns are live for this org's vertical."""
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
    metric: str = Query(..., min_length=1, max_length=64),
):
    """Compare this org against its anonymized cohort (k-anonymity enforced)."""
    org = (
        get_db().table("organizations")
        .select("industry")
        .eq("id", auth.org_id)
        .limit(1)
        .execute()
        .data
        or [{}]
    )
    vertical = (org[0].get("industry") or "hvac").strip().lower()
    return bm.get_cohort_comparison(get_db(), auth.org_id, vertical, metric)
