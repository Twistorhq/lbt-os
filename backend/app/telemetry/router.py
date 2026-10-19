"""TW-622 Twistor Telemetry — HTTP API.

- Capture: POST /telemetry/capture. Batch SDK intake. Scrubbed + hashed
  at ingestion (TW-552); NOT per-event tolled — the flat subscription
  covers capture up to the org's monthly tier (the TW-630 wedge).
- Queries: trends / funnels / retention / breakdown over scrubbed events.
- Admin: saved insights CRUD + PostHog import.
- Datafeed: cohort-level aggregates for the benchmark niches (k>=5 gate).

Org scoping: the caller's active org comes from the Twistor Identity JWT
(claims["org"]). Every endpoint 403s without an active org. PII is
scrubbed at ingestion (TW-552) — see scrub.py for the documented V1
limitation (key-name heuristic; orgs must declare PII-shaped custom
keys via extra_pii_keys).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from supabase import Client

from ..database import get_db
from ..limiter import limiter
from . import budgets, datafeed, migration, query, service
from .models import (
    BreakdownQuery,
    CaptureBatch,
    FunnelQuery,
    RetentionQuery,
    TrendQuery,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
_bearer_scheme = HTTPBearer(auto_error=False)


def _verified_claims(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    # Local import: identity is the auth authority; telemetry must not
    # import it at module load (keeps the package importable in tests).
    from ..identity import service as idn_service
    from ..identity import tokens as idn_tokens

    if not creds:
        raise HTTPException(status_code=401, detail="missing credentials")
    try:
        claims = idn_tokens.verify_access_token(
            idn_service.get_verification_keypairs(), creds.credentials
        )
    except idn_tokens.TokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    return claims


def _require_org(claims: dict = Depends(_verified_claims)) -> str:
    org_id = claims.get("org")
    if not org_id:
        raise HTTPException(status_code=403, detail="no active org on token")
    return org_id


def _require_actor(claims: dict = Depends(_verified_claims)) -> str:
    return str(claims.get("sub") or claims.get("email") or "unknown")


class InsightCreateIn(BaseModel):
    key: str
    kind: str
    definition: dict[str, Any]


class InsightUpdateIn(BaseModel):
    definition: dict[str, Any]


class PostHogImportIn(BaseModel):
    export: dict[str, Any]


class DatafeedExportIn(BaseModel):
    event: str = Field(min_length=1, max_length=128)
    group_props: list[str] = Field(min_length=1, max_length=5)
    min_cohort: int = Field(default=5, ge=5, le=1000)
    start: datetime
    end: datetime
    limit: int = Field(default=10000, ge=1, le=100000)


@router.post("/capture", status_code=202)
@limiter.limit("1200/minute")
def capture(
    request: Request,
    body: CaptureBatch,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """Batch event intake. 202: scrub -> budget decision -> batch insert.

    The response carries the budget state (warn/sampled/rate) — the
    in-band usage signal. No per-event toll: capture is the flat
    subscription's job (TW-630 wedge).
    """
    try:
        result = service.ingest_events(db, org_id, body.events)
    except service.TelemetryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result.get("warn_now"):
        # In-band warning now; the email channel activates with the
        # TW-608 warning path once organizations.billing_email lands.
        log.warning(
            "telemetry: org %s at %.0f%% of monthly event tier "
            "(received=%d stored=%d sampled=%s)",
            org_id,
            budgets.WARN_RATIO * 100,
            result["received"],
            result["stored"],
            result["sampled"],
        )
    return result


@router.post("/trends")
@limiter.limit("120/minute")
def trends(
    request: Request,
    body: TrendQuery,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    fetched = service.fetch_events(db, org_id, [body.event], body.start, body.end)
    return {
        "event": body.event,
        "bucket": body.bucket,
        "rows": query.trend(
            fetched["events"], body.event, body.start, body.end, body.bucket
        ),
        "truncated": fetched["truncated"],
    }


@router.post("/funnels")
@limiter.limit("120/minute")
def funnels(
    request: Request,
    body: FunnelQuery,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    step_events = [s.event for s in body.funnel.steps]
    window = migration.funnel_window(body.funnel)
    # Pad the fetch by the funnel window: a step-1 near `end` still gets
    # its full window of follow-up steps (no truncated windows at the edge).
    fetched = service.fetch_events(
        db, org_id, step_events, body.start, body.end + window
    )
    out = query.funnel(fetched["events"], step_events, window, body.start, body.end)
    out["truncated"] = fetched["truncated"]
    return out


@router.post("/retention")
@limiter.limit("120/minute")
def retention(
    request: Request,
    body: RetentionQuery,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    fetched = service.fetch_events(
        db,
        org_id,
        [body.cohort_event, body.return_event],
        body.start,
        body.end + _retention_tail(body.days),
    )
    out = query.retention(
        fetched["events"],
        body.cohort_event,
        body.return_event,
        body.days,
        body.start,
        body.end,
    )
    out["truncated"] = fetched["truncated"]
    return out


def _retention_tail(days: int) -> timedelta:
    return timedelta(days=days)


@router.post("/breakdown")
@limiter.limit("120/minute")
def breakdown(
    request: Request,
    body: BreakdownQuery,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    fetched = service.fetch_events(db, org_id, [body.event], body.start, body.end)
    return {
        "event": body.event,
        "property": body.property,
        "rows": query.breakdown(
            fetched["events"],
            body.event,
            body.property,
            body.start,
            body.end,
            body.limit,
        ),
        "truncated": fetched["truncated"],
    }


@router.post("/admin/insights", status_code=201)
@limiter.limit("60/minute")
def create_insight(
    request: Request,
    body: InsightCreateIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        return service.create_insight(
            db, org_id, body.key, body.kind, body.definition, actor
        )
    except service.InsightExists as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except service.TelemetryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/admin/insights")
@limiter.limit("120/minute")
def list_insights(
    request: Request,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return {"insights": service.list_insights(db, org_id)}


@router.get("/admin/insights/{key}")
@limiter.limit("120/minute")
def get_insight(
    request: Request,
    key: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    row = service.get_insight(db, org_id, key)
    if row is None:
        raise HTTPException(status_code=404, detail="insight not found")
    return row


@router.patch("/admin/insights/{key}")
@limiter.limit("60/minute")
def update_insight(
    request: Request,
    key: str,
    body: InsightUpdateIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        return service.update_insight(db, org_id, key, body.definition, actor)
    except service.InsightNotFound:
        raise HTTPException(status_code=404, detail="insight not found")
    except service.TelemetryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/admin/insights/{key}", status_code=204)
@limiter.limit("60/minute")
def delete_insight(
    request: Request,
    key: str,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        service.delete_insight(db, org_id, key)
    except service.InsightNotFound:
        raise HTTPException(status_code=404, detail="insight not found")
    return None


@router.post("/admin/import/posthog", status_code=201)
@limiter.limit("30/minute")
def import_posthog(
    request: Request,
    body: PostHogImportIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    """Import a PostHog export. Mapped events are ingested (scrubbed);
    mapped funnels are persisted as saved insights; unmapped sections are
    reported honestly, never silently dropped."""
    report = migration.migrate_with_report(body.export)
    ingested = (
        service.ingest_events(db, org_id, report.events)
        if report.events
        else {"received": 0, "stored": 0}
    )
    persisted_keys: list[str] = []
    for i, mapped in enumerate(report.funnels):
        key = _import_funnel_key(mapped.name, i)
        attempt = key
        suffix = 2
        while True:
            try:
                service.create_insight(
                    db,
                    org_id,
                    attempt,
                    "funnel",
                    {"funnel": mapped.definition.model_dump(mode="json")},
                    actor,
                )
                persisted_keys.append(attempt)
                break
            except service.InsightExists:
                attempt = f"{key}-{suffix}"
                suffix += 1
    return {
        "events_ingested": ingested["stored"],
        "funnels_mapped": len(report.funnels),
        "funnels_persisted": persisted_keys,
        "funnel_definitions": [
            m.definition.model_dump(mode="json") for m in report.funnels
        ],
        "unmapped": report.unmapped,
    }


def _import_funnel_key(name: str | None, index: int) -> str:
    """Slugify a PostHog funnel name into a valid insight key."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not slug:
        slug = f"posthog-funnel-{index + 1}"
    return slug[:64]


@router.post("/datafeed/export")
@limiter.limit("30/minute")
def datafeed_export(
    request: Request,
    body: DatafeedExportIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """Cohort-level aggregates for the benchmark niches. k-anonymity
    gated; no identifiers leave the building."""
    fetched = service.fetch_events(
        db, org_id, [body.event], body.start, body.end, body.limit
    )
    return {
        "event": body.event,
        "rows": datafeed.cohort_aggregates(
            fetched["events"], body.event, body.group_props, body.min_cohort
        ),
        "truncated": fetched["truncated"],
    }
