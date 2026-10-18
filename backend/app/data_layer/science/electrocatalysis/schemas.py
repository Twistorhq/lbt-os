"""TW-593 Track 2 · Electrocatalysis lane — Pydantic request/response schemas.

Prepared by Twistor Holdings LLC.

Every served response carries the §4 response contract: ``as_of`` (dataset
version + semver), ``provenance`` (lab count + attestation summary), and the
``synthetic`` flag — V1 seeds SYNTHETIC data only, labeled as such.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Conditions — first-class data, not metadata (the SLAC round-robin lesson)
# ---------------------------------------------------------------------------

class ElectroConditions(BaseModel):
    """Lane-schema'd measurement conditions. All six required keys are
    enforced NOT NULL at the DB level too."""

    temp_c: float = Field(..., description="Cell temperature, °C")
    pressure_bar: float = Field(..., description="Operating pressure, bar")
    electrolyte: str = Field(..., description="e.g. '1M KOH', '0.5M KHCO3'")
    ph: float = Field(..., description="Electrolyte pH")
    solvent: str = Field(..., description="e.g. 'aqueous', 'acetonitrile'")
    cell_type: str = Field(..., description="e.g. 'H-cell', 'flow-cell', 'MEA'")
    stirring_rpm: float | None = Field(
        None, description="Agitation rate — the round-robin ghost variable"
    )
    extra: dict[str, Any] | None = Field(None, description="Lane extras beyond the core six")


# ---------------------------------------------------------------------------
# Measurements (silver)
# ---------------------------------------------------------------------------

class ElectroMeasurementIn(BaseModel):
    artifact_id: UUID
    lab_id: UUID
    calibration_id: UUID | None = None
    conditions: ElectroConditions
    measured_at: datetime | None = None
    composition: str | None = None
    surface_facet: str | None = None
    adsorbate: str | None = None
    dft_adsorption_ev: float | None = None
    experimental_tof: float | None = None
    faradaic_efficiency: float | None = Field(None, ge=0, le=100)
    synthetic: bool = True

    @field_validator("composition")
    @classmethod
    def _composition_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("composition must not be blank")
        return v


class ElectroMeasurementOut(ElectroMeasurementIn):
    id: UUID
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Gold query
# ---------------------------------------------------------------------------

class ElectroQueryParams(BaseModel):
    composition: str | None = None
    surface_facet: str | None = None
    adsorbate: str | None = None
    min_faradaic_efficiency: float | None = Field(None, ge=0, le=100)
    min_tof: float | None = Field(None, ge=0)
    include_synthetic: bool = True
    limit: int = Field(100, ge=1, le=5000)


class ProvenanceSummary(BaseModel):
    lab_count: int
    attestation_summary: dict[str, int]  # {"reproduced": n, "partial": n, "failed": n}
    calibration_count: int


class GoldRowOut(BaseModel):
    id: UUID
    composition: str | None
    surface_facet: str | None
    adsorbate: str | None
    faradaic_efficiency: float | None
    experimental_tof: float | None
    attestation_summary: dict[str, int]
    row_hash: str
    synthetic: bool


class QueryResponse(BaseModel):
    as_of: dict[str, Any]  # {"dataset_version_id", "semver", "dataset_name", "released_at"}
    provenance: ProvenanceSummary
    synthetic: bool  # True when every returned row is synthetic
    rows: list[GoldRowOut]


# ---------------------------------------------------------------------------
# Dataset versions
# ---------------------------------------------------------------------------

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
    released_at: datetime | None = None
    release_notes: str


# ---------------------------------------------------------------------------
# Ingest (signed lab batches)
# ---------------------------------------------------------------------------

class IngestRecordIn(BaseModel):
    """One raw lab record inside a signed batch. Per-record fault isolation
    (TW-208): one bad record → dead_letters, never kills the run."""

    lab_id: UUID
    calibration_id: UUID | None = None
    content_hash: str = Field(..., description="sha256 of the raw payload")
    collector_signature: str | None = Field(
        None, description="lab's ed25519 signature over content_hash"
    )
    collected_at: datetime
    conditions: ElectroConditions
    measured_at: datetime | None = None
    composition: str | None = None
    surface_facet: str | None = None
    adsorbate: str | None = None
    dft_adsorption_ev: float | None = None
    experimental_tof: float | None = None
    faradaic_efficiency: float | None = Field(None, ge=0, le=100)


class IngestBatchIn(BaseModel):
    lane: Literal["electrocatalysis"] = "electrocatalysis"
    storage_path_prefix: str
    schema_version: int
    records: list[IngestRecordIn] = Field(..., min_length=1, max_length=5000)


class IngestResultOut(BaseModel):
    accepted: int
    quarantined: int  # unsigned lab payloads — never promoted
    dead_letters: int  # per-record failures (TW-208 self-healing)
    batch_id: str
    receipt_hash: str  # lab-visible signed-ingestion receipt


# ---------------------------------------------------------------------------
# Corrections / attestations / benchmarks
# ---------------------------------------------------------------------------

class CorrectionIn(BaseModel):
    corrects_silver_id: UUID
    reason: str = Field(..., min_length=1)
    corrected_values: dict[str, Any] = Field(..., min_length=1)
    corrected_by: UUID  # lab id issuing the correction


class CorrectionOut(CorrectionIn):
    id: UUID
    lane: str = "electrocatalysis"
    corrects_silver_table: str = "sci_electro_measurements"
    corrected_at: datetime | None = None
    new_dataset_version_id: UUID | None = None


class BenchmarkOut(BaseModel):
    id: UUID
    name: str
    dataset_version_id: UUID
    metric: str
    status: str


class BenchmarkRunIn(BaseModel):
    benchmark_id: UUID
    submitter: str = Field(..., min_length=1)
    score: float
    manifest_id: UUID  # reproducibility required — no manifest, no leaderboard


class BenchmarkRunOut(BenchmarkRunIn):
    id: UUID
    submitted_at: datetime | None = None
