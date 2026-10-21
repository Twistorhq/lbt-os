"""PFAS lane domain models and pure data-logic (TW-593 / TW-595).

Prepared by Twistor Holdings LLC.

Holds the lane vocabularies, table names, and the trust primitives as pure
functions so they are unit-testable without a live database:

- validate_conditions: lane schema for the conditions JSONB column.
- walk_provenance: gold row -> silver -> bronze artifact -> lab -> calibration.
- split_valid_records: per-record ingestion isolation (TW-208 self-healing).
- apply_correction: supersession, never mutation.
- guard_version_immutable: dataset versions are append-only.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

log = logging.getLogger(__name__)

LANE = "pfas"

SILVER_TABLE = "sci_pfas_measurements"
GOLD_TABLE = "sci_pfas_gold"

# Shared core tables (owned by the electrocatalysis lane's core migration).
BRONZE_TABLE = "sci_bronze_artifacts"
LABS_TABLE = "sci_labs"
CALIBRATIONS_TABLE = "sci_calibrations"
VERSIONS_TABLE = "sci_dataset_versions"
CORRECTIONS_TABLE = "sci_corrections"
BENCHMARKS_TABLE = "sci_benchmarks"

COMPOUNDS = (
    "PFOA", "PFOS", "GenX", "PFHxS", "PFNA", "PFDA", "PFBS",
    "PFHpA", "PFOSA", "6:2 FTS", "PFBA", "PFPeA",
)

DESTRUCTION_METHODS = (
    "thermal",
    "electrochemical",
    "plasma",
    "sonochemical",
    "photocatalytic",
    "supercritical_water_oxidation",
    "hydrothermal",
    "mechanochemical",
)

ANALYTICAL_METHODS = (
    "LC-MS/MS", "LC-HRMS", "QTOF-MS", "IC", "GC-MS", "TOF-SIMS", "19F-NMR",
)

# Lane schema for conditions JSONB: every measurement must state these.
REQUIRED_CONDITION_KEYS = ("temp_c", "residence_s", "matrix")


class ImmutableVersionError(Exception):
    """Raised when code attempts to mutate a released dataset version."""


class ProvenanceBrokenError(Exception):
    """Raised when a provenance chain link is missing."""


def validate_conditions(conditions: Any) -> list[str]:
    """Return a list of schema violations for a conditions payload."""
    errors: list[str] = []
    if not isinstance(conditions, dict):
        return ["conditions must be a JSON object"]
    for key in REQUIRED_CONDITION_KEYS:
        if key not in conditions:
            errors.append(f"conditions missing required key: {key}")
    temp = conditions.get("temp_c")
    if temp is not None and not isinstance(temp, (int, float)):
        errors.append("conditions.temp_c must be numeric (degrees C)")
    residence = conditions.get("residence_s")
    if residence is not None and (
        not isinstance(residence, (int, float)) or residence <= 0
    ):
        errors.append("conditions.residence_s must be a positive number (seconds)")
    matrix = conditions.get("matrix")
    if matrix is not None and not isinstance(matrix, str):
        errors.append("conditions.matrix must be a string")
    return errors


def validate_measurement_row(row: dict[str, Any]) -> list[str]:
    """Full lane validation for one silver measurement row. Empty = valid."""
    errors = validate_conditions(row.get("conditions"))
    if row.get("compound") not in COMPOUNDS:
        errors.append(f"compound must be one of {', '.join(COMPOUNDS)}")
    if row.get("destruction_method") not in DESTRUCTION_METHODS:
        errors.append(f"destruction_method must be one of {', '.join(DESTRUCTION_METHODS)}")
    pct = row.get("destruction_pct")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or pct < 0 or pct > 100:
        errors.append("destruction_pct must be numeric in [0, 100]")
    if row.get("analytical_method") not in ANALYTICAL_METHODS:
        errors.append(f"analytical_method must be one of {', '.join(ANALYTICAL_METHODS)}")
    dl = row.get("detection_limit_ppt")
    if not isinstance(dl, (int, float)) or isinstance(dl, bool) or dl <= 0:
        errors.append("detection_limit_ppt must be a positive number")
    if not row.get("artifact_id"):
        errors.append("artifact_id is required (bronze provenance link)")
    if not row.get("lab_id"):
        errors.append("lab_id is required (lab identity link)")
    return errors


def split_valid_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-record ingestion isolation (TW-208): one bad record never kills
    the batch. Returns (valid, dead_letters); each dead letter carries the
    record index and its validation errors."""
    valid: list[dict[str, Any]] = []
    dead_letters: list[dict[str, Any]] = []
    for idx, record in enumerate(records):
        errors = validate_measurement_row(record)
        if errors:
            dead_letters.append({"index": idx, "errors": errors, "record": record})
            log.warning("pfas_ingest_dead_letter", extra={"index": idx, "errors": errors})
        else:
            valid.append(record)
    return valid, dead_letters


def walk_provenance(
    gold_row: dict[str, Any],
    silver_by_id: dict[str, dict[str, Any]],
    bronze_by_id: dict[str, dict[str, Any]],
    labs_by_id: dict[str, dict[str, Any]],
    calibrations_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Walk the full provenance chain for one gold row.

    gold -> silver measurement -> bronze artifact (content hash + lab
    signature) -> lab identity -> calibration event -> calibration standard.
    Raises ProvenanceBrokenError on any missing link: no chain, no trust.
    """
    silver = silver_by_id.get(str(gold_row.get("measurement_id")))
    if silver is None:
        raise ProvenanceBrokenError(
            f"gold row {gold_row.get('id')} points at missing silver measurement"
        )
    bronze = bronze_by_id.get(str(silver.get("artifact_id")))
    if bronze is None:
        raise ProvenanceBrokenError(
            f"silver row {silver.get('id')} points at missing bronze artifact"
        )
    lab = labs_by_id.get(str(bronze.get("lab_id")))
    if lab is None:
        raise ProvenanceBrokenError(
            f"bronze artifact {bronze.get('id')} points at missing lab"
        )
    calibration = None
    if silver.get("calibration_id"):
        calibration = calibrations_by_id.get(str(silver.get("calibration_id")))
        if calibration is None:
            raise ProvenanceBrokenError(
                f"silver row {silver.get('id')} points at missing calibration"
            )
    return {
        "gold_id": gold_row.get("id"),
        "dataset_version_id": gold_row.get("dataset_version_id"),
        "measurement": {
            "id": silver.get("id"),
            "compound": silver.get("compound"),
            "destruction_method": silver.get("destruction_method"),
            "destruction_pct": silver.get("destruction_pct"),
            "measured_at": silver.get("measured_at"),
            "synthetic": silver.get("synthetic", False),
        },
        "bronze_artifact": {
            "id": bronze.get("id"),
            "content_hash": bronze.get("content_hash"),
            "collector_signature": bronze.get("collector_signature"),
            "collected_at": bronze.get("collected_at"),
        },
        "lab": {
            "id": lab.get("id"),
            "name": lab.get("name"),
            "kind": lab.get("kind"),
            "verification_status": lab.get("verification_status"),
        },
        "calibration": (
            {
                "id": calibration.get("id"),
                "instrument_kind": calibration.get("instrument_kind"),
                "calibrated_at": calibration.get("calibrated_at"),
                "standard_ref": calibration.get("standard_ref"),
            }
            if calibration
            else None
        ),
    }


def guard_version_immutable(version_row: dict[str, Any], attempted: str = "update") -> None:
    """Dataset versions are append-only: a released version is never mutated.

    Raises ImmutableVersionError describing the blocked operation. Corrections
    cut a NEW version row linked via supersedes_id.
    """
    raise ImmutableVersionError(
        f"sci_dataset_versions row {version_row.get('id')} "
        f"({version_row.get('dataset_name')} {version_row.get('semver')}) is "
        f"immutable: {attempted} blocked. Cut a new version with supersedes_id."
    )


def apply_correction(
    silver_row: dict[str, Any],
    corrected_values: dict[str, Any],
    new_dataset_version_id: str,
    corrected_by_lab_id: str,
    reason: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply an erratum by supersession, never mutation (TW-593 section 3.3).

    Returns (superseded_marker, corrected_row, correction_record):
    - superseded_marker: the original row with superseded_by set to the new
      row's id. The original row stays queryable: history is intact.
    - corrected_row: a NEW silver row carrying the corrected values.
    - correction_record: the sci_corrections payload linking old, new, reason,
      corrector, and the dataset version that carries the correction.
    """
    new_id = str(uuid.uuid4())
    superseded_marker = dict(silver_row)
    superseded_marker["superseded_by"] = new_id

    corrected_row = dict(silver_row)
    corrected_row["id"] = new_id
    corrected_row["superseded_by"] = None
    corrected_row.update(corrected_values)
    errors = validate_measurement_row(corrected_row)
    if errors:
        raise ValueError(f"corrected values fail lane validation: {errors}")

    correction_record = {
        "id": str(uuid.uuid4()),
        "lane": LANE,
        "corrects_silver_table": SILVER_TABLE,
        "corrects_silver_id": silver_row.get("id"),
        "reason": reason,
        "corrected_values": corrected_values,
        "corrected_by": corrected_by_lab_id,
        "new_dataset_version_id": new_dataset_version_id,
    }
    return superseded_marker, corrected_row, correction_record


def attestation_summary(counts: dict[str, int]) -> dict[str, int]:
    """Normalize an attestation summary payload for gold rows."""
    return {
        "reproduced": int(counts.get("reproduced", 0)),
        "partial": int(counts.get("partial", 0)),
        "failed": int(counts.get("failed", 0)),
    }
