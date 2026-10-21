from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from .. import paywall
from ..auth import enforce_lab_binding, require_science_key
from . import schemas

"""TW-593 Track 2 · Electrocatalysis lane — FastAPI router.

Prepared by Twistor Holdings LLC.

Mounted (by Dre's integration pass) under /data/v1/science/electrocatalysis/.

§4 contract:
  GET  /datasets            — list dataset versions
  GET  /datasets/{version}  — snapshot metadata + merkle root
  GET  /query               — gold query (composition/facet/adsorbate/FE filters)
  GET  /benchmarks          — open benchmarks + leaderboard
  POST /ingest              — signed lab ingestion (batch)
  GET  /corrections         — correction feed per dataset

Every response carries as_of (dataset version + semver), provenance (lab
count, attestation summary), and synthetic flags. V1 seeds SYNTHETIC data
only.

Tolling (TW-549): every served call emits a toll event with
idempotency_key=f"sci:{request_id}". Rates are wired in config.TOLL_RATES;
metering failure is caught, logged, and never 500s a query.
"""
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data/v1/science/electrocatalysis", tags=["science-electrocatalysis"])

LANE = "electrocatalysis"
_GOLD_TABLE = "sci_electro_gold"
_SILVER_TABLE = "sci_electro_measurements"
_VERSIONS_TABLE = "sci_dataset_versions"


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def get_db():
    """Supabase client. Imported lazily so tests can override without a live
    connection; integration pass mounts this router with the real client."""
    from app.database import get_db as _get_db

    return _get_db()


# ---------------------------------------------------------------------------
# Tolling — never breaks the request (TW-208 self-healing)
# ---------------------------------------------------------------------------

def _default_toll_impl():
    """Resolve record_toll_event from the app's toll service. Lazy so the
    lane router never hard-fails at import time when the toll service (or its
    deps) isn't importable — tolling is best-effort, serving is not."""
    from app.services.toll_service import record_toll_event

    return record_toll_event


def emit_toll(
    db: Any,
    *,
    event_type: str,
    request_id: str,
    quantity: float = 1,
    amount_cents: int | None = None,
    org_id: str = "public-science",
) -> None:
    """Emit one metered science event. Unknown event types raise ValueError —
    caught here so a missing rate never 500s a query."""
    try:
        record_toll_event = _default_toll_impl()
    except ImportError as exc:
        logger.warning("science toll skipped (toll service unavailable): %s", exc)
        return
    try:
        record_toll_event(
            db,
            org_id=org_id,
            event_type=event_type,
            quantity=quantity,
            idempotency_key=f"sci:{request_id}",
            amount_cents=amount_cents,
        )
    except ValueError as exc:  # unknown toll event type — rates are wired; stay defensive
        logger.warning("science toll skipped (unknown event type): %s", exc)
    except Exception as exc:  # TW-208: metering failure never breaks serving
        logger.warning("science toll emit failed: %s", exc)


# ---------------------------------------------------------------------------
# Response envelope helpers
# ---------------------------------------------------------------------------

def _latest_version(db: Any) -> dict[str, Any] | None:
    res = (
        db.table(_VERSIONS_TABLE)
        .select("*")
        .eq("lane", LANE)
        .order("released_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", []) or []
    return rows[0] if rows else None


def _envelope(db: Any, rows: list[dict[str, Any]], version: dict[str, Any] | None) -> dict[str, Any]:
    """Build the §4 response contract around a page of gold rows."""
    attestation = {"reproduced": 0, "partial": 0, "failed": 0}
    silver_ids = [r.get("silver_id") for r in rows if r.get("silver_id")]
    lab_count = 0
    calibration_count = 0
    if silver_ids:
        # One IN query, never N+1.
        silver = (
            db.table(_SILVER_TABLE)
            .select("id,lab_id,calibration_id")
            .in_("id", silver_ids)
            .execute()
        )
        for s in getattr(silver, "data", []) or []:
            if s.get("lab_id"):
                lab_count += 1  # per-row here; a production rollup counts distinct
            if s.get("calibration_id"):
                calibration_count += 1
    for r in rows:
        summ = r.get("attestation_summary") or {}
        for k in attestation:
            attestation[k] += int(summ.get(k, 0) or 0)
    return {
        "as_of": {
            "dataset_version_id": (version or {}).get("id"),
            "semver": (version or {}).get("semver"),
            "dataset_name": (version or {}).get("dataset_name"),
            "released_at": (version or {}).get("released_at"),
        },
        "provenance": {
            "lab_count": lab_count,
            "attestation_summary": attestation,
            "calibration_count": calibration_count,
        },
        "synthetic": all(r.get("synthetic", True) for r in rows) if rows else True,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

@router.get("/datasets")
def list_datasets(
    response: Response = None,
    db: Any = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    paywall.enforce_rate_limit(db, "public-science")  # TW-608: 429 shutoff
    request_id = x_request_id or str(uuid.uuid4())
    res = (
        db.table(_VERSIONS_TABLE)
        .select("*")
        .eq("lane", LANE)
        .order("released_at", desc=True)
        .execute()
    )
    versions = getattr(res, "data", []) or []
    emit_toll(db, event_type="data.science.query", request_id=request_id)
    result = {
        "as_of": _latest_version(db),
        "provenance": {"lab_count": 0, "attestation_summary": {}, "calibration_count": 0},
        "synthetic": True,  # V1: synthetic seeds only
        "versions": [schemas.DatasetVersionOut(**v).model_dump(mode="json") for v in versions],
    }
    return paywall.finalize(result, db, None, request_id, response)


@router.get("/datasets/{version}")
def get_dataset(
    version: str,
    response: Response = None,
    db: Any = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    paywall.enforce_rate_limit(db, "public-science")  # TW-608: 429 shutoff
    request_id = x_request_id or str(uuid.uuid4())
    q = db.table(_VERSIONS_TABLE).select("*").eq("lane", LANE)
    # Accept a semver ("1.0.0") or a version UUID.
    try:
        uuid.UUID(version)
        q = q.eq("id", version)
    except ValueError:
        q = q.eq("semver", version)
    res = q.limit(1).execute()
    rows = getattr(res, "data", []) or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"dataset version {version!r} not found")
    emit_toll(db, event_type="data.science.query", request_id=request_id)
    v = rows[0]
    result = {
        "as_of": v,
        "provenance": {"lab_count": 0, "attestation_summary": {}, "calibration_count": 0},
        "synthetic": True,
        "merkle_root": v.get("content_root_hash"),
        "row_count": v.get("row_count"),
    }
    return paywall.finalize(result, db, None, request_id, response)


# ---------------------------------------------------------------------------
# Gold query
# ---------------------------------------------------------------------------

@router.get("/query")
def query_gold(
    response: Response = None,
    composition: str | None = Query(default=None),
    surface_facet: str | None = Query(default=None),
    adsorbate: str | None = Query(default=None),
    min_faradaic_efficiency: float | None = Query(default=None, ge=0, le=100),
    min_tof: float | None = Query(default=None, ge=0),
    include_synthetic: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=5000),
    db: Any = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    paywall.enforce_rate_limit(db, "public-science")  # TW-608: 429 shutoff
    request_id = x_request_id or str(uuid.uuid4())
    params = schemas.ElectroQueryParams(
        composition=composition,
        surface_facet=surface_facet,
        adsorbate=adsorbate,
        min_faradaic_efficiency=min_faradaic_efficiency,
        min_tof=min_tof,
        include_synthetic=include_synthetic,
        limit=limit,
    )

    q = db.table(_GOLD_TABLE).select("*")
    if params.composition:
        q = q.ilike("composition", f"%{params.composition}%")
    if params.surface_facet:
        q = q.eq("surface_facet", params.surface_facet)
    if params.adsorbate:
        q = q.eq("adsorbate", params.adsorbate)
    if params.min_faradaic_efficiency is not None:
        q = q.gte("faradaic_efficiency", params.min_faradaic_efficiency)
    if params.min_tof is not None:
        q = q.gte("experimental_tof", params.min_tof)
    if not params.include_synthetic:
        q = q.eq("synthetic", False)
    res = q.limit(params.limit).execute()
    rows = getattr(res, "data", []) or []

    version = _latest_version(db)
    emit_toll(db, event_type="data.science.query", request_id=request_id)
    payload = _envelope(db, rows, version)
    payload["rows"] = [
        schemas.GoldRowOut(**{k: r.get(k) for k in schemas.GoldRowOut.model_fields}).model_dump(
            mode="json"
        )
        for r in rows
    ]
    return paywall.finalize(payload, db, None, request_id, response)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

@router.get("/benchmarks")
def list_benchmarks(
    response: Response = None,
    status: str | None = Query(default=None, pattern="^(open|closed)$"),
    db: Any = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    paywall.enforce_rate_limit(db, "public-science")  # TW-608: 429 shutoff
    request_id = x_request_id or str(uuid.uuid4())
    q = db.table("sci_benchmarks").select("*").eq("lane", LANE)
    if status:
        q = q.eq("status", status)
    benches = getattr(q.execute(), "data", []) or []

    out: list[dict[str, Any]] = []
    for b in benches:
        runs = (
            db.table("sci_benchmark_runs")
            .select("*")
            .eq("benchmark_id", b["id"])
            .execute()
        )
        run_rows = getattr(runs, "data", []) or []
        # MAE-style metrics: lower wins. Documented per benchmark metric name.
        reverse = not any(k in (b.get("metric") or "") for k in ("mae", "loss", "error", "rmse"))
        run_rows.sort(key=lambda r: (r.get("score") is None, r.get("score")), reverse=reverse)
        out.append(
            {
                "benchmark": schemas.BenchmarkOut(**b).model_dump(mode="json"),
                "leaderboard": run_rows,
            }
        )

    emit_toll(db, event_type="data.science.query", request_id=request_id)
    result = {
        "as_of": _latest_version(db),
        "provenance": {"lab_count": 0, "attestation_summary": {}, "calibration_count": 0},
        "synthetic": True,
        "benchmarks": out,
    }
    return paywall.finalize(result, db, None, request_id, response)


# ---------------------------------------------------------------------------
# Ingest — signed lab batches
# ---------------------------------------------------------------------------

def _verify_lab(db: Any, lab_id: str) -> dict[str, Any]:
    res = db.table("sci_labs").select("id,verification_status").eq("id", lab_id).execute()
    labs = getattr(res, "data", []) or []
    if not labs:
        raise HTTPException(status_code=400, detail=f"unknown lab {lab_id}")
    if labs[0].get("verification_status") != "verified":
        raise HTTPException(status_code=403, detail=f"lab {lab_id} is not verified")
    return labs[0]


@router.post("/ingest", status_code=202)
def ingest_batch(
    batch: schemas.IngestBatchIn,
    response: Response = None,
    db: Any = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    paywall.enforce_rate_limit(db, "public-science")  # TW-608: 429 shutoff
    request_id = x_request_id or str(uuid.uuid4())
    key = require_science_key(x_api_key, db, "electrocatalysis")
    enforce_lab_binding(key, [str(r.lab_id) for r in batch.records])
    accepted = 0
    quarantined = 0
    dead_letters = 0
    receipt_parts: list[str] = []

    for rec in batch.records:
        try:
            _verify_lab(db, str(rec.lab_id))
            if not rec.collector_signature:
                # Unsigned lab payloads quarantine — never promoted (TW-208).
                quarantined += 1
                continue
            artifact_id = str(uuid.uuid4())
            db.table("sci_bronze_artifacts").insert(
                {
                    "id": artifact_id,
                    "lane": LANE,
                    "lab_id": str(rec.lab_id),
                    "storage_path": f"{batch.storage_path_prefix}/{artifact_id}.json",
                    "content_hash": rec.content_hash,
                    "collector_signature": rec.collector_signature,
                    "collected_at": rec.collected_at.isoformat(),
                    "schema_version": batch.schema_version,
                }
            ).execute()
            silver = {
                "artifact_id": artifact_id,
                "lab_id": str(rec.lab_id),
                "calibration_id": str(rec.calibration_id) if rec.calibration_id else None,
                "conditions": rec.conditions.model_dump(mode="json"),
                "measured_at": rec.measured_at.isoformat() if rec.measured_at else None,
                "composition": rec.composition,
                "surface_facet": rec.surface_facet,
                "adsorbate": rec.adsorbate,
                "dft_adsorption_ev": rec.dft_adsorption_ev,
                "experimental_tof": rec.experimental_tof,
                "faradaic_efficiency": rec.faradaic_efficiency,
                "synthetic": False,  # real lab records are never synthetic
            }
            db.table(_SILVER_TABLE).insert(silver).execute()
            accepted += 1
            receipt_parts.append(rec.content_hash)
        except HTTPException:
            raise
        except Exception as exc:  # per-record fault isolation — never kills the run
            logger.warning("ingest dead-letter: %s", exc)
            dead_letters += 1

    receipt_hash = hashlib.sha256("|".join(sorted(receipt_parts)).encode()).hexdigest()
    emit_toll(
        db,
        event_type="data.science.ingest",
        request_id=request_id,
        quantity=accepted,
    )
    result = schemas.IngestResultOut(
        accepted=accepted,
        quarantined=quarantined,
        dead_letters=dead_letters,
        batch_id=f"sci-batch-{request_id}",
        receipt_hash=receipt_hash,
    ).model_dump(mode="json")
    return paywall.finalize(result, db, None, request_id, response)


# ---------------------------------------------------------------------------
# Corrections feed
# ---------------------------------------------------------------------------

@router.get("/corrections")
def list_corrections(
    response: Response = None,
    dataset_version_id: str | None = Query(default=None),
    db: Any = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    paywall.enforce_rate_limit(db, "public-science")  # TW-608: 429 shutoff
    request_id = x_request_id or str(uuid.uuid4())
    q = (
        db.table("sci_corrections")
        .select("*")
        .eq("lane", LANE)
        .eq("corrects_silver_table", _SILVER_TABLE)
    )
    if dataset_version_id:
        q = q.eq("new_dataset_version_id", dataset_version_id)
    corrections = getattr(q.order("corrected_at", desc=True).execute(), "data", []) or []
    emit_toll(db, event_type="data.science.query", request_id=request_id)
    result = {
        "as_of": _latest_version(db),
        "provenance": {"lab_count": 0, "attestation_summary": {}, "calibration_count": 0},
        "synthetic": True,
        "corrections": [
            schemas.CorrectionOut(**c).model_dump(mode="json") for c in corrections
        ],
    }
    return paywall.finalize(result, db, None, request_id, response)
