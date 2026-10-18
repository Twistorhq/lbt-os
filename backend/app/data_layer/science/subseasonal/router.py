# Prepared by Twistor Holdings LLC
"""Subseasonal lane router (TW-593 Track 2).

Mounted by Dre's integration pass under /data/v1/science/subseasonal/ —
this module is NOT self-mounted (main.py is shared; never touched).

Endpoints:
  GET  /datasets            — list dataset versions
  GET  /datasets/{version}  — snapshot metadata + merkle root
  GET  /query               — gold query (variable / region / lead_weeks)
  GET  /benchmarks          — open benchmarks + leaderboard
  POST /ingest              — signed lab ingestion (batch)

Tolling (TW-549 + dynamic derivative doctrine): every served call attempts
a toll event via app.services.toll_service.record_toll_event with
idempotency_key=f"sci:{request_id}". Unknown event types raise ValueError
in the toll service; we log and continue (tolling never breaks serving).
Dre wires TOLL_RATES in the integration pass.

Auth: V1 placeholder — org_id rides the X-Org-Id header until the
data_api_keys pattern lands in Dre's integration pass.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Header, Query, Request

from ..auth import enforce_lab_binding, require_science_key
from . import schemas

log = logging.getLogger(__name__)

router = APIRouter(prefix="/data/v1/science/subseasonal", tags=["science-subseasonal"])

LANE = "subseasonal"
DATASET_NAME = "twistor-s2s-forecasts"

# Toll event types (TW-549). Rates land in TOLL_RATES during integration.
TOLL_QUERY = "data.science.query"
TOLL_BULK = "data.science.bulk"
TOLL_INGEST = "data.science.ingest"
TOLL_BENCHMARK = "data.science.benchmark"


def _get_toll_recorder():
    """Lazy import so this module imports without supabase installed.

    Patchable in tests: monkeypatch this function to return a fake.
    """
    from app.services.toll_service import record_toll_event

    return record_toll_event


def _emit_toll(
    db: Any,
    org_id: str,
    event_type: str,
    request_id: str,
    quantity: float = 1.0,
) -> None:
    """Attempt one toll emission. ValueError (unknown event type, before
    rates are wired) is logged and swallowed — serving continues."""
    try:
        record = _get_toll_recorder()
    except Exception as exc:  # toll stack unavailable in this context
        log.warning("toll recorder unavailable (%s); continuing untolled", exc)
        return
    try:
        record(
            db,
            org_id=org_id,
            event_type=event_type,
            quantity=quantity,
            idempotency_key=f"sci:{request_id}",
            source_type="sci_subseasonal",
        )
    except ValueError as exc:
        log.warning("toll emission %s failed (%s); continuing", event_type, exc)


def _rows(resp: Any) -> list[dict[str, Any]]:
    return getattr(resp, "data", None) or []


# ------------------------------------------------------------------
# Service functions (db-injected; unit-testable without FastAPI)
# ------------------------------------------------------------------
def list_dataset_versions(db: Any, org_id: str, request_id: str) -> schemas.ScienceEnvelope:
    resp = (
        db.table("sci_dataset_versions")
        .select("*")
        .eq("lane", LANE)
        .order("released_at", desc=True)
        .execute()
    )
    rows = _rows(resp)
    _emit_toll(db, org_id, TOLL_QUERY, request_id)
    lab_count = len({r.get("lane") for r in rows})  # placeholder; lab join lands with core
    return schemas.envelope(
        data=[schemas.DatasetVersionOut(**r).model_dump() for r in rows],
        dataset_name=DATASET_NAME,
        semver=rows[0]["semver"] if rows else "0.0.0",
        lab_count=lab_count,
        synthetic=True,  # V1: synthetic seed only
    )


def get_dataset_version(db: Any, version: str, org_id: str, request_id: str) -> schemas.ScienceEnvelope:
    resp = (
        db.table("sci_dataset_versions")
        .select("*")
        .eq("lane", LANE)
        .eq("semver", version)
        .single()
        .execute()
    )
    row = getattr(resp, "data", None)
    if row is None:
        raise LookupError(f"no {LANE} dataset version {version}")
    _emit_toll(db, org_id, TOLL_QUERY, request_id)
    return schemas.envelope(
        data=schemas.DatasetVersionOut(**row).model_dump(),
        dataset_name=DATASET_NAME,
        semver=row["semver"],
        lab_count=0,
        synthetic=True,
    )


def query_gold(
    db: Any,
    org_id: str,
    request_id: str,
    variable: str | None = None,
    region: str | None = None,
    lead_weeks: int | None = None,
    limit: int = 100,
) -> schemas.ScienceEnvelope:
    if lead_weeks is not None and not 2 <= lead_weeks <= 6:
        raise ValueError("lead_weeks must be between 2 and 6")
    q = db.table("sci_subseasonal_gold").select("*")
    if variable:
        q = q.eq("variable", variable)
    if region:
        q = q.eq("region", region)
    if lead_weeks:
        q = q.eq("lead_weeks", lead_weeks)
    rows = _rows(q.limit(min(limit, 10000)).execute())
    _emit_toll(db, org_id, TOLL_QUERY, request_id)
    if limit > 1000:
        # Bulk pull: toll per 1k rows served (TW-549).
        _emit_toll(db, org_id, TOLL_BULK, f"{request_id}:bulk", quantity=len(rows) / 1000)
    out = []
    for r in rows:
        cond = r.get("conditions") or {}
        out.append(
            schemas.MeasurementOut(
                id=r["id"],
                model_source=r["model_source"],
                init_date=r["init_date"],
                lead_weeks=r["lead_weeks"],
                ensemble_size=r["ensemble_size"],
                variable=r["variable"],
                region=r["region"],
                crps=float(r["crps"]),
                skill_vs_climatology=r.get("skill_vs_climatology"),
                synthetic=bool(r.get("synthetic", False)),
                bias_correction=str(cond.get("bias_correction", "unknown")),
                base_model=str(cond.get("base_model", "unknown")),
            ).model_dump()
        )
    return schemas.envelope(
        data=out,
        dataset_name=DATASET_NAME,
        semver="1.0.0",
        lab_count=0,
        synthetic=all(r.get("synthetic", False) for r in rows) if rows else True,
    )


def list_benchmarks(db: Any, org_id: str, request_id: str) -> schemas.ScienceEnvelope:
    resp = (
        db.table("sci_benchmarks")
        .select("*, sci_benchmark_runs(submitter, score, submitted_at)")
        .eq("lane", LANE)
        .eq("status", "open")
        .execute()
    )
    rows = _rows(resp)
    _emit_toll(db, org_id, TOLL_BENCHMARK, request_id)
    out = []
    for r in rows:
        runs = sorted(r.get("sci_benchmark_runs") or [], key=lambda x: x.get("score", 0), reverse=True)
        out.append(
            schemas.BenchmarkOut(
                id=r["id"],
                name=r["name"],
                dataset_version_id=r["dataset_version_id"],
                metric=r["metric"],
                status=r["status"],
                leaderboard=runs,
            ).model_dump()
        )
    return schemas.envelope(
        data=out,
        dataset_name=DATASET_NAME,
        semver="1.0.0",
        lab_count=0,
        synthetic=True,
    )


def ingest_batch(
    db: Any, batch: schemas.IngestBatch, org_id: str, request_id: str
) -> schemas.IngestResult:
    """Batch ingest with per-record self-healing (TW-208): one bad record
    -> dead_letters, never kills the run. Unsigned payloads quarantine."""
    accepted = 0
    dead_letters: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for i, m in enumerate(batch.measurements):
        try:
            if not m.collector_signature:
                quarantined.append(
                    {"index": i, "reason": "missing collector_signature: unsigned payloads quarantine, never promote"}
                )
                continue
            if m.artifact_id is None:
                dead_letters.append(
                    {
                        "index": i,
                        "reason": "artifact_id required: bronze artifact must exist before silver insert (no floating silver)",
                    }
                )
                continue
            payload = m.model_dump(exclude={"collector_signature"})
            payload["conditions"] = m.conditions.model_dump()
            payload["artifact_id"] = str(m.artifact_id)
            payload["lab_id"] = str(m.lab_id)
            payload["calibration_id"] = str(m.calibration_id) if m.calibration_id else None
            db.table("sci_subseasonal_measurements").insert(payload).execute()
            accepted += 1
        except Exception as exc:  # per-record isolation: log, dead-letter, continue
            log.warning("ingest record %d dead-lettered: %s", i, exc)
            dead_letters.append({"index": i, "reason": str(exc)[:500]})
    _emit_toll(db, org_id, TOLL_INGEST, request_id, quantity=float(accepted))
    return schemas.IngestResult(
        accepted=accepted, dead_letters=dead_letters, quarantined=quarantined
    )


def _request_id(request: Request, x_request_id: str | None) -> str:
    return x_request_id or request.headers.get("x-request-id") or uuid.uuid4().hex


def _org_id(x_org_id: str | None) -> str:
    return x_org_id or "unscoped"


def _db() -> Any:
    from app.database import get_db

    return get_db()


# ------------------------------------------------------------------
# HTTP endpoints (thin wrappers over the service functions)
# ------------------------------------------------------------------
@router.get("/datasets")
def http_list_datasets(
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
):
    return list_dataset_versions(_db(), _org_id(x_org_id), _request_id(request, x_request_id))


@router.get("/datasets/{version}")
def http_get_dataset_version(
    version: str,
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
):
    return get_dataset_version(_db(), version, _org_id(x_org_id), _request_id(request, x_request_id))


@router.get("/query")
def http_query_gold(
    request: Request,
    variable: str | None = Query(default=None),
    region: str | None = Query(default=None),
    lead_weeks: int | None = Query(default=None, ge=2, le=6),
    limit: int = Query(default=100, ge=1, le=10000),
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
):
    return query_gold(
        _db(), _org_id(x_org_id), _request_id(request, x_request_id),
        variable=variable, region=region, lead_weeks=lead_weeks, limit=limit,
    )


@router.get("/benchmarks")
def http_list_benchmarks(
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
):
    return list_benchmarks(_db(), _org_id(x_org_id), _request_id(request, x_request_id))


@router.post("/ingest")
def http_ingest_batch(
    batch: schemas.IngestBatch,
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    db = _db()
    key = require_science_key(x_api_key, db, "subseasonal")
    enforce_lab_binding(key, [str(m.lab_id) for m in batch.measurements])
    return ingest_batch(db, batch, _org_id(x_org_id), _request_id(request, x_request_id))
