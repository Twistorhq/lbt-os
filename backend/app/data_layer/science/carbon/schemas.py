# Prepared by Twistor Holdings LLC
"""Carbon lane API schemas (TW-599 Wave 1, CARBON-TRUST v1).

Response contract (§4): every response carries `as_of` (dataset version +
semver), `provenance` (lab count, attestation summary), and a `synthetic`
flag where V1 seeds synthetic data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# Ingest
# ------------------------------------------------------------------
class CarbonConditions(BaseModel):
    sampling_protocol: str = Field(
        description="sampling/collection protocol id, e.g. 'wosis:sop-2024', 'ameriflux:badm-v3'"
    )
    depth_scheme: str | None = Field(
        default=None, description="soil depth scheme, e.g. '0-10-30-100cm'"
    )


class IngestMeasurement(BaseModel):
    artifact_id: UUID | None = Field(
        default=None,
        description="bronze artifact id; the lab's signed raw upload must exist first — silver never floats without bronze",
    )
    lab_id: UUID
    calibration_id: UUID | None = Field(
        default=None,
        description="NULL-able: model runs and literature extracts have no instrument chain",
    )
    conditions: CarbonConditions
    measured_at: datetime = Field(description="measurement timestamp")
    record_class: str = Field(
        description="'soil-profile' | 'flux-tower' | 'biomass-pixel' | "
        "'mrv-method-run' | 'literature-extracted' | 'registry-crosswalk'"
    )
    carbon_pool: str = Field(
        description="'soil-organic' | 'aboveground-biomass' | 'belowground-biomass' | "
        "'biochar-stable' | 'harvested-wood' | 'peat'"
    )
    analytical_method: str = Field(
        description="method code — first-class, never hidden. 'unknown' or missing "
        "-> provenance_incomplete quarantine (never silently mixed in)."
    )
    region: str
    site_id: str | None = None
    # soil-profile
    depth_top_cm: float | None = Field(default=None, ge=0)
    depth_bottom_cm: float | None = Field(default=None, gt=0)
    soc_g_kg: float | None = Field(default=None, ge=0)
    bulk_density_g_cm3: float | None = Field(default=None, gt=0)
    texture: str | None = None
    ph: float | None = Field(default=None, ge=0, le=14)
    land_use: str | None = None
    management_history: str | None = None
    # flux-tower
    tower_id: str | None = None
    tower_doi: str | None = None
    nee_g_c_m2_yr: float | None = None
    gpp_g_c_m2_yr: float | None = None
    respiration_g_c_m2_yr: float | None = None
    gap_filling_method: str | None = None
    # biomass-pixel
    biomass_source: str | None = None
    agb_mg_ha: float | None = Field(default=None, ge=0)
    agb_uncertainty_mg_ha: float | None = None
    epoch: str | None = None
    uncertainty_framework: str | None = Field(
        default=None,
        description="uncertainty framework tag, e.g. 'gedi-v2' | 'cci-v6' — "
        "the GEDI/CCI misalignment is recorded, not hidden",
    )
    # biochar stability annex
    feedstock: str | None = None
    pyrolysis_temp_c: float | None = None
    residence_time_min: float | None = None
    h_c_org_ratio: float | None = Field(default=None, ge=0)
    aromatic_c_pct: float | None = Field(default=None, ge=0, le=100)
    o_c_ratio: float | None = None
    ebc_cert_id: str | None = None
    persistence_100yr_pct: float | None = Field(default=None, ge=0, le=100)
    persistence_method: str | None = None
    # mrv-method-run
    mrv_method_class: str | None = None
    baseline_definition: str | None = None
    additionality_claim: str | None = None
    additionality_evidence_level: str | None = None
    permanence_assumption: str | None = None
    buffer_terms: str | None = None
    uncertainty_quantification: str | None = None
    class_payload: dict[str, Any] = Field(default_factory=dict)
    baseline_credibility: str | None = None
    permanence_evidence: str | None = None
    synthetic: bool = False
    collector_signature: str | None = Field(
        default=None,
        description="lab's ed25519 signature over the raw payload hash; missing -> quarantine",
    )


class IngestBatch(BaseModel):
    measurements: list[IngestMeasurement] = Field(min_length=1, max_length=5000)
    content_hash: str = Field(description="sha256 of the raw batch payload")


class IngestResult(BaseModel):
    accepted: int
    dead_letters: list[dict[str, Any]]
    quarantined: list[dict[str, Any]]


class BlindPredictionIn(BaseModel):
    plot_code: str
    soc_change_mg_ha: float
    interval_lo: float
    interval_hi: float
    nominal_confidence: float = Field(gt=0, lt=1)
    flags_additionality_failure: bool = False


class BlindSubmissionIn(BaseModel):
    benchmark_name: str = Field(default="mrv-blind_v1")
    submitter: str
    training_lineage_hash: str = Field(
        description="SHA-256 of the published training-data manifest — REQUIRED (eligibility)"
    )
    holdout_declaration_hash: str = Field(
        description="plot-and-date hashes of the submitter's training data, for overlap checks"
    )
    predictions: list[BlindPredictionIn] = Field(min_length=1, max_length=1000)


class BlindSubmissionOut(BaseModel):
    id: UUID
    benchmark_name: str
    submitter: str
    status: str
    predictions_accepted: int


# ------------------------------------------------------------------
# Read
# ------------------------------------------------------------------
class DatasetVersionOut(BaseModel):
    id: UUID
    lane: str
    dataset_name: str
    semver: str
    row_count: int
    content_root_hash: str
    benchmark_pin: str | None = None
    doi: str | None = None
    supersedes_id: UUID | None = None
    released_at: datetime
    release_notes: str


class MeasurementOut(BaseModel):
    id: UUID
    record_class: str
    carbon_pool: str
    analytical_method: str
    method_comparable_group: str | None = None
    region: str
    site_id: str | None = None
    depth_top_cm: float | None = None
    depth_bottom_cm: float | None = None
    soc_g_kg: float | None = None
    bulk_density_g_cm3: float | None = None
    agb_mg_ha: float | None = None
    agb_uncertainty_mg_ha: float | None = None
    uncertainty_framework: str | None = None
    h_c_org_ratio: float | None = None
    aromatic_c_pct: float | None = None
    persistence_100yr_pct: float | None = None
    mrv_method_class: str | None = None
    baseline_credibility: str | None = None
    permanence_evidence: str | None = None
    synthetic: bool


class BenchmarkOut(BaseModel):
    id: UUID
    name: str
    dataset_version_id: UUID
    metric: str
    status: str
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)


class BlindPlotOut(BaseModel):
    plot_code: str
    region: str
    soil_texture: str | None = None
    climate_zone: str | None = None
    management_history: str | None = None
    measurement_window_start: str
    measurement_window_end: str
    dry_combustion_endpoints: bool
    status: str


class ProvenanceSummary(BaseModel):
    lab_count: int
    attestation_summary: dict[str, int] = Field(default_factory=dict)


class ScienceEnvelope(BaseModel):
    as_of: dict[str, str]
    provenance: ProvenanceSummary
    synthetic: bool
    data: Any


def envelope(
    *,
    data: Any,
    dataset_name: str,
    semver: str,
    lab_count: int,
    synthetic: bool,
    attestation_summary: dict[str, int] | None = None,
) -> ScienceEnvelope:
    """Wrap any lane response in the §4 contract."""
    return ScienceEnvelope(
        as_of={"dataset": dataset_name, "semver": semver},
        provenance=ProvenanceSummary(
            lab_count=lab_count,
            attestation_summary=attestation_summary or {},
        ),
        synthetic=synthetic,
        data=data,
    )
