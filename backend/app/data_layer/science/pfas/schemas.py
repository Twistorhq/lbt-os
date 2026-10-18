"""PFAS lane API schemas (TW-593 / TW-595). Prepared by Twistor Holdings LLC.

Typed contracts for the science data layer router. Every served response
carries as_of (dataset version), provenance summary, and the synthetic flag.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, PositiveFloat, field_validator

Compound = Literal[
    "PFOA", "PFOS", "GenX", "PFHxS", "PFNA", "PFDA", "PFBS",
    "PFHpA", "PFOSA", "6:2 FTS", "PFBA", "PFPeA",
]
DestructionMethod = Literal[
    "thermal", "electrochemical", "plasma", "sonochemical",
    "photocatalytic", "supercritical_water_oxidation",
    "hydrothermal", "mechanochemical",
]
AnalyticalMethod = Literal[
    "LC-MS/MS", "LC-HRMS", "QTOF-MS", "IC", "GC-MS", "TOF-SIMS", "19F-NMR",
]


class Conditions(BaseModel):
    """Lane schema for the conditions JSONB column. Extra keys allowed:
    labs may record more, never less."""

    temp_c: float = Field(description="Reactor temperature in degrees C")
    residence_s: PositiveFloat = Field(description="Residence time in seconds")
    matrix: str = Field(description="Sample matrix, e.g. groundwater, AFFF concentrate")

    model_config = {"extra": "allow"}


class PFASMeasurementIn(BaseModel):
    """One destruction experiment for ingestion."""

    artifact_id: str
    lab_id: str
    calibration_id: str | None = None
    conditions: Conditions
    measured_at: datetime
    compound: Compound
    destruction_method: DestructionMethod
    destruction_pct: float = Field(ge=0, le=100)
    analytical_method: AnalyticalMethod
    detection_limit_ppt: PositiveFloat
    synthetic: bool = False

    @field_validator("compound", "destruction_method", "analytical_method", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class PFASMeasurementOut(PFASMeasurementIn):
    id: str
    superseded_by: str | None = None
    created_at: datetime


class DatasetVersionOut(BaseModel):
    id: str
    lane: str = "pfas"
    dataset_name: str
    semver: str
    row_count: int
    content_root_hash: str
    benchmark_pin: str | None = None
    doi: str | None = None
    supersedes_id: str | None = None
    released_at: datetime
    release_notes: str


class DatasetListOut(BaseModel):
    lane: str = "pfas"
    datasets: list[DatasetVersionOut]


class AttestationSummary(BaseModel):
    reproduced: int = 0
    partial: int = 0
    failed: int = 0


class ProvenanceOut(BaseModel):
    lab_count: int
    attestation: AttestationSummary
    synthetic_rows: int
    total_rows: int


class GoldRowOut(BaseModel):
    id: str
    measurement_id: str
    compound: str
    destruction_method: str
    destruction_pct: float
    attestation_summary: AttestationSummary
    synthetic: bool


class QueryResponse(BaseModel):
    as_of: str
    dataset_name: str
    semver: str
    provenance: ProvenanceOut
    rows: list[GoldRowOut]
    row_count: int
    truncated: bool = False


class BenchmarkOut(BaseModel):
    name: str
    metric: str
    status: str
    dataset_version: str | None = None
    description: str
    leaderboard: list[dict[str, Any]] = []


class BenchmarkListOut(BaseModel):
    lane: str = "pfas"
    benchmarks: list[BenchmarkOut]


class CorrectionOut(BaseModel):
    id: str
    corrects_silver_id: str
    reason: str
    corrected_values: dict[str, Any]
    new_dataset_version_id: str | None = None
    corrected_at: datetime


class CorrectionListOut(BaseModel):
    dataset_name: str
    semver: str
    corrections: list[CorrectionOut]


class IngestRequest(BaseModel):
    records: list[PFASMeasurementIn] = Field(min_length=1, max_length=1000)
    collector_signature: str | None = Field(
        default=None,
        description="Lab ed25519 signature over the batch content hash",
    )


class IngestResponse(BaseModel):
    accepted: int
    dead_letters: int
    dead_letter_details: list[dict[str, Any]] = []
    dataset_version_pending: bool = True
