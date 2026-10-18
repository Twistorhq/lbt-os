"""TW-593 Track 2 crop-climate lane: API schemas (TW-596).

Typed, documented responses — no "just figure it out". Every response
carries as_of (dataset version), provenance, and synthetic flags per
the TW-593 §4 response contract.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Split = Literal["train", "test_future"]
Season = Literal["spring", "summer", "fall", "winter"]


class MeasurementCreate(BaseModel):
    artifact_id: str
    lab_id: str
    calibration_id: str | None = Field(
        default=None,
        description="NULL for field trials: no calibrated instrument in the loop",
    )
    conditions: dict[str, Any] = Field(
        description="Lane-schema'd conditions; MUST carry trial_design"
    )
    genotype: str
    location: str
    year: int = Field(ge=1900, le=2100)
    season: Season = "summer"
    weather: dict[str, Any] = Field(default_factory=dict)
    management: dict[str, Any] = Field(default_factory=dict)
    yield_t_ha: float = Field(ge=0, le=60)
    synthetic: bool = False
    traits: dict[str, Any] = Field(
        default_factory=dict,
        description="TW-599 hemp-first: crop-specific trait panel "
        "(hemp: cannabinoid panel THC/CBD/CBG + fiber quality + lab "
        "accreditation ID). Empty for crops without a trait panel.",
    )

    @field_validator("conditions")
    @classmethod
    def _trial_design_required(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "trial_design" not in v:
            raise ValueError("conditions must carry trial_design")
        return v


class MeasurementRead(MeasurementCreate):
    id: str
    created_at: str


class GoldRowRead(BaseModel):
    id: str
    dataset_version_id: str
    silver_id: str
    split: Split
    genotype: str
    location: str
    year: int
    yield_t_ha: float | None = Field(
        description="NULL when yield_withheld: the benchmark's hidden split"
    )
    yield_withheld: bool = False
    synthetic: bool
    attestation_summary: dict[str, Any] = Field(default_factory=dict)


class DatasetVersionRead(BaseModel):
    id: str
    lane: str
    dataset_name: str
    semver: str
    row_count: int
    content_root_hash: str
    benchmark_pin: str | None
    doi: str | None
    supersedes_id: str | None
    released_at: str
    release_notes: str


class BenchmarkRead(BaseModel):
    id: str
    lane: str
    name: str
    dataset_version_id: str
    metric: str
    hidden_split_hash: str
    status: Literal["open", "closed"]
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)


class CorrectionRead(BaseModel):
    id: str
    dataset_name: str
    corrects_silver_id: str
    reason: str
    corrected_values: dict[str, Any]
    corrected_by: str
    corrected_at: str
    new_dataset_version_id: str


class CorrectionCreate(BaseModel):
    corrects_silver_id: str
    reason: str
    corrected_values: dict[str, Any]
    corrected_by: str


class IngestRequest(BaseModel):
    lab_id: str
    collector_signature: str = Field(
        description="Lab's signature over the batch content hash"
    )
    # Raw dicts, not MeasurementCreate: per-record validation happens in the
    # handler so one bad record lands in dead_letters instead of 422-ing
    # the whole batch (self-healing, TW-208).
    records: list[dict[str, Any]] = Field(max_length=1000)


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    receipt_ids: list[str] = Field(default_factory=list)
    dead_letters: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-record failures; one bad record never kills the run",
    )


class ProvenanceRead(BaseModel):
    gold: dict[str, Any]
    silver: dict[str, Any]
    bronze: dict[str, Any]
    lab: dict[str, Any]
    calibration: dict[str, Any] | None


class Envelope(BaseModel):
    """TW-593 §4 response contract: as_of + provenance + synthetic on everything."""

    as_of: dict[str, str] = Field(
        description="{dataset_name, semver, content_root_hash}"
    )
    provenance: dict[str, Any] = Field(
        description="{lab_count, attestation_summary, verification}"
    )
    synthetic: bool
    data: Any
