"""High-Tc lane API schemas (TW-593 Track 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .models import MEASUREMENT_METHODS, STRUCTURES


class ProvenanceSummary(BaseModel):
    lab_count: int = 0
    attested_rows: int = 0
    unattested_rows: int = 0


class ResponseEnvelope(BaseModel):
    """Every lane response carries as_of / provenance / synthetic flags (§4)."""

    as_of: str | None = None  # dataset semver, e.g. "1.0.0"
    provenance: ProvenanceSummary = ProvenanceSummary()
    synthetic: bool = False
    usage: dict[str, Any] | None = Field(
        default=None,
        description="TW-607 EOM usage block: records served, toll charged this "
        "call (ledger-read), running period total, rate-limit tier.",
    )


class MeasurementIngest(BaseModel):
    artifact_id: UUID
    lab_id: UUID | None = None
    calibration_id: UUID | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    measured_at: datetime | None = None
    composition: str = Field(min_length=1)
    structure: str
    synthesis_route: str | None = None
    tc_k: float = Field(gt=0, lt=400)
    measurement_method: str
    pressure_gpa: float = Field(default=0, ge=0)
    synthetic: bool = False

    model_config = {"extra": "forbid"}

    @classmethod
    def _validate_enums(cls, values: dict) -> dict:
        if values.get("structure") not in STRUCTURES:
            raise ValueError(f"structure must be one of {STRUCTURES}")
        if values.get("measurement_method") not in MEASUREMENT_METHODS:
            raise ValueError(f"measurement_method must be one of {MEASUREMENT_METHODS}")
        # Verified-lab ingestion must name its provenance; synthetic seeds may not.
        if not values.get("synthetic") and (
            values.get("lab_id") is None or values.get("calibration_id") is None
        ):
            raise ValueError(
                "non-synthetic ingestion requires lab_id and calibration_id"
            )
        return values

    def __init__(self, **data: Any):
        data = self._validate_enums(data)
        super().__init__(**data)


class MeasurementRead(BaseModel):
    id: UUID
    artifact_id: UUID
    lab_id: UUID | None
    calibration_id: UUID | None
    conditions: dict[str, Any]
    measured_at: datetime
    composition: str
    structure: str
    synthesis_route: str | None
    tc_k: float
    measurement_method: str
    pressure_gpa: float
    synthetic: bool


class GoldRowRead(BaseModel):
    id: UUID
    silver_id: UUID
    composition: str
    structure: str
    tc_k: float
    pressure_gpa: float
    measurement_method: str
    synthetic: bool
    provenance_hash: str
    attestation_summary: dict[str, Any]


class QueryResponse(ResponseEnvelope):
    rows: list[GoldRowRead]
    row_count: int
    bulk: bool = False


class DatasetVersionRead(BaseModel):
    id: UUID
    lane: str
    dataset_name: str
    semver: str
    row_count: int
    content_root_hash: str
    benchmark_pin: str | None
    doi: str | None
    supersedes_id: UUID | None
    released_at: datetime
    release_notes: str


class DatasetsResponse(ResponseEnvelope):
    versions: list[DatasetVersionRead]


class DatasetDetailResponse(ResponseEnvelope):
    version: DatasetVersionRead
    corrections_since: list["CorrectionRead"] = Field(default_factory=list)


class CorrectionRead(BaseModel):
    id: UUID
    reason: str
    corrected_values: dict[str, Any]
    corrected_at: datetime
    new_dataset_version_id: UUID | None


class CorrectionsResponse(ResponseEnvelope):
    corrections: list[CorrectionRead]


class IngestRequest(BaseModel):
    records: list[MeasurementIngest] = Field(min_length=1, max_length=500)


class IngestResponse(ResponseEnvelope):
    accepted: int
    rejected: int
    dead_letters: list[dict[str, Any]] = Field(default_factory=list)


class BenchmarkRunSubmit(BaseModel):
    benchmark_name: str
    submitter: str = Field(min_length=1)
    score: float
    manifest_id: UUID  # reproducibility required — no manifest, no leaderboard


class BenchmarkRunRead(BaseModel):
    id: UUID
    benchmark_id: UUID
    submitter: str
    score: float
    manifest_id: UUID
    submitted_at: datetime


class BenchmarkRead(BaseModel):
    id: UUID
    name: str
    metric: str
    status: str
    leaderboard: list[BenchmarkRunRead] = Field(default_factory=list)


class BenchmarksResponse(ResponseEnvelope):
    benchmarks: list[BenchmarkRead]
