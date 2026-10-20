"""PFAS lane HTTP API (TW-593 / TW-595). Prepared by Twistor Holdings LLC.

Mounted by Dre's integration pass under /data/v1/science/pfas/:

    GET  /datasets            list dataset versions
    GET  /datasets/{version} snapshot metadata + merkle root
    GET  /query               gold query (filters: compound, destruction_method)
    GET  /benchmarks          open benchmarks + leaderboard
    POST /ingest              signed lab ingestion (batch, per-record isolation)
    GET  /corrections         correction feed per dataset version

Tolling (TW-549): every served call attempts a toll emission via
app.services.toll_service.record_toll_event with idempotency_key
"sci:{request_id}". Unknown event types raise ValueError until Dre wires
TOLL_RATES in integration: that path logs and continues, never 500s.

Auth: API keys via the data_api_keys pattern are wired in Dre's integration
pass. This router accepts an optional X-API-Key header and threads it
through to toll context; it does not enforce it yet.
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from ....database import get_db
from ....services import toll_service
from .. import paywall
from ..auth import enforce_lab_binding, require_science_key
from . import models, schemas, seed

log = logging.getLogger(__name__)

router = APIRouter(prefix="/data/v1/science/pfas", tags=["science-pfas"])

_TOLL_ORG = "twistor-science"


def _request_id(x_request_id: str | None) -> str:
    return x_request_id or uuid.uuid4().hex


def _emit_toll(
    db,
    *,
    event_type: str,
    request_id: str,
    quantity: float = 1.0,
    source_id: str | None = None,
) -> None:
    """Attempt a toll emission. A ValueError (unknown event type until
    TOLL_RATES is wired, or missing idempotency key) logs and continues:
    metering must never break serving."""
    try:
        toll_service.record_toll_event(
            db,
            org_id=_TOLL_ORG,
            event_type=event_type,
            quantity=quantity,
            idempotency_key=f"sci:{request_id}",
            source_type=f"science:{models.LANE}",
            source_id=source_id,
        )
    except ValueError as exc:
        log.warning(
            "toll emission skipped",
            extra={"event_type": event_type, "request_id": request_id, "error": str(exc)},
        )
    except Exception as exc:  # TW-208: metering failure never breaks serving
        log.warning(
            "toll emission failed",
            extra={"event_type": event_type, "request_id": request_id, "error": str(exc)},
        )


def _latest_version(db) -> dict[str, Any]:
    rows = (
        db.table(models.VERSIONS_TABLE)
        .select("*")
        .eq("lane", models.LANE)
        .order("released_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise HTTPException(status_code=404, detail="no PFAS dataset versions released yet")
    return rows[0]


@router.get("/datasets", response_model=schemas.DatasetListOut)
def list_datasets(
    response: Response = None,
    db=Depends(get_db),
    x_request_id: Annotated[str | None, Header()] = None,
):
    """List released PFAS dataset versions, newest first."""
    rows = (
        db.table(models.VERSIONS_TABLE)
        .select("*")
        .eq("lane", models.LANE)
        .order("released_at", desc=True)
        .execute()
        .data
        or []
    )
    request_id = _request_id(x_request_id)
    result = schemas.DatasetListOut(
        datasets=[schemas.DatasetVersionOut(**r) for r in rows]
    )
    return paywall.finalize(result, db, _TOLL_ORG, request_id, response)


@router.get("/datasets/{version}", response_model=schemas.DatasetVersionOut)
def get_dataset_version(
    version: str,
    response: Response = None,
    db=Depends(get_db),
    x_request_id: Annotated[str | None, Header()] = None,
):
    """Snapshot metadata for one semver, including the merkle root."""
    row = (
        db.table(models.VERSIONS_TABLE)
        .select("*")
        .eq("lane", models.LANE)
        .eq("semver", version)
        .maybe_single()
        .execute()
        .data
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"dataset version {version} not found")
    request_id = _request_id(x_request_id)
    result = schemas.DatasetVersionOut(**row)
    return paywall.finalize(result, db, _TOLL_ORG, request_id, response)


@router.get("/query", response_model=schemas.QueryResponse)
def query_gold(
    response: Response = None,
    compound: Annotated[str | None, Query()] = None,
    destruction_method: Annotated[str | None, Query()] = None,
    min_destruction_pct: Annotated[float | None, Query(ge=0, le=100)] = None,
    synthetic: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 200,
    bulk: Annotated[bool, Query()] = False,
    db=Depends(get_db),
    x_request_id: Annotated[str | None, Header()] = None,
):
    """Query the gold snapshot. Filters: compound, destruction_method,
    min_destruction_pct, synthetic. Set bulk=true for full export (tolls
    per 1k rows under data.science.bulk)."""
    request_id = _request_id(x_request_id)
    version = _latest_version(db)

    q = (
        db.table(models.GOLD_TABLE)
        .select("*")
        .eq("dataset_version_id", version["id"])
    )
    if compound:
        q = q.eq("compound", compound)
    if destruction_method:
        q = q.eq("destruction_method", destruction_method)
    if min_destruction_pct is not None:
        q = q.gte("destruction_pct", min_destruction_pct)
    if synthetic is not None:
        q = q.eq("synthetic", synthetic)
    rows = (q.limit(limit + 1).execute().data or [])
    truncated = len(rows) > limit
    rows = rows[:limit]

    _emit_toll(
        db,
        event_type="data.science.bulk" if bulk else "data.science.query",
        request_id=request_id,
        quantity=(len(rows) / 1000.0) if bulk else 1.0,
        source_id=version["id"],
    )

    reproduced = sum(
        (r.get("attestation_summary") or {}).get("reproduced", 0) for r in rows
    )
    result = schemas.QueryResponse(
        as_of=f"{version['dataset_name']} {version['semver']}",
        dataset_name=version["dataset_name"],
        semver=version["semver"],
        provenance=schemas.ProvenanceOut(
            lab_count=len({r.get("measurement_id") for r in rows}),
            attestation=schemas.AttestationSummary(reproduced=reproduced),
            synthetic_rows=sum(1 for r in rows if r.get("synthetic")),
            total_rows=len(rows),
        ),
        rows=[schemas.GoldRowOut(**r) for r in rows],
        row_count=len(rows),
        truncated=truncated,
    )
    return paywall.finalize(result, db, _TOLL_ORG, request_id, response)


@router.get("/benchmarks", response_model=schemas.BenchmarkListOut)
def list_benchmarks(
    response: Response = None,
    db=Depends(get_db),
    x_request_id: Annotated[str | None, Header()] = None,
):
    """Open benchmarks for the lane. V1 ships the method-comparison concept;
    rows from the shared sci_benchmarks table attach once seeded."""
    benchmarks: list[schemas.BenchmarkOut] = []
    try:
        rows = (
            db.table(models.BENCHMARKS_TABLE)
            .select("*")
            .eq("lane", models.LANE)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        log.warning("benchmark table read failed", extra={"error": str(exc)})
        rows = []
    for r in rows:
        benchmarks.append(
            schemas.BenchmarkOut(
                name=r["name"],
                metric=r["metric"],
                status=r["status"],
                dataset_version=r.get("dataset_version_id"),
                description=r.get("description", ""),
                leaderboard=[],
            )
        )
    if not any(b.name == seed.PFAS_METHOD_BENCHMARK["name"] for b in benchmarks):
        benchmarks.append(
            schemas.BenchmarkOut(
                name=seed.PFAS_METHOD_BENCHMARK["name"],
                metric=seed.PFAS_METHOD_BENCHMARK["metric"],
                status="concept",
                description=seed.PFAS_METHOD_BENCHMARK["description"],
            )
        )
    result = schemas.BenchmarkListOut(benchmarks=benchmarks)
    return paywall.finalize(result, db, _TOLL_ORG, _request_id(x_request_id),
                            response)


@router.post("/ingest", response_model=schemas.IngestResponse, status_code=202)
def ingest_batch(
    payload: schemas.IngestRequest,
    response: Response = None,
    db=Depends(get_db),
    x_request_id: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
):
    """Signed lab ingestion. Per-record isolation: invalid records land in
    dead letters, valid ones insert. Accepted rows await the next dataset
    version cut before they appear in gold."""
    request_id = _request_id(x_request_id)
    key = require_science_key(x_api_key, db, "pfas")
    enforce_lab_binding(key, [str(r.lab_id) for r in payload.records])
    records = [r.model_dump(mode="json") for r in payload.records]
    valid, dead_letters = models.split_valid_records(records)

    accepted = 0
    for row in valid:
        try:
            db.table(models.SILVER_TABLE).insert(row).execute()
            accepted += 1
        except Exception as exc:
            dead_letters.append({"index": -1, "errors": [str(exc)], "record": row})
            log.warning("pfas_ingest_insert_failed", extra={"error": str(exc)})

    _emit_toll(
        db,
        event_type="data.science.ingest",
        request_id=request_id,
        quantity=float(len(records)),
    )
    result = schemas.IngestResponse(
        accepted=accepted,
        dead_letters=len(dead_letters),
        dead_letter_details=dead_letters,
        dataset_version_pending=True,
    )
    return paywall.finalize(result, db, _TOLL_ORG, request_id, response)


@router.get("/corrections", response_model=schemas.CorrectionListOut)
def correction_feed(
    version: Annotated[str, Query()] = "1.0.0",
    response: Response = None,
    db=Depends(get_db),
    x_request_id: Annotated[str | None, Header()] = None,
):
    """Correction feed for one dataset version: errata that superseded rows
    carried by that version. History is never rewritten."""
    version_row = (
        db.table(models.VERSIONS_TABLE)
        .select("*")
        .eq("lane", models.LANE)
        .eq("semver", version)
        .maybe_single()
        .execute()
        .data
    )
    if not version_row:
        raise HTTPException(status_code=404, detail=f"dataset version {version} not found")
    rows = (
        db.table(models.CORRECTIONS_TABLE)
        .select("*")
        .eq("lane", models.LANE)
        .eq("new_dataset_version_id", version_row["id"])
        .order("corrected_at", desc=True)
        .execute()
        .data
        or []
    )
    result = schemas.CorrectionListOut(
        dataset_name=version_row["dataset_name"],
        semver=version_row["semver"],
        corrections=[schemas.CorrectionOut(**r) for r in rows],
    )
    return paywall.finalize(result, db, _TOLL_ORG, _request_id(x_request_id),
                            response)
