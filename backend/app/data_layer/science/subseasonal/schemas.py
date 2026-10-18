# Prepared by Twistor Holdings LLC
"""Subseasonal lane API schemas (TW-593 Track 2).

Response contract (§4): every response carries `as_of` (dataset version +
semver), `provenance` (lab count, attestation summary), and a `synthetic`
flag where V1 seeds synthetic data.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# Ingest
# ------------------------------------------------------------------
class SubseasonalConditions(BaseModel):
    bias_correction: str = Field(
        description="'pbc_v1' (learned probabilistic bias correction) or 'none'"
    )
    base_model: str = Field(
        description="dynamical model the correction post-processes, e.g. 'ecmwf_ifs_cy48r1'"
    )


class IngestMeasurement(BaseModel):
    artifact_id: UUID | None = Field(
        default=None,
        description="bronze artifact id; the lab's signed raw upload must exist first — silver never floats without bronze",
    )
    lab_id: UUID
    calibration_id: UUID | None = Field(
        default=None,
        description="NULL-able: model runs have no instrument calibration chain",
    )
    conditions: SubseasonalConditions
    measured_at: datetime = Field(description="forecast init timestamp")
    model_source: str
    # --- S2S-TRUST v1 (Diego TW-592 spec): forecast identity + truth lineage
    center: str | None = Field(
        default=None,
        description="forecasting center, e.g. 'ecmwf', 'ncep', 'cmcc'",
    )
    model_cycle: str | None = Field(
        default=None,
        description="model version/cycle, e.g. 'IFS CY49R1', 'GEFSv12'",
    )
    run_type: str | None = Field(
        default=None,
        description="'realtime' | 'reforecast' (hindcast)",
    )
    truth_source: str | None = Field(
        default=None,
        description="verification truth, e.g. 'ERA5:0.25deg:v1', 'station:WMO', '20CRv3:mean'. "
        "Missing -> provenance_incomplete quarantine (never silently mixed in).",
    )
    truth_lag_days: int | None = Field(
        default=None,
        description="truth latency in days (5 for ERA5T-class near-real-time)",
    )
    assimilation_window: str | None = Field(
        default=None,
        description="initial-condition assimilation window, e.g. '+9h/-3h' (ERA5) vs '+3h/-3h' (HRES). "
        "Declared so AI-vs-physics comparisons are fair (Zhang et al. 2026).",
    )
    reforecast_design: str | None = Field(
        default=None,
        description="'fixed' | 'on-the-fly' + hindcast period, e.g. 'on-the-fly:20y:11m'",
    )
    # ---
    lead_weeks: int = Field(ge=2, le=6)
    ensemble_size: int = Field(gt=0)
    variable: str
    region: str
    crps: float = Field(ge=0)
    skill_vs_climatology: float | None = None
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
    model_source: str
    init_date: date
    lead_weeks: int
    ensemble_size: int
    variable: str
    region: str
    crps: float
    skill_vs_climatology: float | None = None
    synthetic: bool
    bias_correction: str
    base_model: str
    # S2S-TRUST v1 truth lineage (served so every number carries its truth)
    center: str | None = None
    model_cycle: str | None = None
    run_type: str | None = None
    truth_source: str | None = None
    truth_lag_days: int | None = None
    assimilation_window: str | None = None
    reforecast_design: str | None = None


class BenchmarkOut(BaseModel):
    id: UUID
    name: str
    dataset_version_id: UUID
    metric: str
    status: str
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)


class ProvenanceSummary(BaseModel):
    lab_count: int
    attestation_summary: dict[str, int] = Field(default_factory=dict)


class ScienceEnvelope(BaseModel):
    as_of: dict[str, str]
    provenance: ProvenanceSummary
    synthetic: bool
    data: Any
    usage: dict[str, Any] | None = Field(
        default=None,
        description="TW-607 EOM usage block: records served, toll charged this "
        "call (ledger-read), running period total, rate-limit tier.",
    )


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
