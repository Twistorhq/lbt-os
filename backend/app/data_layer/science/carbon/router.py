# Prepared by Twistor Holdings LLC
"""Carbon lane router (TW-599 Wave 1, CARBON-TRUST v1).

Mounted by Dre's integration pass under /data/v1/science/carbon/ —
this module is NOT self-mounted (main.py is shared; never touched).

Endpoints:
  GET  /datasets                    — list dataset versions
  GET  /datasets/{version}          — snapshot metadata + merkle root
  GET  /query                       — gold query (record_class / carbon_pool /
                                      analytical_method / comparable_group / region)
  GET  /benchmarks                  — open benchmarks + leaderboard
  GET  /benchmarks/mrv-blind/plots  — MRV-BLIND open challenge plots (sealed)
  POST /benchmarks/mrv-blind/submit — MRV-BLIND submission (lineage required)
  POST /ingest                      — signed lab ingestion (batch)

Method-code comparability gating: the query endpoint surfaces the
analytical_method on every row and accepts a comparable_group filter, so
Walkley-Black and dry-combustion records are never silently pooled.
Records with missing/'unknown' method codes quarantine at ingest.

Tolling (TW-549 + dynamic derivative doctrine): every served call attempts
a toll event with idempotency_key=f"sci:{request_id}". Unknown event types
raise ValueError in the toll service; we log and continue (tolling never
breaks serving).

Auth: X-API-Key (sci_api_keys, scoped) on mutations; every read needs an
active Twistor subscription (402 otherwise) — TW-599 paywall, day one.

Paywall (TW-599): subscription gate via paywall.require_active_subscription;
API tolls metered with dynamic derivative pricing
(paywall.metered_toll: volume multiplier on trailing-30d science spend);
Excel downloads at GET /datasets/{version}/download carry a one-time fee
on top of the subscription: the dataset's live curve price (pennies,
hard-capped at $0.10 per download; large = API only).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from .. import paywall
from ..auth import enforce_lab_binding, require_science_key
from . import models, schemas

log = logging.getLogger(__name__)

router = APIRouter(prefix="/data/v1/science/carbon", tags=["science-carbon"])

LANE = "carbon"
DATASET_NAME = "carbon-trust"
MRV_BLIND_BENCHMARK = "mrv-blind_v1"

# Toll event types (TW-549, shared science namespace).
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
    """Metered toll with dynamic derivative pricing (TW-599 paywall).

    Delegates to paywall.metered_toll: volume multiplier from the org's
    trailing-30d science spend. Failures are logged and swallowed (TW-208):
    metering never breaks serving.
    """
    try:
        paywall.metered_toll(
            db, org_id, event_type, request_id, quantity=quantity,
            source_type="sci_carbon",
        )
    except Exception as exc:  # TW-208: metering never breaks serving
        log.warning("toll emission %s failed (%s); continuing", event_type, exc)


def _emit_neutral_toll(
    db: Any,
    org_id: str,
    request_id: str,
    lane: str,
    rows_served: int,
) -> None:
    """Neutral data toll (TW-606): rows_served x per-row rate.

    Delegates to paywall.emit_neutral_data_toll. Failures are logged and
    swallowed (TW-208): metering never breaks serving.
    """
    try:
        paywall.emit_neutral_data_toll(
            db, org_id, request_id, lane, rows_served,
            source_type="sci_carbon",
        )
    except Exception as exc:  # TW-208
        log.warning("neutral toll emission failed (%s); continuing", exc)


def _gate(db: Any, org_id: str) -> str:
    """Paywall: every read needs an active Twistor subscription (402 otherwise)."""
    return paywall.require_active_subscription(db, org_id)


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
    return schemas.envelope(
        data=[schemas.DatasetVersionOut(**r).model_dump() for r in rows],
        dataset_name=DATASET_NAME,
        semver=rows[0]["semver"] if rows else "0.0.0",
        lab_count=0,
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
    record_class: str | None = None,
    carbon_pool: str | None = None,
    analytical_method: str | None = None,
    comparable_group: str | None = None,
    region: str | None = None,
    limit: int = 100,
) -> schemas.ScienceEnvelope:
    """Gold query. Method codes are always surfaced on every row — no silent
    pooling. comparable_group restricts to one cross-calibrated set."""
    if record_class is not None and record_class not in models.RECORD_CLASSES:
        raise ValueError(f"record_class must be one of {models.RECORD_CLASSES}")
    if carbon_pool is not None and carbon_pool not in models.CARBON_POOLS:
        raise ValueError(f"carbon_pool must be one of {models.CARBON_POOLS}")
    if analytical_method is not None and analytical_method not in models.METHOD_CODES:
        raise ValueError(f"analytical_method must be one of {models.METHOD_CODES}")
    if comparable_group is not None and comparable_group not in models.COMPARABLE_GROUPS:
        raise ValueError(f"comparable_group must be one of {tuple(models.COMPARABLE_GROUPS)}")
    q = db.table("sci_carbon_gold").select("*")
    if record_class:
        q = q.eq("record_class", record_class)
    if carbon_pool:
        q = q.eq("carbon_pool", carbon_pool)
    if analytical_method:
        q = q.eq("analytical_method", analytical_method)
    if comparable_group:
        q = q.eq("method_comparable_group", comparable_group)
    if region:
        q = q.eq("region", region)
    rows = _rows(q.limit(min(limit, 10000)).execute())
    # TW-606: neutral data toll -- rows_served x per-row rate, converging to
    # the dataset's current download price on a full pull. Replaces the flat
    # per-query + bulk tolls so API and download stay economically neutral.
    _emit_neutral_toll(db, org_id, request_id, LANE, len(rows))
    out = []
    for r in rows:
        out.append(
            schemas.MeasurementOut(
                id=r["id"],
                record_class=r["record_class"],
                carbon_pool=r["carbon_pool"],
                analytical_method=r["analytical_method"],
                method_comparable_group=r.get("method_comparable_group"),
                region=r["region"],
                site_id=r.get("site_id"),
                depth_top_cm=r.get("depth_top_cm"),
                depth_bottom_cm=r.get("depth_bottom_cm"),
                soc_g_kg=r.get("soc_g_kg"),
                bulk_density_g_cm3=r.get("bulk_density_g_cm3"),
                agb_mg_ha=r.get("agb_mg_ha"),
                agb_uncertainty_mg_ha=r.get("agb_uncertainty_mg_ha"),
                uncertainty_framework=r.get("uncertainty_framework"),
                h_c_org_ratio=r.get("h_c_org_ratio"),
                aromatic_c_pct=r.get("aromatic_c_pct"),
                persistence_100yr_pct=r.get("persistence_100yr_pct"),
                mrv_method_class=r.get("mrv_method_class"),
                baseline_credibility=r.get("baseline_credibility"),
                permanence_evidence=r.get("permanence_evidence"),
                synthetic=bool(r.get("synthetic", False)),
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
        runs = sorted(r.get("sci_benchmark_runs") or [], key=lambda x: x.get("score", 0))
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


def list_blind_plots(db: Any, org_id: str, request_id: str) -> schemas.ScienceEnvelope:
    """MRV-BLIND open challenge plots. Measurements stay sealed (hashes only)."""
    resp = (
        db.table("sci_carbon_blind_plots")
        .select("*")
        .eq("status", "open")
        .execute()
    )
    rows = _rows(resp)
    _emit_toll(db, org_id, TOLL_QUERY, request_id)
    out = [
        schemas.BlindPlotOut(
            plot_code=r["plot_code"],
            region=r["region"],
            soil_texture=r.get("soil_texture"),
            climate_zone=r.get("climate_zone"),
            management_history=r.get("management_history"),
            measurement_window_start=str(r["measurement_window_start"]),
            measurement_window_end=str(r["measurement_window_end"]),
            dry_combustion_endpoints=bool(r.get("dry_combustion_endpoints", False)),
            status=r["status"],
        ).model_dump()
        for r in rows
    ]
    return schemas.envelope(
        data=out,
        dataset_name=DATASET_NAME,
        semver="1.0.0",
        lab_count=0,
        synthetic=True,
    )


def submit_blind(
    db: Any, sub: schemas.BlindSubmissionIn, org_id: str, request_id: str
) -> schemas.BlindSubmissionOut:
    """MRV-BLIND submission with eligibility gating (Diego TW-600 spec).

    Gates:
      * training_lineage_hash must be a 64-hex SHA-256 (eligibility).
      * benchmark must exist and be open.
      * every prediction must carry a proper interval (lo < estimate < hi).
      * duplicate plot_code within one submission is rejected.
    Scoring happens after the measurement consortium reveals endpoints;
    the dry-combustion gate is enforced in models.score_mrv_blind.
    """
    if not models.SHA256_HEX.match(sub.training_lineage_hash):
        raise ValueError(
            "training_lineage_hash must be a 64-hex SHA-256 of the published "
            "training-data manifest (MRV-BLIND eligibility)"
        )
    bench = (
        db.table("sci_benchmarks")
        .select("*")
        .eq("lane", LANE)
        .eq("name", sub.benchmark_name)
        .eq("status", "open")
        .single()
        .execute()
    )
    benchmark = getattr(bench, "data", None)
    if benchmark is None:
        raise LookupError(f"no open {LANE} benchmark {sub.benchmark_name}")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for p in sub.predictions:
        if p.plot_code in seen:
            raise ValueError(f"duplicate prediction for plot {p.plot_code}")
        seen.add(p.plot_code)
        validated.append(models.validate_blind_prediction(p.model_dump()).model_dump())
    row = {
        "id": uuid.uuid4().hex,
        "benchmark_id": benchmark["id"],
        "submitter": sub.submitter,
        "training_lineage_hash": sub.training_lineage_hash,
        "holdout_declaration_hash": sub.holdout_declaration_hash,
        "predictions": validated,
        "status": "submitted",
    }
    db.table("sci_carbon_blind_submissions").insert(row).execute()
    _emit_toll(db, org_id, TOLL_BENCHMARK, request_id)
    return schemas.BlindSubmissionOut(
        id=row["id"],
        benchmark_name=sub.benchmark_name,
        submitter=sub.submitter,
        status="submitted",
        predictions_accepted=len(validated),
    )


def ingest_batch(
    db: Any, batch: schemas.IngestBatch, org_id: str, request_id: str
) -> schemas.IngestResult:
    """Batch ingest with per-record self-healing (TW-208): one bad record
    -> dead_letters, never kills the run. Unsigned payloads quarantine.

    CARBON-TRUST v1: records with missing/'unknown' analytical_method
    quarantine to provenance_incomplete — method codes are never silently
    mixed in. Records are also assigned a method_comparable_group when
    their method belongs to a documented cross-calibrated set."""
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
            if m.record_class not in models.RECORD_CLASSES:
                dead_letters.append(
                    {"index": i, "reason": f"record_class must be one of {models.RECORD_CLASSES}"}
                )
                continue
            if m.carbon_pool not in models.CARBON_POOLS:
                dead_letters.append(
                    {"index": i, "reason": f"carbon_pool must be one of {models.CARBON_POOLS}"}
                )
                continue
            # Method-code comparability gate: unknown/missing method codes
            # never enter silver silently — they quarantine.
            method = (m.analytical_method or "").strip().lower()
            if not method or method == "unknown" or method not in models.METHOD_CODES:
                quarantined.append(
                    {
                        "index": i,
                        "partition": "provenance_incomplete",
                        "reason": "missing or 'unknown' analytical_method: method code required, never silently mixed in",
                    }
                )
                continue
            group = next(
                (name for name, members in models.COMPARABLE_GROUPS.items() if method in members),
                None,
            )
            payload = m.model_dump(exclude={"collector_signature"})
            payload["conditions"] = m.conditions.model_dump()
            payload["artifact_id"] = str(m.artifact_id)
            payload["lab_id"] = str(m.lab_id)
            payload["calibration_id"] = str(m.calibration_id) if m.calibration_id else None
            payload["analytical_method"] = method
            payload["method_comparable_group"] = group
            db.table("sci_carbon_measurements").insert(payload).execute()
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
    db = _db()
    org_id = _gate(db, _org_id(x_org_id))
    return list_dataset_versions(db, org_id, _request_id(request, x_request_id))


@router.get("/datasets/{version}")
def http_get_dataset_version(
    version: str,
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
):
    db = _db()
    org_id = _gate(db, _org_id(x_org_id))
    return get_dataset_version(db, version, org_id, _request_id(request, x_request_id))


@router.get("/query")
def http_query_gold(
    request: Request,
    record_class: str | None = Query(default=None),
    carbon_pool: str | None = Query(default=None),
    analytical_method: str | None = Query(default=None),
    comparable_group: str | None = Query(default=None),
    region: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=10000),
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
):
    db = _db()
    org_id = _gate(db, _org_id(x_org_id))
    return query_gold(
        db, org_id, _request_id(request, x_request_id),
        record_class=record_class, carbon_pool=carbon_pool,
        analytical_method=analytical_method, comparable_group=comparable_group,
        region=region, limit=limit,
    )


@router.get("/benchmarks")
def http_list_benchmarks(
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
):
    db = _db()
    org_id = _gate(db, _org_id(x_org_id))
    return list_benchmarks(db, org_id, _request_id(request, x_request_id))


@router.get("/benchmarks/mrv-blind/plots")
def http_blind_plots(
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
):
    db = _db()
    org_id = _gate(db, _org_id(x_org_id))
    return list_blind_plots(db, org_id, _request_id(request, x_request_id))


@router.post("/benchmarks/mrv-blind/submit")
def http_blind_submit(
    sub: schemas.BlindSubmissionIn,
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    db = _db()
    key = require_science_key(x_api_key, db, "carbon")
    enforce_lab_binding(key, [])
    return submit_blind(db, sub, _org_id(x_org_id), _request_id(request, x_request_id))


@router.get("/datasets/{version}/download")
def http_download_dataset(
    version: str,
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Excel download: subscription + one-time curve fee (TW-606).

    Small/medium tiers only -- large datasets are API-only (403). The fee
    is the dataset's live curve price: pennies, hard-capped at $0.10.
    """
    db = _db()
    org_id = _gate(db, _org_id(x_org_id))
    request_id = _request_id(request, x_request_id)
    row = get_dataset_version(db, version, org_id, request_id).data
    row_count = row["row_count"]
    tier = paywall.dataset_tier(row_count)
    paywall.check_excel_delivery(tier, row_count)

    resp = (
        db.table("sci_carbon_gold")
        .select("*")
        .eq("dataset_version_id", row["id"])
        .limit(500_000)
        .execute()
    )
    rows = _rows(resp)
    columns = [
        "id", "record_class", "carbon_pool", "analytical_method",
        "method_comparable_group", "region", "site_id",
        "depth_top_cm", "depth_bottom_cm", "soc_g_kg", "bulk_density_g_cm3",
        "agb_mg_ha", "agb_uncertainty_mg_ha", "uncertainty_framework",
        "h_c_org_ratio", "aromatic_c_pct", "persistence_100yr_pct",
        "mrv_method_class", "baseline_credibility", "permanence_evidence",
        "synthetic",
    ]
    fee = paywall.download_fee_cents(db, org_id, row["id"])
    content = paywall.build_excel_bytes(
        rows, columns, DATASET_NAME, version, tier, fee,
        synthetic=all(r.get("synthetic", False) for r in rows) if rows else True,
    )
    paywall.emit_download_toll(db, org_id, request_id, row["id"])
    # The download itself is also a served read: meter it as a query.
    _emit_toll(db, org_id, TOLL_QUERY, f"{request_id}:download")
    return paywall.excel_response(content, DATASET_NAME, version)


@router.post("/ingest")
def http_ingest_batch(
    batch: schemas.IngestBatch,
    request: Request,
    x_request_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    db = _db()
    key = require_science_key(x_api_key, db, "carbon")
    enforce_lab_binding(key, [str(m.lab_id) for m in batch.measurements])
    return ingest_batch(db, batch, _org_id(x_org_id), _request_id(request, x_request_id))
