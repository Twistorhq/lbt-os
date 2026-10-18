# Prepared by Twistor Holdings LLC
"""Carbon lane domain models (TW-599 Wave 1, CARBON-TRUST v1).

Pydantic row types for the silver/gold lane tables, the provenance-chain
walker (gold row -> silver -> bronze artifact -> lab -> calibration), the
version-immutability guard, the correction-supersession builder, and the
MRV-BLIND scoring logic.

Diego TW-600 spec: the harmonized sequestration evidence layer —
soil profiles + flux towers + biomass pixels + biochar stability annex +
MRV-method runs + registry crosswalks — with method-code comparability
gating. MRV-BLIND is the blind predict-then-measure challenge for
soil-carbon MRV methods, scored ONLY against plots with dry-combustion
measurements at both endpoints.

No supabase import at module level: the db client is typed as Any so unit
tests stay importable without the supabase package. Callers pass the client
returned by backend.app.database.get_db().
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

LANE = "carbon"

RECORD_CLASSES = (
    "soil-profile",
    "flux-tower",
    "biomass-pixel",
    "mrv-method-run",
    "literature-extracted",
    "registry-crosswalk",
)

CARBON_POOLS = (
    "soil-organic",
    "aboveground-biomass",
    "belowground-biomass",
    "biochar-stable",
    "harvested-wood",
    "peat",
)

# Analytical method vocabulary. Method codes are first-class and never
# hidden: Walkley-Black and dry-combustion records are never silently
# pooled — they carry different comparable_group assignments.
METHOD_CODES = (
    "dry-combustion",
    "walkley-black",
    "loi",
    "spectroscopy",
    "ec-tower",
    "lidar-agb",
    "model-derived",
    "registry-reported",
    "unknown",
)

# Comparable method groups. A group assignment means the methods inside it
# have documented cross-calibration and MAY be pooled; anything else is
# compared only within its own method. 'unknown' is never groupable.
COMPARABLE_GROUPS: dict[str, tuple[str, ...]] = {
    "dry-combustion-equivalent": ("dry-combustion", "spectroscopy"),
    "walkley-black-family": ("walkley-black", "loi"),
    "flux-tower-standard": ("ec-tower",),
    "lidar-biomass": ("lidar-agb",),
}

SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class DatasetImmutableError(RuntimeError):
    """Raised when code attempts to mutate an immutable dataset version or gold row."""


class SilverMeasurement(BaseModel):
    id: UUID
    artifact_id: UUID
    lab_id: UUID
    calibration_id: UUID | None = None
    conditions: dict[str, Any]
    measured_at: datetime
    record_class: str
    carbon_pool: str
    analytical_method: str
    method_comparable_group: str | None = None
    region: str
    site_id: str | None = None
    # soil-profile
    depth_top_cm: float | None = None
    depth_bottom_cm: float | None = None
    soc_g_kg: float | None = None
    bulk_density_g_cm3: float | None = None
    texture: str | None = None
    ph: float | None = None
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
    agb_mg_ha: float | None = None
    agb_uncertainty_mg_ha: float | None = None
    epoch: str | None = None
    uncertainty_framework: str | None = None
    # biochar stability annex
    feedstock: str | None = None
    pyrolysis_temp_c: float | None = None
    residence_time_min: float | None = None
    h_c_org_ratio: float | None = None
    aromatic_c_pct: float | None = None
    o_c_ratio: float | None = None
    ebc_cert_id: str | None = None
    persistence_100yr_pct: float | None = None
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
    anomaly_flag: bool = False
    synthetic: bool = False


class GoldRow(BaseModel):
    id: UUID
    dataset_version_id: UUID
    silver_id: UUID
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
    anomaly_flag: bool = False
    synthetic: bool = False
    attestation_summary: dict[str, int] = Field(default_factory=dict)
    row_hash: str


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
    calibration may be None for model/literature records.
    """
    silver = _single(db, "sci_carbon_measurements", "id", silver_id)
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
        "dataset version (supersedes_id), never mutate history (TW-599)"
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
        "corrects_silver_table": "sci_carbon_measurements",
        "corrects_silver_id": silver_id,
        "reason": reason,
        "corrected_values": corrected_values,
        "corrected_by": corrected_by_lab_id,
        "new_dataset_version_id": new_dataset_version_id,
    }


def method_comparable(method_a: str, method_b: str) -> bool:
    """Two methods may be pooled only if they share a comparable group.

    'unknown' is never comparable with anything, including itself.
    """
    if method_a == "unknown" or method_b == "unknown":
        return False
    if method_a == method_b:
        return True
    return any(
        method_a in group and method_b in group
        for group in COMPARABLE_GROUPS.values()
    )


# ------------------------------------------------------------------
# MRV-BLIND scoring (Diego TW-600 spec)
#
# Pure function over (predictions, revealed plots). Scoring is gated:
# only plots with dry_combustion_endpoints=True are scored. Score:
#   interval_score  = mean(|pred - actual| / max(half_width, eps))
#   coverage        = fraction of intervals containing the actual
#   coverage_penalty = |coverage - nominal_confidence| * 2
#   reversion_bonus  = +0.5 per correctly flagged known_reversion plot,
#                      -1.0 per false flag
#   total = interval_score + coverage_penalty - reversion_bonus
# Lower is better.
# ------------------------------------------------------------------
class BlindPlot(BaseModel):
    plot_code: str
    dry_combustion_endpoints: bool
    known_reversion: bool
    actual_soc_change_mg_ha: float | None = None  # revealed only at scoring


class BlindPrediction(BaseModel):
    plot_code: str
    soc_change_mg_ha: float
    interval_lo: float
    interval_hi: float
    nominal_confidence: float = Field(gt=0, lt=1)
    flags_additionality_failure: bool = False


class BlindScore(BaseModel):
    plots_scored: int
    interval_score: float
    coverage: float
    coverage_penalty: float
    reversion_bonus: float
    total_score: float


def score_mrv_blind(
    predictions: list[BlindPrediction],
    plots: list[BlindPlot],
    eps: float = 1e-6,
) -> BlindScore:
    """Score an MRV-BLIND submission. Only dry-combustion-anchored plots count."""
    by_code = {p.plot_code: p for p in plots}
    scored: list[tuple[BlindPrediction, BlindPlot]] = []
    for pred in predictions:
        plot = by_code.get(pred.plot_code)
        if plot is None:
            continue
        if not plot.dry_combustion_endpoints:
            continue  # scoring gate: no dry-combustion anchor, no score
        if plot.actual_soc_change_mg_ha is None:
            continue  # measurements still sealed
        scored.append((pred, plot))
    if not scored:
        raise ValueError("no scorable plots: need dry-combustion-anchored, revealed plots")

    interval_terms: list[float] = []
    in_interval = 0
    nominal_sum = 0.0
    reversion_bonus = 0.0
    for pred, plot in scored:
        half_width = max((pred.interval_hi - pred.interval_lo) / 2.0, eps)
        interval_terms.append(
            abs(pred.soc_change_mg_ha - plot.actual_soc_change_mg_ha) / half_width
        )
        if pred.interval_lo <= plot.actual_soc_change_mg_ha <= pred.interval_hi:
            in_interval += 1
        nominal_sum += pred.nominal_confidence
        if pred.flags_additionality_failure and plot.known_reversion:
            reversion_bonus += 0.5
        elif pred.flags_additionality_failure and not plot.known_reversion:
            reversion_bonus -= 1.0

    interval_score = sum(interval_terms) / len(interval_terms)
    coverage = in_interval / len(scored)
    nominal = nominal_sum / len(scored)
    coverage_penalty = abs(coverage - nominal) * 2.0
    total = interval_score + coverage_penalty - reversion_bonus
    return BlindScore(
        plots_scored=len(scored),
        interval_score=interval_score,
        coverage=coverage,
        coverage_penalty=coverage_penalty,
        reversion_bonus=reversion_bonus,
        total_score=total,
    )


def validate_blind_prediction(pred: dict[str, Any]) -> BlindPrediction:
    """Validate one MRV-BLIND prediction payload. Raises ValueError on bad input."""
    parsed = BlindPrediction(**pred)
    if not parsed.interval_lo < parsed.soc_change_mg_ha < parsed.interval_hi:
        raise ValueError(
            f"plot {parsed.plot_code}: point estimate must lie strictly inside "
            f"[interval_lo, interval_hi]"
        )
    return parsed
