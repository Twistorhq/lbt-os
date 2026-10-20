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

Tolling (TW-549) + paywall (TW-599): every served call is metered via
paywall.metered_toll (dynamic derivative pricing: volume multiplier on
trailing-30d science spend); every READ needs an active Twistor subscription
(402 otherwise). Excel downloads at GET /datasets/{version}/download carry a
one-time fee on top of the subscription: the dataset's live curve price
(pennies, hard-capped at $0.10 per download; large = API only).
Idempotency key = f"sci:{request_id}".
A toll failure never breaks a read (TW-208).

Split discipline: /query defaults to split='train'. 'test_future' rows
(the benchmark's withheld 2035-like conditions) are served with yields
masked; the withheld answers are pinned by the benchmark's
hidden_split_hash. Test rows never leak into train queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from .. import paywall
from ..auth import enforce_lab_binding, require_science_key
from . import models, schemas

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
    """Production store factory: Supabase-backed (Rosa Major 2).

    The in-memory CropClimateStore remains the test double — tests override
    this dependency with a seeded store.
    """
    from ....database import get_db
    from .supabase_store import SupabaseCropClimateStore

    return SupabaseCropClimateStore(get_db())


router = APIRouter(
    prefix="/data/v1/science/crop_climate",
    tags=["science:crop_climate"],
    dependencies=[Depends(get_api_key)],
)


# ------------------------------------------------------------------ tolling
def _toll_db(store: models.CropClimateStore):
    """Toll/metering client: the Supabase client's db, or the in-memory
    store itself (its table() shim serves toll_events in tests)."""
    return getattr(store, "_db", store)


def _gate(store: models.CropClimateStore, org_id: str | None) -> str:
    """TW-599 paywall: every read needs an active Twistor subscription."""
    return paywall.require_active_subscription(_toll_db(store), org_id)


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
    """Metered toll with dynamic derivative pricing (TW-599 paywall).

    Delegates to paywall.metered_toll: volume multiplier from the org's
    trailing-30d science spend. Failures logged and swallowed (TW-208).
    """
    try:
        paywall.metered_toll(
            db, org_id, event_type, request_id, quantity=quantity,
            source_type=source_type, source_id=source_id,
        )
    except Exception as exc:  # TW-208: metering never breaks serving
        log.warning("metered toll %s failed (%s); continuing", event_type, exc)


def _toll(request_id: str, event_type: str, org_id: str = "public",
          quantity: float = 1, source_type: str | None = None,
          source_id: str | None = None,
          store: models.CropClimateStore | None = None) -> None:
    """Emit a toll under an explicit request_id (TW-607).

    The caller computes request_id ONCE via _request_id(request) and threads
    it through both _toll and paywall.finalize, so the usage block looks up
    the same idempotency key the toll was charged under. Minting a second
    ID here would make the readout lie ($0.00 on a charged call).
    """
    _toll_best_effort(
        request_id, event_type, quantity=quantity,
        source_type=source_type, source_id=source_id, org_id=org_id,
        db=_toll_db(store) if store is not None else None)


def _neutral_toll(db: Any, org_id: str, request_id: str, lane: str,
                  rows_served: int, dataset_version_id: str | None = None) -> None:
    """Neutral data toll (TW-606): rows_served x per-row rate.

    Delegates to paywall.emit_neutral_data_toll. Failures are logged and
    swallowed (TW-208): metering never breaks serving.
    """
    try:
        paywall.emit_neutral_data_toll(
            db, org_id, request_id, lane, rows_served,
            source_type="sci_crop_gold", dataset_version_id=dataset_version_id,
        )
    except Exception as exc:  # TW-208
        log.warning("neutral toll emission failed (%s); continuing", exc)


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
def list_datasets(request: Request, response: Response = None,
                  x_org_id: Annotated[str | None, Header()] = None,
                  store: models.CropClimateStore = Depends(get_store)):
    org_id = _gate(store, x_org_id)
    request_id = _request_id(request)
    _toll(request_id, EVENT_QUERY, org_id=org_id,
          source_type="sci_dataset_versions", store=store)
    versions = [schemas.DatasetVersionRead(
        id=v.id, lane=v.lane, dataset_name=v.dataset_name, semver=v.semver,
        row_count=v.row_count, content_root_hash=v.content_root_hash,
        benchmark_pin=v.benchmark_pin, doi=v.doi, supersedes_id=v.supersedes_id,
        released_at=v.released_at, release_notes=v.release_notes).model_dump()
        for v in store.list_versions()]
    latest = store.list_versions()[-1]
    result = _dataset_envelope(store, latest, versions)
    return paywall.finalize(result, _toll_db(store), org_id, request_id, response)


@router.get("/datasets/{version}", response_model=schemas.Envelope)
def get_dataset(version: str, request: Request, response: Response = None,
                x_org_id: Annotated[str | None, Header()] = None,
                store: models.CropClimateStore = Depends(get_store)):
    v = _version_or_404(store, version)
    org_id = _gate(store, x_org_id)
    request_id = _request_id(request)
    _toll(request_id, EVENT_QUERY, org_id=org_id,
          source_type="sci_dataset_versions", source_id=v.id, store=store)
    payload = schemas.DatasetVersionRead(
        id=v.id, lane=v.lane, dataset_name=v.dataset_name, semver=v.semver,
        row_count=v.row_count, content_root_hash=v.content_root_hash,
        benchmark_pin=v.benchmark_pin, doi=v.doi, supersedes_id=v.supersedes_id,
        released_at=v.released_at, release_notes=v.release_notes).model_dump()
    result = _dataset_envelope(store, v, payload)
    return paywall.finalize(result, _toll_db(store), org_id, request_id, response)


@router.get("/query", response_model=schemas.Envelope)
def query_gold(
    request: Request,
    response: Response = None,
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
    org_id = _gate(store, x_org_id)
    resolved = store.get_version(version) if version else store.list_versions()[-1]
    # TW-606: neutral data toll -- rows_served x per-row rate, converging to
    # the dataset's current download price on a full pull. Replaces the flat
    # per-query + bulk tolls so API and download stay economically neutral.
    request_id = _request_id(request)
    _neutral_toll(_toll_db(store), org_id, request_id,
                  resolved.lane, len(rows), dataset_version_id=resolved.id)
    result = _dataset_envelope(store, resolved, rows)
    return paywall.finalize(result, _toll_db(store), org_id, request_id, response)


@router.get("/query/{gold_id}/provenance", response_model=schemas.Envelope)
def gold_provenance(gold_id: str, request: Request, response: Response = None,
                    x_org_id: Annotated[str | None, Header()] = None,
                    store: models.CropClimateStore = Depends(get_store)):
    try:
        chain = store.provenance_chain(gold_id)
    except models.StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    version = store.versions[chain["gold"]["dataset_version_id"]]
    org_id = _gate(store, x_org_id)
    request_id = _request_id(request)
    _toll(request_id, EVENT_QUERY, org_id=org_id,
          source_type="sci_crop_gold", source_id=gold_id, store=store)
    result = _dataset_envelope(store, version, chain)
    return paywall.finalize(result, _toll_db(store), org_id, request_id, response)


@router.get("/benchmarks", response_model=schemas.Envelope)
def list_benchmarks(request: Request, response: Response = None,
                     x_org_id: Annotated[str | None, Header()] = None,
                     store: models.CropClimateStore = Depends(get_store)):
    org_id = _gate(store, x_org_id)
    request_id = _request_id(request)
    _toll(request_id, EVENT_BENCHMARK, org_id=org_id,
          source_type="sci_benchmarks", store=store)
    benchmarks = store.list_benchmarks()
    latest = store.list_versions()[-1]
    result = _dataset_envelope(store, latest, benchmarks)
    return paywall.finalize(result, _toll_db(store), org_id, request_id, response)


@router.post("/ingest", response_model=schemas.IngestResponse)
def ingest_batch(payload: schemas.IngestRequest, request: Request,
                 response: Response = None,
                 x_org_id: Annotated[str | None, Header()] = None,
                 store: models.CropClimateStore = Depends(get_store),
                 x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None):
    """Signed lab ingestion. Per-record: one bad record -> dead_letters,
    never kills the run (TW-208). Unsigned payloads quarantine, never promote."""
    key = require_science_key(x_api_key, store, "crop_climate")
    enforce_lab_binding(key, [str(payload.lab_id)])
    if payload.lab_id not in store.labs:
        raise HTTPException(status_code=422,
                            detail=f"unknown lab_id {payload.lab_id}")
    request_id = _request_id(request)
    org_id = x_org_id or "public"
    if not payload.collector_signature:
        # quarantine: unsigned payloads never promote
        dead = [{"index": i, "error": "missing collector_signature: quarantined"}
                for i in range(len(payload.records))]
        store.dead_letters.extend(dead)
        result = schemas.IngestResponse(accepted=0, rejected=len(dead),
                                      dead_letters=dead)
        return paywall.finalize(result, _toll_db(store), org_id,
                                request_id, response)

    content_hash = "sha256:" + hashlib.sha256(
        json.dumps(payload.records, sort_keys=True, default=str).encode()).hexdigest()
    artifact = store.register_artifact(
        lab_id=payload.lab_id,
        storage_path=f"s3://twistor-sci-bronze/crop_climate/ingest/{request_id}.json",
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
                traits=record.traits,
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
    _toll(request_id, EVENT_INGEST, org_id=x_org_id or "public", quantity=accepted,
          source_type="sci_bronze_artifacts", source_id=artifact.id, store=store)
    result = schemas.IngestResponse(accepted=accepted, rejected=rejected,
                                  receipt_ids=receipts,
                                  dead_letters=dead_letters)
    return paywall.finalize(result, _toll_db(store), x_org_id or "public",
                            request_id, response)


@router.get("/corrections", response_model=schemas.Envelope)
def correction_feed(
    request: Request,
    response: Response = None,
    dataset: Annotated[str, Query()] = models.DATASET_NAME,
    x_org_id: Annotated[str | None, Header()] = None,
    store: models.CropClimateStore = Depends(get_store),
):
    org_id = _gate(store, x_org_id)
    request_id = _request_id(request)
    _toll(request_id, EVENT_QUERY, org_id=org_id,
          source_type="sci_corrections", store=store)
    latest = store.list_versions()[-1]
    result = _dataset_envelope(store, latest, store.list_corrections(dataset))
    return paywall.finalize(result, _toll_db(store), org_id, request_id, response)


@router.post("/corrections", response_model=schemas.Envelope)
def file_correction(payload: schemas.CorrectionCreate, request: Request,
                    response: Response = None,
                    x_org_id: Annotated[str | None, Header()] = None,
                    store: models.CropClimateStore = Depends(get_store),
                    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None):
    """Errata propagate forward: a correction mints a new dataset version.
    History is never mutated."""
    key = require_science_key(x_api_key, store, "crop_climate")
    enforce_lab_binding(key, [str(payload.corrected_by)])
    try:
        new_version = store.correct_measurement(
            silver_id=payload.corrects_silver_id,
            corrected_values=payload.corrected_values,
            reason=payload.reason,
            corrected_by=payload.corrected_by,
        )
    except models.StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request_id = _request_id(request)
    _toll(request_id, EVENT_INGEST, org_id=x_org_id or "public",
          source_type="sci_corrections", source_id=new_version.id, store=store)
    result = _dataset_envelope(store, new_version, {"new_version": new_version.semver,
                                                  "supersedes": new_version.supersedes_id})
    return paywall.finalize(result, _toll_db(store), x_org_id or "public",
                            request_id, response)


@router.get("/datasets/{version}/download")
def download_dataset(version: str, request: Request,
                     x_org_id: Annotated[str | None, Header()] = None,
                     store: models.CropClimateStore = Depends(get_store)):
    """Excel download: subscription + one-time curve fee (TW-606).

    Small/medium tiers only -- large datasets are API-only (403). The fee
    is the dataset's live curve price: pennies, hard-capped at $0.10.
    """
    org_id = _gate(store, x_org_id)
    v = _version_or_404(store, version)
    tier = paywall.dataset_tier(v.row_count)
    paywall.check_excel_delivery(tier, v.row_count)

    rows = store.query_gold(version=version, split="all")[:500_000]
    columns = [
        "id", "genotype", "location", "year", "split",
        "yield_t_ha", "yield_withheld",
        "thc_pct", "cbd_pct", "cbg_pct", "thc_compliant_03",
        "fiber_quality_grade", "lab_accreditation_id",
        "synthetic",
    ]
    flat = []
    for r in rows:
        traits = r.get("traits") or {}
        flat.append({
            "id": r["id"],
            "genotype": r["genotype"],
            "location": r["location"],
            "year": r["year"],
            "split": r["split"],
            "yield_t_ha": r["yield_t_ha"],
            "yield_withheld": r["yield_withheld"],
            "thc_pct": traits.get("thc_pct"),
            "cbd_pct": traits.get("cbd_pct"),
            "cbg_pct": traits.get("cbg_pct"),
            "thc_compliant_03": traits.get("thc_compliant_03"),
            "fiber_quality_grade": traits.get("fiber_quality_grade"),
            "lab_accreditation_id": traits.get("lab_accreditation_id"),
            "synthetic": r["synthetic"],
        })
    fee = paywall.download_fee_cents(_toll_db(store), org_id, v.id)
    content = paywall.build_excel_bytes(
        flat, columns, v.dataset_name, v.semver, tier, fee,
        synthetic=all(r["synthetic"] for r in rows) if rows else True,
    )
    request_id = _request_id(request)
    paywall.emit_download_toll(_toll_db(store), org_id, request_id, v.id)
    _toll(request_id, EVENT_QUERY, org_id=org_id,
          source_type="sci_crop_gold", source_id=v.id, store=store)
    # TW-607: EOM usage block in X-Twistor-* headers (body is the workbook).
    usage = paywall.build_usage_block(
        _toll_db(store), org_id, request_id=request_id, records=len(rows)
    )
    dl = paywall.excel_response(content, v.dataset_name, v.semver)
    paywall.set_usage_headers(dl, usage)
    return dl
