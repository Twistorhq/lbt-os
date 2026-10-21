"""PFAS lane V1 seed data (TW-593 / TW-595). Prepared by Twistor Holdings LLC.

30 realistic SYNTHETIC destruction experiments spanning 5 compounds x 6
destruction methods. Every row carries synthetic=true. Deterministic: seeded
RNG plus uuid5 ids, so the seed is byte-identical on every run.

No real lab data until partners sign. This seed exists to exercise the full
chain: ingest -> provenance -> version -> gold -> query -> toll.
"""
from __future__ import annotations

import hashlib
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone

from . import models

log = logging.getLogger(__name__)

SEED = 593
DATASET_NAME = "twistor-pfas-destruction"
DATASET_SEMVER = "1.0.0"

# First benchmark concept: method-comparison across destruction methods.
# Pins to the first released dataset version; seeded into the shared
# sci_benchmarks table by seed_all() once the core migration is applied.
PFAS_METHOD_BENCHMARK = {
    "lane": models.LANE,
    "name": "pfas_method_bench_v1",
    "metric": "destruction_pct_mae",
    "status": "open",
    "description": (
        "Blind method-comparison benchmark: predict destruction_pct for "
        "held-out PFAS destruction experiments given compound, destruction "
        "method, and conditions. Scored by mean absolute error against the "
        "withheld answers. The field's 2026 roadmap calls for exactly this "
        "dataset; V1 is the first benchmark."
    ),
}

_COMPOUNDS = ["PFOA", "PFOS", "GenX", "PFHxS", "PFNA"]

# Per-method operating envelopes drawn from the published PFAS destruction
# literature (thermal PFAS destruction is Justynn's pyrolysis-adjacent lane).
_METHOD_ENVELOPES = {
    "thermal": {
        "temp_c": (800.0, 1100.0), "residence_s": (2.0, 30.0), "pct": (85.0, 99.9),
    },
    "electrochemical": {
        "temp_c": (20.0, 60.0), "residence_s": (1800.0, 14400.0), "pct": (60.0, 95.0),
    },
    "plasma": {
        "temp_c": (25.0, 200.0), "residence_s": (60.0, 1800.0), "pct": (70.0, 99.0),
    },
    "sonochemical": {
        "temp_c": (20.0, 40.0), "residence_s": (3600.0, 21600.0), "pct": (30.0, 70.0),
    },
    "photocatalytic": {
        "temp_c": (20.0, 50.0), "residence_s": (3600.0, 28800.0), "pct": (40.0, 85.0),
    },
    "supercritical_water_oxidation": {
        "temp_c": (400.0, 600.0), "residence_s": (30.0, 300.0), "pct": (95.0, 99.99),
    },
}

_MATRICES = [
    "groundwater",
    "AFFF concentrate",
    "drinking water",
    "industrial wastewater",
    "soil leachate",
]

_BASE_TIME = datetime(2024, 3, 1, tzinfo=timezone.utc)


def _det_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "twistor-pfas-seed:" + ":".join(parts)))


def generate_measurements() -> list[dict]:
    """Build the 30-row synthetic silver seed. Deterministic."""
    rng = random.Random(SEED)
    rows: list[dict] = []
    idx = 0
    for compound in _COMPOUNDS:
        for method, env in _METHOD_ENVELOPES.items():
            idx += 1
            pct = round(rng.uniform(*env["pct"]), 2)
            measured_at = _BASE_TIME + timedelta(days=rng.randint(0, 700))
            rows.append(
                {
                    "id": _det_id("measurement", compound, method),
                    "artifact_id": _det_id("artifact", compound, method),
                    "lab_id": _det_id("lab", "twistor-synthetic-lab"),
                    "calibration_id": _det_id("calibration", method),
                    "conditions": {
                        "temp_c": round(rng.uniform(*env["temp_c"]), 1),
                        "residence_s": round(rng.uniform(*env["residence_s"]), 1),
                        "matrix": _MATRICES[(idx - 1) % len(_MATRICES)],
                    },
                    "measured_at": measured_at.isoformat(),
                    "compound": compound,
                    "destruction_method": method,
                    "destruction_pct": pct,
                    "analytical_method": (
                        "LC-HRMS" if rng.random() < 0.25 else "LC-MS/MS"
                    ),
                    "detection_limit_ppt": round(rng.uniform(0.1, 4.0), 2),
                    "synthetic": True,
                }
            )
    return rows


def build_dataset_version(row_count: int) -> dict:
    """Dataset version row for the V1 snapshot. The merkle root is computed
    over the sorted row hashes; placeholder until the real release cut."""
    measurements = generate_measurements()
    leaf_hashes = sorted(
        hashlib.sha256(
            f"{m['id']}:{m['compound']}:{m['destruction_method']}:{m['destruction_pct']}".encode()
        ).hexdigest()
        for m in measurements
    )
    root = hashlib.sha256("".join(leaf_hashes).encode()).hexdigest()
    return {
        "id": _det_id("dataset-version", DATASET_SEMVER),
        "lane": models.LANE,
        "dataset_name": DATASET_NAME,
        "semver": DATASET_SEMVER,
        "row_count": row_count,
        "content_root_hash": root,
        "benchmark_pin": PFAS_METHOD_BENCHMARK["name"],
        "doi": None,  # DOI minting is Justynn's call (TW-593 section 7.1)
        "supersedes_id": None,
        "release_notes": (
            "V1 synthetic seed: 30 destruction experiments, 5 compounds x 6 "
            "methods. Synthetic only, labeled. First benchmark: "
            "pfas_method_bench_v1."
        ),
    }


def build_gold_rows(version_id: str) -> list[dict]:
    """Gold snapshot rows for the V1 dataset version."""
    return [
        {
            "id": _det_id("gold", m["id"]),
            "measurement_id": m["id"],
            "dataset_version_id": version_id,
            "compound": m["compound"],
            "destruction_method": m["destruction_method"],
            "destruction_pct": m["destruction_pct"],
            "attestation_summary": {"reproduced": 0, "partial": 0, "failed": 0},
            "synthetic": True,
        }
        for m in generate_measurements()
    ]


def seed_all(db) -> dict:
    """Insert the V1 seed with per-record isolation (TW-208). Returns counts.

    Inserts silver measurements, the dataset version row, gold snapshot rows,
    and the first benchmark concept row. Safe to re-run: deterministic ids,
    upsert-style skips on conflict.
    """
    measurements = generate_measurements()
    valid, dead_letters = models.split_valid_records(measurements)
    inserted = 0
    for row in valid:
        try:
            db.table(models.SILVER_TABLE).upsert(row, on_conflict="id").execute()
            inserted += 1
        except Exception as exc:  # one bad row never kills the seed
            dead_letters.append({"index": -1, "errors": [str(exc)], "record": row})
            log.warning("pfas_seed_dead_letter", extra={"error": str(exc)})

    version = build_dataset_version(inserted)
    try:
        db.table(models.VERSIONS_TABLE).upsert(version, on_conflict="id").execute()
    except Exception as exc:
        log.warning("pfas_seed_version_failed", extra={"error": str(exc)})

    gold_inserted = 0
    for grow in build_gold_rows(version["id"]):
        try:
            db.table(models.GOLD_TABLE).upsert(grow, on_conflict="id").execute()
            gold_inserted += 1
        except Exception as exc:
            dead_letters.append({"index": -1, "errors": [str(exc)], "record": grow})

    benchmark = dict(PFAS_METHOD_BENCHMARK)
    benchmark["id"] = _det_id("benchmark", benchmark["name"])
    benchmark["dataset_version_id"] = version["id"]
    benchmark["hidden_split_hash"] = hashlib.sha256(
        f"pfas-method-bench-v1:{version['content_root_hash']}".encode()
    ).hexdigest()
    try:
        db.table(models.BENCHMARKS_TABLE).upsert(
            benchmark, on_conflict="lane,name"
        ).execute()
    except Exception as exc:
        log.warning("pfas_seed_benchmark_failed", extra={"error": str(exc)})

    return {
        "silver_inserted": inserted,
        "gold_inserted": gold_inserted,
        "dataset_version": f"{version['dataset_name']} {version['semver']}",
        "dead_letters": len(dead_letters),
    }
