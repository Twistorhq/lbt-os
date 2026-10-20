"""TW-593 Track 2 · Turbulence lane — API router.

Prepared by Twistor Holdings LLC.

Mounted by the integration pass under /data/v1/science/turbulence/ (Dre).
Endpoints follow the §4 serving contract; every response carries as_of,
provenance, and synthetic flags. Every served call emits a toll event
(best-effort: tolls never break reads, TW-208).
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from ....auth import AuthContext, get_auth
from ....database import get_db
from ....services.toll_service import record_toll_event
from .. import paywall
from ..auth import enforce_lab_binding, require_science_key
from .schemas import (
    BenchmarkOut,
    DatasetVersionOut,
    IngestBatch,
    IngestResponse,
    ProvenanceSummary,
    QueryFilters,
    QueryResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["science-turbulence"])

LANE = "turbulence"
_MEAS_TABLE = "sci_turbulence_measurements"
_GOLD_TABLE = "sci_turbulence_gold"
_VERSIONS_TABLE = "sci_dataset_versions"
_BENCH_TABLE = "sci_benchmarks"
_BENCH_RUNS_TABLE = "sci_benchmark_runs"


# ---------------------------------------------------------------------------
# Tolling (TW-549): best-effort, never breaks the request (TW-208).
# ---------------------------------------------------------------------------

def _emit_toll(org_id: str, event_type: str, request_id: str,
               quantity: float = 1.0) -> None:
    try:
        record_toll_event(
            get_db(),
            org_id=org_id,
            event_type=event_type,
            quantity=quantity,
            idempotency_key=f"sci:{request_id}",
        )
    except ValueError as exc:  # unknown event type / missing key: log, continue
        log.warning("turbulence toll emit failed",
                    extra={"event": event_type, "error": str(exc)})
    except Exception as exc:  # TW-208: metering failure never breaks serving
        log.warning("turbulence toll emit failed",
                    extra={"event": event_type, "error": str(exc)})


def _request_id() -> str:
    return uuid.uuid4().hex


def _rows(db, table: str) -> list[dict[str, Any]]:
    res = db.table(table).select("*").execute()
    return list(res.data or [])


# ---------------------------------------------------------------------------
# GET /datasets — list dataset versions
# ---------------------------------------------------------------------------

@router.get("/datasets", response_model=list[DatasetVersionOut])
def list_datasets(auth: Annotated[AuthContext, Depends(get_auth)],
                 response: Response = None):
    request_id = _request_id()
    db = get_db()
    rows = [r for r in _rows(db, _VERSIONS_TABLE) if r.get("lane") == LANE]
    _emit_toll(auth.org_id, "data.science.query", request_id)
    result = [DatasetVersionOut(**{
        "id": r["id"], "dataset_name": r["dataset_name"], "semver": r["semver"],
        "row_count": r["row_count"], "content_root_hash": r["content_root_hash"],
        "benchmark_pin": r.get("benchmark_pin"), "doi": r.get("doi"),
        "supersedes_id": r.get("supersedes_id"),
        "released_at": r.get("released_at"), "release_notes": r.get("release_notes", ""),
    }) for r in rows]
    # JSON array: usage rides the X-Twistor-* headers (finalize handles lists).
    return paywall.finalize(result, db, auth.org_id, request_id, response)


# ---------------------------------------------------------------------------
# GET /datasets/{version} — snapshot metadata + merkle root
# ---------------------------------------------------------------------------

@router.get("/datasets/{version}", response_model=DatasetVersionOut)
def get_dataset(version: str, auth: Annotated[AuthContext, Depends(get_auth)],
                response: Response = None):
    request_id = _request_id()
    db = get_db()
    rows = [r for r in _rows(db, _VERSIONS_TABLE)
            if r.get("lane") == LANE and r.get("semver") == version]
    _emit_toll(auth.org_id, "data.science.query", request_id)
    if not rows:
        raise HTTPException(status_code=404,
                            detail=f"no {LANE} dataset version {version!r}")
    r = rows[0]
    result = DatasetVersionOut(**{
        "id": r["id"], "dataset_name": r["dataset_name"], "semver": r["semver"],
        "row_count": r["row_count"], "content_root_hash": r["content_root_hash"],
        "benchmark_pin": r.get("benchmark_pin"), "doi": r.get("doi"),
        "supersedes_id": r.get("supersedes_id"),
        "released_at": r.get("released_at"), "release_notes": r.get("release_notes", ""),
    })
    return paywall.finalize(result, db, auth.org_id, request_id, response)


# ---------------------------------------------------------------------------
# GET /query — filtered measurement query
# ---------------------------------------------------------------------------

@router.get("/query", response_model=QueryResponse)
def query_measurements(
    auth: Annotated[AuthContext, Depends(get_auth)],
    response: Response = None,
    case_name: str | None = Query(default=None),
    reynolds_min: float | None = Query(default=None, gt=0),
    reynolds_max: float | None = Query(default=None, gt=0),
    closure_model: str | None = Query(default=None),
    solver: str | None = Query(default=None),
    synthetic: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
):
    request_id = _request_id()
    filters = QueryFilters(case_name=case_name, reynolds_min=reynolds_min,
                           reynolds_max=reynolds_max, closure_model=closure_model,
                           solver=solver, synthetic=synthetic, limit=limit)
    db = get_db()
    rows = _rows(db, _MEAS_TABLE)
    if filters.case_name:
        rows = [r for r in rows if r.get("case_name") == filters.case_name]
    if filters.reynolds_min is not None:
        rows = [r for r in rows
                if r.get("reynolds") is not None and r["reynolds"] >= filters.reynolds_min]
    if filters.reynolds_max is not None:
        rows = [r for r in rows
                if r.get("reynolds") is not None and r["reynolds"] <= filters.reynolds_max]
    if filters.closure_model:
        rows = [r for r in rows if r.get("closure_model") == filters.closure_model]
    if filters.solver:
        rows = [r for r in rows if r.get("solver") == filters.solver]
    if filters.synthetic is not None:
        rows = [r for r in rows if bool(r.get("synthetic")) == filters.synthetic]
    rows = rows[:filters.limit]

    versions = [r for r in _rows(db, _VERSIONS_TABLE) if r.get("lane") == LANE]
    # Order by released_at, not semver: lexicographic max() puts 10.0.0 below 2.0.0.
    latest = max(versions, key=lambda r: r.get("released_at", "")) if versions else None

    lab_ids = {r.get("lab_id") for r in rows if r.get("lab_id")}
    _emit_toll(auth.org_id, "data.science.query", request_id)
    if len(rows) >= 1000:  # bulk export tier
        _emit_toll(auth.org_id, "data.science.bulk", request_id,
                   quantity=len(rows) / 1000.0)

    result = QueryResponse(
        as_of={"dataset_name": latest["dataset_name"] if latest else "unversioned",
               "semver": latest["semver"] if latest else "0.0.0"},
        provenance=ProvenanceSummary(
            lab_count=len(lab_ids),
            attestation_summary={},
            synthetic=all(bool(r.get("synthetic")) for r in rows) if rows else True,
        ),
        rows=rows,
        row_count=len(rows),
    )
    return paywall.finalize(result, db, auth.org_id, request_id, response)


# ---------------------------------------------------------------------------
# GET /benchmarks — open benchmarks + leaderboard (the Closure Challenge analog)
# ---------------------------------------------------------------------------

@router.get("/benchmarks", response_model=list[BenchmarkOut])
def list_benchmarks(auth: Annotated[AuthContext, Depends(get_auth)],
                    response: Response = None):
    request_id = _request_id()
    db = get_db()
    benches = [r for r in _rows(db, _BENCH_TABLE) if r.get("lane") == LANE]
    runs = _rows(db, _BENCH_RUNS_TABLE)
    out = []
    for b in benches:
        board = sorted(
            (r for r in runs if r.get("benchmark_id") == b["id"]),
            key=lambda r: r.get("score", 0), reverse=True,
        )
        out.append(BenchmarkOut(
            id=b["id"], name=b["name"], metric=b["metric"],
            status=b["status"],
            leaderboard=[{"submitter": r.get("submitter"),
                          "score": r.get("score"),
                          "submitted_at": r.get("submitted_at")}
                         for r in board],
        ))
    _emit_toll(auth.org_id, "data.science.benchmark", request_id)
    # JSON array: usage rides the X-Twistor-* headers.
    return paywall.finalize(out, db, auth.org_id, request_id, response)


# ---------------------------------------------------------------------------
# POST /ingest — signed lab ingestion (batch). Per-record self-healing:
# one bad record -> dead_letters, never kills the batch (TW-208).
# ---------------------------------------------------------------------------

@router.post("/ingest", response_model=IngestResponse)
def ingest_batch(batch: IngestBatch,
                 auth: Annotated[AuthContext, Depends(get_auth)],
                 response: Response = None,
                 x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    request_id = _request_id()
    db = get_db()
    key = require_science_key(x_api_key, db, "turbulence")
    enforce_lab_binding(key, [rec.lab_id for rec in batch.records])
    accepted = 0
    quarantined = 0
    dead_letters: list[dict[str, Any]] = []
    for i, rec in enumerate(batch.records):
        try:
            _verify_lab(db, rec.lab_id)
            if not rec.collector_signature:
                # Unsigned lab payloads quarantine — never promoted (TW-208).
                quarantined += 1
                continue
            artifact_id = str(uuid.uuid4())
            db.table("sci_bronze_artifacts").insert(
                {
                    "id": artifact_id,
                    "lane": LANE,
                    "lab_id": rec.lab_id,
                    "storage_path": f"{batch.storage_path_prefix}/{artifact_id}.json",
                    "content_hash": rec.content_hash,
                    "collector_signature": rec.collector_signature,
                    "collected_at": rec.collected_at or rec.measured_at,
                    "schema_version": batch.schema_version,
                }
            ).execute()
            row = rec.model_dump(mode="json")
            row.pop("content_hash", None)
            row.pop("collector_signature", None)
            row.pop("collected_at", None)
            row["artifact_id"] = artifact_id
            # id left to the DB default (gen_random_uuid) — never client-set.
            db.table(_MEAS_TABLE).insert(row).execute()
            accepted += 1
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — per-record isolation (TW-208)
            dead_letters.append({"index": i, "error": str(exc),
                                 "case_name": rec.case_name})
            log.warning("turbulence ingest dead-letter",
                        extra={"index": i, "error": str(exc)})
    _emit_toll(auth.org_id, "data.science.ingest", request_id,
               quantity=float(accepted))
    result = IngestResponse(accepted=accepted, quarantined=quarantined,
                          dead_letters=dead_letters,
                          batch_id=f"sci:{request_id}")
    return paywall.finalize(result, db, auth.org_id, request_id, response)


def _verify_lab(db: Any, lab_id: str) -> dict[str, Any]:
    res = db.table("sci_labs").select("id,verification_status").eq("id", lab_id).execute()
    labs = getattr(res, "data", []) or []
    if not labs:
        raise HTTPException(status_code=400, detail=f"unknown lab {lab_id}")
    if labs[0].get("verification_status") != "verified":
        raise HTTPException(status_code=403, detail=f"lab {lab_id} is not verified")
    return labs[0]
