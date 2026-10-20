"""TW-593 Track 2 · Turbulence lane — API schemas.

Prepared by Twistor Holdings LLC.

Every response carries as_of (dataset version + semver), provenance (lab
count, attestation summary), and synthetic flags per the §4 contract.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

LANE = "turbulence"

ALLOWED_SOLVERS = ("DNS", "LES", "RANS", "URANS", "DES")
ALLOWED_CASES = ("channel_flow", "boundary_layer", "jet")


class Conditions(BaseModel):
    """Lane-schema'd measurement conditions. domain + bc are data, not metadata."""

    domain: str = Field(..., description="e.g. '2pi x 2 x pi channel'")
    bc: str = Field(..., description="e.g. 'periodic streamwise/spanwise, no-slip walls'")
    extra: dict[str, Any] = Field(default_factory=dict)


class FidelityMetrics(BaseModel):
    l2_error_mean_velocity: float | None = None
    reynolds_stress_mae: float | None = None
    spectra_agreement: float | None = None
    stability_hours: float | None = None

    @field_validator("spectra_agreement")
    @classmethod
    def _agreement_in_unit_interval(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 1.0:
            raise ValueError("spectra_agreement must be in [0, 1]")
        return v


class MeasurementIngest(BaseModel):
    lab_id: str
    calibration_id: str | None = None
    content_hash: str = Field(..., description="sha256 of the raw payload")
    collector_signature: str | None = Field(
        None, description="lab's ed25519 signature over content_hash"
    )
    collected_at: str | None = None
    conditions: Conditions
    measured_at: str
    case_name: str
    reynolds: float | None = Field(default=None, gt=0)
    grid_resolution: str | None = None
    solver: str | None = None
    scheme: str | None = None
    closure_model: str | None = None
    fidelity_metrics: FidelityMetrics | None = None
    synthetic: bool = True

    @field_validator("solver")
    @classmethod
    def _solver_allowed(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_SOLVERS:
            raise ValueError(f"solver must be one of {ALLOWED_SOLVERS}")
        return v

    @field_validator("case_name")
    @classmethod
    def _case_allowed(cls, v: str) -> str:
        if v not in ALLOWED_CASES:
            raise ValueError(f"case_name must be one of {ALLOWED_CASES}")
        return v

    @field_validator("closure_model")
    @classmethod
    def _dns_has_no_closure(cls, v: str | None, info) -> str | None:
        solver = (info.data or {}).get("solver")
        if solver == "DNS" and v not in (None, "none", "resolved"):
            raise ValueError("DNS resolves all scales: closure_model must be unset/'none'")
        return v


class IngestBatch(BaseModel):
    records: list[MeasurementIngest] = Field(..., min_length=1, max_length=1000)
    storage_path_prefix: str = "turbulence"
    schema_version: int = 1


class QueryFilters(BaseModel):
    case_name: str | None = None
    reynolds_min: float | None = Field(default=None, gt=0)
    reynolds_max: float | None = Field(default=None, gt=0)
    closure_model: str | None = None
    solver: str | None = None
    synthetic: bool | None = None
    limit: int = Field(default=50, ge=1, le=1000)


class ProvenanceSummary(BaseModel):
    lab_count: int
    attestation_summary: dict[str, int] = Field(default_factory=dict)
    synthetic: bool


class QueryResponse(BaseModel):
    as_of: dict[str, str]
    provenance: ProvenanceSummary
    rows: list[dict[str, Any]]
    row_count: int
    dead_letters: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] | None = Field(
        default=None,
        description="TW-607 EOM usage block: records served, toll charged this "
        "call (ledger-read), running period total, rate-limit tier.",
    )


class DatasetVersionOut(BaseModel):
    id: str
    dataset_name: str
    semver: str
    row_count: int
    content_root_hash: str
    benchmark_pin: str | None = None
    doi: str | None = None
    supersedes_id: str | None = None
    released_at: str | None = None
    release_notes: str = ""
    usage: dict[str, Any] | None = Field(
        default=None,
        description="TW-607 EOM usage block: records served, toll charged this "
        "call (ledger-read), running period total, rate-limit tier.",
    )


class BenchmarkOut(BaseModel):
    id: str
    name: str
    metric: str
    status: str
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)


class IngestResponse(BaseModel):
    accepted: int
    quarantined: int = 0  # unsigned lab payloads — never promoted
    dead_letters: list[dict[str, Any]]
    batch_id: str
    usage: dict[str, Any] | None = Field(
        default=None,
        description="TW-607 EOM usage block: records served, toll charged this "
        "call (ledger-read), running period total, rate-limit tier.",
    )
