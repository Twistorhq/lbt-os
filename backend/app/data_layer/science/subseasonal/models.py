# Prepared by Twistor Holdings LLC
"""Subseasonal lane domain models (TW-593 Track 2).

Pydantic row types for the silver/gold lane tables, the provenance-chain
walker (gold row -> silver -> bronze artifact -> lab -> calibration), the
version-immutability guard, and the correction-supersession builder.

No supabase import at module level: the db client is typed as Any so unit
tests stay importable without the supabase package. Callers pass the client
returned by backend.app.database.get_db().
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

LANE = "subseasonal"


class DatasetImmutableError(RuntimeError):
    """Raised when code attempts to mutate an immutable dataset version or gold row."""


class SilverMeasurement(BaseModel):
    id: UUID
    artifact_id: UUID
    lab_id: UUID
    calibration_id: UUID | None = None
    conditions: dict[str, Any]
    measured_at: datetime
    init_date: date
    model_source: str
    # S2S-TRUST v1: forecast identity + truth lineage (Diego TW-592 spec)
    center: str | None = None
    model_cycle: str | None = None
    run_type: str | None = None
    truth_source: str | None = None
    truth_lag_days: int | None = None
    assimilation_window: str | None = None
    reforecast_design: str | None = None
    lead_weeks: int = Field(ge=2, le=6)
    ensemble_size: int = Field(gt=0)
    variable: str
    region: str
    crps: float = Field(ge=0)
    skill_vs_climatology: float | None = None
    synthetic: bool = False


class GoldRow(BaseModel):
    id: UUID
    dataset_version_id: UUID
    silver_id: UUID
    model_source: str
    init_date: date
    lead_weeks: int
    ensemble_size: int
    variable: str
    region: str
    crps: float
    skill_vs_climatology: float | None = None
    synthetic: bool = False
    attestation_summary: dict[str, int] = Field(default_factory=dict)
    row_hash: str
    # S2S-TRUST v1 truth lineage, denormalized into gold for serving
    center: str | None = None
    model_cycle: str | None = None
    run_type: str | None = None
    truth_source: str | None = None
    truth_lag_days: int | None = None
    assimilation_window: str | None = None
    reforecast_design: str | None = None


class ProvenanceLink(BaseModel):
    silver: dict[str, Any]
    bronze_artifact: dict[str, Any]
    lab: dict[str, Any]
    calibration: dict[str, Any] | None = None


def _single(db: Any, table: str, column: str, value: Any) -> dict[str, Any]:
    """Fetch one row by equality. Matches the supabase-py call pattern."""
    resp = db.table(table).select("*").eq(column, value).single().execute()
    data = getattr(resp, "data", None)
    if data is None:
        raise LookupError(f"no row in {table} where {column}={value}")
    return data


def walk_provenance(db: Any, silver_id: str) -> ProvenanceLink:
    """Walk the full trust chain: silver -> bronze artifact -> lab -> calibration.

    One query per hop, no N+1 (exactly one row fetched per table).
    calibration may be None for model runs (no instrument chain).
    """
    silver = _single(db, "sci_subseasonal_measurements", "id", silver_id)
    artifact = _single(db, "sci_bronze_artifacts", "id", silver["artifact_id"])
    lab = _single(db, "sci_labs", "id", silver["lab_id"])
    calibration = None
    if silver.get("calibration_id"):
        calibration = _single(db, "sci_calibrations", "id", silver["calibration_id"])
    return ProvenanceLink(
        silver=silver,
        bronze_artifact=artifact,
        lab=lab,
        calibration=calibration,
    )


def guard_dataset_version_mutation() -> None:
    """Dataset versions and gold rows are immutable. Corrections supersede,
    never mutate. Call before any UPDATE/DELETE path on versioned data."""
    raise DatasetImmutableError(
        "dataset versions and gold rows are immutable: corrections create a new "
        "dataset version (supersedes_id), never mutate history (TW-593)"
    )


def build_correction(
    silver_id: str,
    corrected_values: dict[str, Any],
    new_dataset_version_id: str,
    reason: str,
    corrected_by_lab_id: str | None = None,
) -> dict[str, Any]:
    """Build a sci_corrections row dict. The original silver row is never
    touched; the correction rides the new dataset version."""
    if not corrected_values:
        raise ValueError("corrected_values must be non-empty")
    return {
        "lane": LANE,
        "corrects_silver_table": "sci_subseasonal_measurements",
        "corrects_silver_id": silver_id,
        "reason": reason,
        "corrected_values": corrected_values,
        "corrected_by": corrected_by_lab_id,
        "new_dataset_version_id": new_dataset_version_id,
    }
