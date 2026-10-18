"""TW-593 Track 2 crop-climate lane: serving router (TW-596).

Mounted (at integration, Dre's pass) under /data/v1/science/crop_climate/:

  GET  /datasets            list dataset versions
  GET  /datasets/{version}  snapshot metadata + merkle root
  GET  /query               gold query (genotype/location/year/split filters)
  GET  /query/{gold_id}/provenance   provenance chain walk
  GET  /benchmarks          open benchmarks + leaderboard
  POST /ingest              signed lab ingestion (batch, per-record)
  GET  /corrections         correction feed per dataset
  POST /corrections         file a correction (supersession, new version)

Tolling (TW-549): every served call emits to toll_events via
app.services.toll_service.record_toll_event, best-effort — a toll failure
never breaks a read (TW-208). Idempotency key = f"sci:{request_id}".
Dre wires the data.science.* rates into TOLL_RATES at integration.

Split discipline: /query defaults to split='train'. 'test_future' rows
(the benchmark's withheld 2035-like conditions) are served with yields
masked; the withheld answers are pinned by the benchmark's
hidden_split_hash. Test rows never leak into train queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.services import toll_service

from . import models, schemas, seed

log = logging.getLogger(__name__)

EVENT_QUERY = "data.science.query"
EVENT_BULK = "data.science.bulk"
EVENT_INGEST = "data.science.ingest"
EVENT_BENCHMARK = "data.science.benchmark"

SplitParam = Literal["train", "test_future", "all"]


# ------------------------------------------------------------ dependencies
def get_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> str | None:
    """V1 passthrough: API-key enforcement against data_api_keys is wired
    at integration (Dre). The header is accepted and threaded through."""
    return x_api_key


_store: models.CropClimateStore | None = None


def get_store() -> models.CropClimateStore:
    """Demo store factory. Integration swaps this for the Supabase adapter."""
    global _store
    if _store is None:
        _store = seed.bootstrap_demo_store()
    return _store


router = APIRouter(
    prefix="/data/v1/science/crop_climate",
    tags=["science:crop_climate"],
    dependencies=[Depends(get_api_key)],
)


# ------------------------------------------------------------------ tolling
def _request_id(request: Request | None) -> str:
    if request is not None:
        header = request.headers.get("x-request-id")
        if header:
            return header
    return uuid.uuid4().hex


def _toll_best_effort(
    request_id: str,
    event_type: str,
    quantity: float = 1,
    source_type: str | None = None,
    source_id: str | None = None,
    org_id: str = "public",
    db=None,
) -> None:
    """Emit one toll event. Unknown event types (ValueError) are logged and
    skipped — Dre wires the data.science.* rates into TOLL_RATES at
    integration. Tolls never break reads (TW-208)."""
    try:
        toll_service.record_toll_event(
            db,
            org_id=org_id,
            event_type=event_type,
            quantity=quantity,
            idempotency_key=f"sci:{request_id}",
            source_type=source_type,
            source_id=source_id,
        )
    except ValueError as exc:
        log.warning(
            "crop-climate toll emit failed; continuing without toll",
            extra={"event": event_type, "error": str(exc)},
        )


def _toll(request: Request | None, event_type: str, org_id: str = "public",
          quantity: float = 1, source_type: str | None = None,
          source_id: str | None = None) -> None:
    _toll_best_effort(
        _request_id(request), event_type, quantity=quantity,
        source_type=source_type, source_id=source_id, org_id=org_id)


# ---------------------------------------------------------------- envelopes
def _attestation_rollup(store: models.CropClimateStore,
                        version_id: str) -> dict:
    rollup = {"reproduced": 0, "partial": 0, "failed": 0, "unattested": 0}
    for row in store.gold.values():
        if row.dataset_version_id != version_id:
            continue
        summary = row.attestation_summary or {}
        if not summary:
            rollup["unattested"] += 1
        for key in ("reproduced", "partial", "failed"):
            rollup[key] += int(summary.get(key, 0) or 0)
    return rollup


def _dataset_envelope(store: models.CropClimateStore,
                      version: models.DatasetVersion,
                      data) -> dict:
    verified = sum(1 for lab in store.labs.values()
                   if lab.verification_status == "verified")
    rows = [r for r in store.gold.values()
            if r.dataset_version_id == version.id]
    return {
        "as_of": {
            "dataset_name": version.dataset_name,
            "semver": version.semver,
            "content_root_hash": version.content_root_hash,
        },
        "provenance": {
            "lab_count": len(store.labs),
            "verified_labs": verified,
            "attestation_summary": _attestation_rollup(store, version.id),
        },
        "synthetic": all(r.synthetic for r in rows) if rows else True,
        "data": data,
    }


def _version_or_404(store: models.CropClimateStore, version: str):
    try:
        return store.get_version(version)
    except models.StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ------------------------------------------------------------------- routes
@router.get("/datasets", response_model=schemas.Envelope)
def list_datasets(request: Request,
                  x_org_id: Annotated[str | None, Header()] = None,
                  store: models.CropClimateStore = Depends(get_store)):
    _toll(request, EVENT_QUERY, org_id=x_org_id or "public",
          source_type="sci_dataset_versions")
    versions = [schemas.DatasetVersionRead(
        id=v.id, lane=v.lane, dataset_name=v.dataset_name, semver=v.semver,
        row_count=v.row_count, content_root_hash=v.content_root_hash,
        benchmark_pin=v.benchmark_pin, doi=v.doi, supersedes_id=v.supersedes_id,
        released_at=v.released_at, release_notes=v.release_notes).model_dump()
        for v in store.list_versions()]
    latest = store.list_versions()[-1]
    return _dataset_envelope(store, latest, versions)


@router.get("/datasets/{version}", response_model=schemas.Envelope)
def get_dataset(version: str, request: Request,
                x_org_id: Annotated[str | None, Header()] = None,
                store: models.CropClimateStore = Depends(get_store)):
    v = _version_or_404(store, version)
    _toll(request, EVENT_QUERY, org_id=x_org_id or "public",
          source_type="sci_dataset_versions", source_id=v.id)
    payload = schemas.DatasetVersionRead(
        id=v.id, lane=v.lane, dataset_name=v.dataset_name, semver=v.semver,
        row_count=v.row_count, content_root_hash=v.content_root_hash,
        benchmark_pin=v.benchmark_pin, doi=v.doi, supersedes_id=v.supersedes_id,
        released_at=v.released_at, release_notes=v.release_notes).model_dump()
    return _dataset_envelope(store, v, payload)


@router.get("/query", response_model=schemas.Envelope)
def query_gold(
    request: Request,
    genotype: Annotated[str | None, Query()] = None,
    location: Annotated[str | None, Query()] = None,
    year: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    split: Annotated[SplitParam, Query()] = "train",
    version: Annotated[str | None, Query()] = None,
    bulk: Annotated[bool, Query()] = False,
    x_org_id: Annotated[str | None, Header()] = None,
    store: models.CropClimateStore = Depends(get_store),
):
    """Gold query. split defaults to 'train' — the benchmark's withheld
    'test_future' rows are never served with yields; they come back masked."""
    try:
        rows = store.query_gold(version=version, genotype=genotype,
                                location=location, year=year, split=split)
    except models.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    resolved = store.get_version(version) if version else store.list_versions()[-1]
    if bulk:
        _toll(request, EVENT_BULK, org_id=x_org_id or "public",
              quantity=max(1, math.ceil(len(rows) / 1000)),
              source_type="sci_crop_gold", source_id=resolved.id)
    else:
        _toll(request, EVENT_QUERY, org_id=x_org_id or "public",
              source_type="sci_crop_gold", source_id=resolved.id)
    return _dataset_envelope(store, resolved, rows)


@router.get("/query/{gold_id}/provenance", response_model=schemas.Envelope)
def gold_provenance(gold_id: str, request: Request,
                    x_org_id: Annotated[str | None, Header()] = None,
                    store: models.CropClimateStore = Depends(get_store)):
    try:
        chain = store.provenance_chain(gold_id)
    except models.StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    version = store.versions[chain["gold"]["dataset_version_id"]]
    _toll(request, EVENT_QUERY, org_id=x_org_id or "public",
          source_type="sci_crop_gold", source_id=gold_id)
    return _dataset_envelope(store, version, chain)


@router.get("/benchmarks", response_model=schemas.Envelope)
def list_benchmarks(request: Request,
                     x_org_id: Annotated[str | None, Header()] = None,
                     store: models.CropClimateStore = Depends(get_store)):
    _toll(request, EVENT_BENCHMARK, org_id=x_org_id or "public",
          source_type="sci_benchmarks")
    benchmarks = store.list_benchmarks()
    latest = store.list_versions()[-1]
    return _dataset_envelope(store, latest, benchmarks)


@router.post("/ingest", response_model=schemas.IngestResponse)
def ingest_batch(payload: schemas.IngestRequest, request: Request,
                 x_org_id: Annotated[str | None, Header()] = None,
                 store: models.CropClimateStore = Depends(get_store)):
    """Signed lab ingestion. Per-record: one bad record -> dead_letters,
    never kills the run (TW-208). Unsigned payloads quarantine, never promote."""
    if payload.lab_id not in store.labs:
        raise HTTPException(status_code=422,
                            detail=f"unknown lab_id {payload.lab_id}")
    if not payload.collector_signature:
        # quarantine: unsigned payloads never promote
        dead = [{"index": i, "error": "missing collector_signature: quarantined"}
                for i in range(len(payload.records))]
        store.dead_letters.extend(dead)
        return schemas.IngestResponse(accepted=0, rejected=len(dead),
                                      dead_letters=dead)

    content_hash = "sha256:" + hashlib.sha256(
        json.dumps(payload.records, sort_keys=True, default=str).encode()).hexdigest()
    artifact = store.register_artifact(
        lab_id=payload.lab_id,
        storage_path=f"s3://twistor-sci-bronze/crop_climate/ingest/{_request_id(request)}.json",
        content_hash=content_hash,
        collector_signature=payload.collector_signature,
        schema_version=1,
    )
    accepted, rejected, receipts, dead_letters = 0, 0, [], []
    for i, raw in enumerate(payload.records):
        try:
            record = schemas.MeasurementCreate(**raw)
            m = store.add_measurement(
                artifact_id=artifact.id,
                lab_id=payload.lab_id,
                calibration_id=record.calibration_id,
                conditions=record.conditions,
                genotype=record.genotype,
                location=record.location,
                year=record.year,
                season=record.season,
                weather=record.weather,
                management=record.management,
                yield_t_ha=record.yield_t_ha,
                synthetic=record.synthetic,
            )
            accepted += 1
            receipts.append(m.id)
        except (models.StoreError, ValueError) as exc:
            rejected += 1
            entry = {"index": i, "error": str(exc),
                     "genotype": raw.get("genotype"),
                     "location": raw.get("location")}
            dead_letters.append(entry)
            store.dead_letters.append(entry)
    _toll(request, EVENT_INGEST, org_id=x_org_id or "public", quantity=accepted,
          source_type="sci_bronze_artifacts", source_id=artifact.id)
    return schemas.IngestResponse(accepted=accepted, rejected=rejected,
                                  receipt_ids=receipts,
                                  dead_letters=dead_letters)


@router.get("/corrections", response_model=schemas.Envelope)
def correction_feed(
    request: Request,
    dataset: Annotated[str, Query()] = models.DATASET_NAME,
    x_org_id: Annotated[str | None, Header()] = None,
    store: models.CropClimateStore = Depends(get_store),
):
    _toll(request, EVENT_QUERY, org_id=x_org_id or "public",
          source_type="sci_corrections")
    latest = store.list_versions()[-1]
    return _dataset_envelope(store, latest, store.list_corrections(dataset))


@router.post("/corrections", response_model=schemas.Envelope)
def file_correction(payload: schemas.CorrectionCreate, request: Request,
                    x_org_id: Annotated[str | None, Header()] = None,
                    store: models.CropClimateStore = Depends(get_store)):
    """Errata propagate forward: a correction mints a new dataset version.
    History is never mutated."""
    try:
        new_version = store.correct_measurement(
            silver_id=payload.corrects_silver_id,
            corrected_values=payload.corrected_values,
            reason=payload.reason,
            corrected_by=payload.corrected_by,
        )
    except models.StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _toll(request, EVENT_INGEST, org_id=x_org_id or "public",
          source_type="sci_corrections", source_id=new_version.id)
    return _dataset_envelope(store, new_version, {"new_version": new_version.semver,
                                                  "supersedes": new_version.supersedes_id})
