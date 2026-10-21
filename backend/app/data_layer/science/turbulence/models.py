"""TW-593 Track 2 · Turbulence lane — domain models and trust primitives.

Prepared by Twistor Holdings LLC.

These are pure-Python helpers over the rows in
sci_turbulence_measurements / sci_turbulence_gold. They encode the §3 trust
primitives (provenance proof, version pinning, correction supersession) so
the router and tests exercise the same logic.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict

LANE = "turbulence"

# ---------------------------------------------------------------------------
# Provenance chain (trust primitive §3.1)
# ---------------------------------------------------------------------------

#: Ordered link names for a full provenance walk: gold row -> silver
#: measurement -> bronze artifact (content hash + lab signature) -> lab
#: identity -> calibration event -> standard.
CHAIN_LINKS = (
    "gold",
    "silver_measurement",
    "bronze_artifact",
    "lab",
    "calibration",
)


def walk_provenance(
    *,
    gold_row: dict[str, Any],
    measurement: dict[str, Any],
    artifact: dict[str, Any],
    lab: dict[str, Any],
    calibration: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Walk the full provenance chain for one gold row.

    Returns the chain in verification order (gold -> ... -> calibration).
    Simulation runs have no instrument calibration, so the calibration link
    is optional — its absence is stated explicitly, never hidden.
    """
    chain: list[dict[str, Any]] = [
        {"link": "gold", "id": gold_row.get("id"),
         "dataset_version_id": gold_row.get("dataset_version_id")},
        {"link": "silver_measurement", "id": measurement.get("id"),
         "case_name": measurement.get("case_name"),
         "solver": measurement.get("solver"),
         "synthetic": measurement.get("synthetic")},
        {"link": "bronze_artifact", "id": artifact.get("id"),
         "content_hash": artifact.get("content_hash"),
         "collector_signature": artifact.get("collector_signature")},
        {"link": "lab", "id": lab.get("id"), "name": lab.get("name"),
         "verification_status": lab.get("verification_status")},
    ]
    if calibration is not None:
        chain.append({
            "link": "calibration",
            "id": calibration.get("id"),
            "instrument_id": calibration.get("instrument_id"),
            "standard_ref": calibration.get("standard_ref"),
        })
    else:
        chain.append({"link": "calibration", "id": None,
                      "note": "simulation run: no instrument calibration"})
    return chain


# ---------------------------------------------------------------------------
# Version pinning / immutability (trust primitive §3.2)
# ---------------------------------------------------------------------------


class DatasetVersion(BaseModel):
    """An immutable gold snapshot pointer. Frozen: new version = new object."""

    model_config = ConfigDict(frozen=True)

    id: str
    lane: str = LANE
    dataset_name: str
    semver: str
    row_count: int
    content_root_hash: str
    supersedes_id: str | None = None
    release_notes: str = ""


# ---------------------------------------------------------------------------
# Correction supersession (trust primitive §3.3)
# ---------------------------------------------------------------------------


def apply_correction(
    *,
    measurement: dict[str, Any],
    reason: str,
    corrected_values: dict[str, Any],
    corrected_by_lab_id: str,
    new_dataset_version: DatasetVersion,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a correction + the new dataset version that carries it.

    History is never mutated: the original measurement dict is returned
    UNCHANGED (verified by the caller/test), and the correction record plus
    the new version row carry the fix forward via supersession links.
    """
    correction = {
        "id": f"corr:{measurement['id']}:{new_dataset_version.semver}",
        "lane": LANE,
        "corrects_silver_table": "sci_turbulence_measurements",
        "corrects_silver_id": measurement["id"],
        "reason": reason,
        "corrected_values": deepcopy(corrected_values),
        "corrected_by": corrected_by_lab_id,
        "new_dataset_version_id": new_dataset_version.id,
    }
    version_row = {
        "id": new_dataset_version.id,
        "lane": new_dataset_version.lane,
        "dataset_name": new_dataset_version.dataset_name,
        "semver": new_dataset_version.semver,
        "row_count": new_dataset_version.row_count,
        "content_root_hash": new_dataset_version.content_root_hash,
        "supersedes_id": new_dataset_version.supersedes_id,
        "release_notes": new_dataset_version.release_notes,
    }
    return correction, version_row
