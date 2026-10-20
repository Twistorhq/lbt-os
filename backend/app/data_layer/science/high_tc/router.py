"""High-Tc lane HTTP API (TW-593 Track 2).

Mounted by Dre's integration pass under /data/v1/science/high_tc/
(see the architecture doc §4). This module owns only the lane routes —
no shared files touched.

Tolling (TW-549): every served data call emits to toll_events via
app.services.toll_service.record_toll_event, idempotency_key=f"sci:{request_id}".
Dre wires TOLL_RATES["data.science.*"] in integration; until then an unknown
event type raises ValueError inside the toll service, which we catch, log,
and continue — a toll hiccup never fails a read.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from supabase import Client

from ....database import get_db
from ....services.toll_service import record_toll_event
from .. import paywall
from ..auth import enforce_lab_binding, require_science_key
from . import schemas

log = logging.getLogger(__name__)

router = APIRouter(prefix="/data/v1/science/high_tc", tags=["science-high_tc"])

LANE = "high_tc"

# Placeholder org until Dre wires the science-account mapping in integration.
ORG_SCIENCE = "twistor_science"

TOLL_QUERY = "data.science.query"
TOLL_BULK = "data.science.bulk"
TOLL_INGEST = "data.science.ingest"
TOLL_BENCHMARK = "data.science.benchmark"


# ---------------------------------------------------------------------------
# Toll emission (never fails the request)
# ---------------------------------------------------------------------------
def _emit_toll(
    db: Client,
    *,
    event_type: str,
    request_id: str,
    quantity: float = 1.0,
) -> None:
    try:
        record_toll_event(
            db,
            ORG_SCIENCE,
            event_type,
            quantity=quantity,
            idempotency_key=f"sci:{request_id}",
        )
    except ValueError as exc:  # unknown toll event type — rates are wired; stay defensive
        log.warning("toll_skipped", extra={"event_type": event_type, "err": str(exc)})
    except Exception as exc:  # TW-208: metering failure never breaks serving
        log.warning("toll_emit_failed", extra={"event_type": event_type, "err": str(exc)})


def _request_id(x_request_id: str | None) -> str:
    return x_request_id or uuid.uuid4().hex


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _latest_version(db: Client) -> dict | None:
    res = (
        db.table("sci_dataset_versions")
        .select("*")
        .eq("lane", LANE)
        .order("released_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _provenance(db: Client, version: dict | None, rows: list[dict]) -> schemas.ProvenanceSummary:
    attested = sum(
        1 for r in rows if (r.get("attestation_summary") or {}).get("reproduced", 0) > 0
    )
    lab_count = 0
    if rows and version:
        silver_ids = list({r["silver_id"] for r in rows if r.get("silver_id")})
        if silver_ids:
            res = (
                db.table("sci_hightc_measurements")
                .select("lab_id")
                .in_("id", silver_ids)
                .execute()
            )
            lab_count = len({r.get("lab_id") for r in (res.data or []) if r.get("lab_id")})
    return schemas.ProvenanceSummary(
        lab_count=lab_count,
        attested_rows=attested,
        unattested_rows=len(rows) - attested,
    )


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
@router.get("/datasets", response_model=schemas.DatasetsResponse)
def list_datasets(db: Annotated[Client, Depends(get_db)], response: Response = None):
    """List immutable dataset versions for the high-Tc lane (metadata read)."""
    paywall.enforce_rate_limit(db, ORG_SCIENCE)  # TW-608: 429 shutoff
    res = (
        db.table("sci_dataset_versions")
        .select("*")
        .eq("lane", LANE)
        .order("released_at", desc=True)
        .execute()
    )
    versions = [schemas.DatasetVersionRead(**_json_safe(v)) for v in (res.data or [])]
    latest = versions[0].semver if versions else None
    result = schemas.DatasetsResponse(
        as_of=latest,
        synthetic=any(v.doi is None for v in versions) if versions else False,
        versions=versions,
    )
    return paywall.finalize(result, db, ORG_SCIENCE, _request_id(None), response)


@router.get("/datasets/{semver}", response_model=schemas.DatasetDetailResponse)
def dataset_detail(semver: str, db: Annotated[Client, Depends(get_db)], response: Response = None):
    """Snapshot metadata + merkle root for one pinned version."""
    paywall.enforce_rate_limit(db, ORG_SCIENCE)  # TW-608: 429 shutoff
    res = (
        db.table("sci_dataset_versions")
        .select("*")
        .eq("lane", LANE)
        .eq("semver", semver)
        .maybe_single()
        .execute()
    )
    version = schemas.DatasetVersionRead(**_json_safe(res.data)) if res.data else None
    if version is None:
        raise HTTPException(status_code=404, detail=f"unknown dataset version {semver}")
    corr = (
        db.table("sci_corrections")
        .select("*")
        .eq("lane", LANE)
        .eq("new_dataset_version_id", str(version.id))
        .execute()
    )
    corrections = [schemas.CorrectionRead(**_json_safe(c)) for c in (corr.data or [])]
    result = schemas.DatasetDetailResponse(
        as_of=version.semver, version=version, corrections_since=corrections
    )
    return paywall.finalize(result, db, ORG_SCIENCE, _request_id(None), response)


# ---------------------------------------------------------------------------
# Gold query
# ---------------------------------------------------------------------------
@router.get("/query", response_model=schemas.QueryResponse)
def query_gold(
    db: Annotated[Client, Depends(get_db)],
    response: Response = None,
    x_request_id: Annotated[str | None, Header()] = None,
    structure: Annotated[str | None, Query()] = None,
    min_tc_k: Annotated[float | None, Query(ge=0)] = None,
    max_tc_k: Annotated[float | None, Query(ge=0)] = None,
    min_pressure_gpa: Annotated[float | None, Query(ge=0)] = None,
    max_pressure_gpa: Annotated[float | None, Query(ge=0)] = None,
    synthetic: Annotated[bool | None, Query()] = None,
    dataset_version: Annotated[str | None, Query()] = None,
    bulk: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=5000)] = 100,
):
    paywall.enforce_rate_limit(db, ORG_SCIENCE)  # TW-608: 429 shutoff
    """Filtered gold query. Tolls data.science.query; bulk exports also toll
    data.science.bulk per 1k rows."""
    request_id = _request_id(x_request_id)

    if dataset_version:
        res = (
            db.table("sci_dataset_versions")
            .select("*")
            .eq("lane", LANE)
            .eq("semver", dataset_version)
            .maybe_single()
            .execute()
        )
        version = res.data
    else:
        version = _latest_version(db)

    q = db.table("sci_hightc_gold").select("*")
    if version:
        q = q.eq("dataset_version_id", version["id"])
    if structure:
        q = q.eq("structure", structure)
    if min_tc_k is not None:
        q = q.gte("tc_k", min_tc_k)
    if max_tc_k is not None:
        q = q.lte("tc_k", max_tc_k)
    if min_pressure_gpa is not None:
        q = q.gte("pressure_gpa", min_pressure_gpa)
    if max_pressure_gpa is not None:
        q = q.lte("pressure_gpa", max_pressure_gpa)
    if synthetic is not None:
        q = q.eq("synthetic", synthetic)
    res = q.limit(limit).execute()
    raw_rows = res.data or []

    _emit_toll(db, event_type=TOLL_QUERY, request_id=request_id)
    if bulk:
        _emit_toll(
            db,
            event_type=TOLL_BULK,
            request_id=request_id,
            quantity=max(1.0, math.ceil(len(raw_rows) / 1000)),
        )

    rows = [schemas.GoldRowRead(**_json_safe(r)) for r in raw_rows]
    result = schemas.QueryResponse(
        as_of=version["semver"] if version else None,
        provenance=_provenance(db, version, raw_rows),
        synthetic=any(r.synthetic for r in rows),
        rows=rows,
        row_count=len(rows),
        bulk=bulk,
    )
    return paywall.finalize(result, db, ORG_SCIENCE, request_id, response)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------
@router.get("/benchmarks", response_model=schemas.BenchmarksResponse)
def list_benchmarks(db: Annotated[Client, Depends(get_db)], response: Response = None):
    """Open benchmarks + leaderboards for the lane (metadata read)."""
    paywall.enforce_rate_limit(db, ORG_SCIENCE)  # TW-608: 429 shutoff
    res = db.table("sci_benchmarks").select("*").eq("lane", LANE).execute()
    out: list[schemas.BenchmarkRead] = []
    for b in res.data or []:
        runs = (
            db.table("sci_benchmark_runs")
            .select("*")
            .eq("benchmark_id", b["id"])
            .order("score", desc=True)
            .execute()
        )
        out.append(
            schemas.BenchmarkRead(
                **_json_safe({**b, "leaderboard": runs.data or []})
            )
        )
    result = schemas.BenchmarksResponse(benchmarks=out)
    return paywall.finalize(result, db, ORG_SCIENCE, _request_id(None), response)


@router.post("/benchmarks/runs", response_model=schemas.BenchmarkRunRead, status_code=201)
def submit_benchmark_run(
    payload: schemas.BenchmarkRunSubmit,
    db: Annotated[Client, Depends(get_db)],
    response: Response = None,
    x_request_id: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
):
    """Submit a benchmark run. Manifest required — no manifest, no leaderboard."""
    paywall.enforce_rate_limit(db, ORG_SCIENCE)  # TW-608: 429 shutoff
    request_id = _request_id(x_request_id)
    require_science_key(x_api_key, db, "high_tc")  # auth gate; key unused after
    bench = (
        db.table("sci_benchmarks")
        .select("*")
        .eq("lane", LANE)
        .eq("name", payload.benchmark_name)
        .maybe_single()
        .execute()
    )
    if not bench.data:
        raise HTTPException(
            status_code=404, detail=f"unknown benchmark {payload.benchmark_name}"
        )
    # No manifest, no leaderboard: the manifest must exist in sci_manifests.
    manifest = (
        db.table("sci_manifests")
        .select("id")
        .eq("id", str(payload.manifest_id))
        .maybe_single()
        .execute()
    )
    if not manifest.data:
        raise HTTPException(
            status_code=404, detail=f"unknown manifest {payload.manifest_id}"
        )
    row = {
        "benchmark_id": bench.data["id"],
        "submitter": payload.submitter,
        "score": payload.score,
        "manifest_id": str(payload.manifest_id),
    }
    res = db.table("sci_benchmark_runs").insert(row).execute()
    _emit_toll(db, event_type=TOLL_BENCHMARK, request_id=request_id)
    data = dict((res.data or [row])[0])
    # Server-side defaults may already be present; fill only if the
    # insert response came back bare.
    data.setdefault("id", str(uuid.uuid4()))
    data.setdefault("submitted_at", datetime.now(timezone.utc).isoformat())
    result = schemas.BenchmarkRunRead(**_json_safe(data))
    # BenchmarkRunRead is not a ResponseEnvelope: usage rides the headers.
    return paywall.finalize(result, db, ORG_SCIENCE, request_id, response)


# ---------------------------------------------------------------------------
# Corrections feed
# ---------------------------------------------------------------------------
@router.get("/corrections", response_model=schemas.CorrectionsResponse)
def correction_feed(
    db: Annotated[Client, Depends(get_db)],
    response: Response = None,
    dataset_version: Annotated[str | None, Query()] = None,
):
    paywall.enforce_rate_limit(db, ORG_SCIENCE)  # TW-608: 429 shutoff
    """Correction feed per dataset — errata propagate forward, history intact."""
    q = db.table("sci_corrections").select("*").eq("lane", LANE)
    if dataset_version:
        version = (
            db.table("sci_dataset_versions")
            .select("id")
            .eq("lane", LANE)
            .eq("semver", dataset_version)
            .maybe_single()
            .execute()
        )
        if version.data:
            q = q.eq("new_dataset_version_id", version.data["id"])
    res = q.order("corrected_at", desc=True).execute()
    corrections = [schemas.CorrectionRead(**_json_safe(c)) for c in (res.data or [])]
    result = schemas.CorrectionsResponse(as_of=dataset_version, corrections=corrections)
    return paywall.finalize(result, db, ORG_SCIENCE, _request_id(None), response)


# ---------------------------------------------------------------------------
# Ingest (signed lab batches; per-record self-healing via dead_letters)
# ---------------------------------------------------------------------------
@router.post("/ingest", response_model=schemas.IngestResponse, status_code=201)
def ingest_batch(
    payload: schemas.IngestRequest,
    db: Annotated[Client, Depends(get_db)],
    response: Response = None,
    x_request_id: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
):
    paywall.enforce_rate_limit(db, ORG_SCIENCE)  # TW-608: 429 shutoff
    """Batch ingestion. One bad record -> dead_letters, never kills the run."""
    request_id = _request_id(x_request_id)
    key = require_science_key(x_api_key, db, "high_tc")
    enforce_lab_binding(key, [str(r.lab_id) for r in payload.records])
    accepted = 0
    dead_letters: list[dict] = []
    for rec in payload.records:
        try:
            row = _json_safe(rec.model_dump())
            res = db.table("sci_hightc_measurements").insert(row).execute()
            if res.data:
                accepted += 1
            else:
                dead_letters.append({"record": row, "error": "insert returned no data"})
        except Exception as exc:  # noqa: BLE001 - per-record isolation is the point
            log.warning("ingest_dead_letter", extra={"error": str(exc)[:200]})
            dead_letters.append(
                {"record": _json_safe(rec.model_dump()), "error": str(exc)[:500]}
            )

    _emit_toll(
        db, event_type=TOLL_INGEST, request_id=request_id, quantity=float(len(payload.records))
    )
    version = _latest_version(db)
    result = schemas.IngestResponse(
        as_of=version["semver"] if version else None,
        synthetic=all(r.synthetic for r in payload.records),
        accepted=accepted,
        rejected=len(dead_letters),
        dead_letters=dead_letters,
    )
    return paywall.finalize(result, db, ORG_SCIENCE, request_id, response)
