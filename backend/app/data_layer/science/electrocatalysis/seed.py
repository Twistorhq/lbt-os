"""TW-593 Track 2 · Electrocatalysis lane — synthetic V1 seed.

Prepared by Twistor Holdings LLC.

V1 seeds SYNTHETIC data only, synthetic=true stamped on every row. Anchored
on the breakthrough-lanes reference points (UNSW Fe-Bi-Ni-Sn-Zn green
ammonia at 25°C ~100% FE; Toronto Cu-Al CO2-to-ethylene at record 80% FE),
with deterministic jitter so the 30-row spread exercises the full schema
range — enough to exercise the full chain: ingest → provenance → version →
gold → query → toll. No real lab data until partners sign.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

_RNG = random.Random(20261018)

# ---------------------------------------------------------------------------
# Cohort anchors (synthetic variations around public reference results)
# ---------------------------------------------------------------------------

_FE_BI_NI_SN_ZN = [
    ("Fe70Bi15Ni10Sn3Zn2", "(111)"), ("Fe72Bi14Ni9Sn3Zn2", "(100)"),
    ("Fe68Bi16Ni11Sn3Zn2", "(110)"), ("Fe70Bi15Ni10Sn3Zn2", "(211)"),
    ("Fe75Bi12Ni8Sn3Zn2", "(111)"), ("Fe65Bi18Ni12Sn3Zn2", "polycrystalline"),
    ("Fe70Bi15Ni10Sn2Zn3", "(100)"), ("Fe71Bi14Ni10Sn3Zn2", "(111)"),
    ("Fe69Bi16Ni10Sn3Zn2", "(100)"), ("Fe73Bi13Ni9Sn3Zn2", "(110)"),
    ("Fe70Bi15Ni11Sn2Zn2", "(111)"), ("Fe66Bi17Ni12Sn3Zn2", "(211)"),
]

_CU_AL = [
    ("Cu85Al15", "(100)"), ("Cu88Al12", "(111)"), ("Cu82Al18", "(100)"),
    ("Cu90Al10", "(211)"), ("Cu85Al15", "(110)"), ("Cu87Al13", "polycrystalline"),
    ("Cu80Al20", "(100)"), ("Cu85Al15", "(100)"), ("Cu92Al8", "(111)"),
    ("Cu84Al16", "(110)"), ("Cu86Al14", "(100)"), ("Cu83Al17", "(211)"),
]

_PEROVSKITES = [
    ("LaNiO3", "perovskite-001"), ("La0.8Sr0.2NiO3", "perovskite-110"),
    ("Ba0.5Sr0.5Co0.8Fe0.2O3", "perovskite-001"),
    ("PrBaCo2O5+delta", "perovskite-110"),
    ("LaCoO3", "perovskite-001"), ("SrFeO3", "perovskite-110"),
]


def seed_labs() -> list[dict[str, Any]]:
    """Three synthetic generator labs — named as synthetic so nobody mistakes
    this for real lab data."""
    labs = [
        ("Twistor Synthetic Generator A", "independent"),
        ("Twistor Synthetic Generator B", "independent"),
        ("Twistor Benchmark Fixture Lab", "independent"),
    ]
    return [
        {
            "id": str(uuid4()),
            "name": name,
            "kind": kind,
            "verification_status": "verified",
            "public_key": f"ed25519:synthetic-{i}",
            "contact_hashed": "sha256:synthetic-contact-placeholder",
        }
        for i, (name, kind) in enumerate(labs)
    ]


def seed_calibrations(labs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cals: list[dict[str, Any]] = []
    for lab in labs:
        cals.append(
            {
                "id": str(uuid4()),
                "lab_id": lab["id"],
                "instrument_id": "potentiostat-01",
                "instrument_kind": "potentiostat",
                "calibrated_at": datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat(),
                "valid_until": datetime(2027, 9, 1, tzinfo=timezone.utc).isoformat(),
                "standard_ref": "NIST-traceable Ag/AgCl reference electrode",
                "calibration_payload_hash": "sha256:synthetic-calibration-certificate",
            }
        )
    return cals


def _conditions(adsorbate: str, i: int) -> dict[str, Any]:
    if adsorbate in ("*N2", "*N2H"):
        # Green ammonia lane: near-ambient conditions (the UNSW shape).
        return {
            "temp_c": round(_RNG.uniform(20, 30), 1),
            "pressure_bar": round(_RNG.uniform(1, 3), 2),
            "electrolyte": _RNG.choice(["1M KOH", "0.5M K2SO4"]),
            "ph": round(_RNG.uniform(13, 14), 2),
            "solvent": "aqueous",
            "cell_type": _RNG.choice(["H-cell", "flow-cell"]),
            "stirring_rpm": round(_RNG.uniform(300, 900), 0),
            "extra": {"reaction": "nitrogen_reduction", "product": "NH3"},
        }
    if adsorbate in ("*CO2", "*CO", "*OCCO"):
        # CO2 reduction lane: flow-cell, bicarbonate (the Toronto shape).
        return {
            "temp_c": round(_RNG.uniform(20, 28), 1),
            "pressure_bar": round(_RNG.uniform(1, 2), 2),
            "electrolyte": _RNG.choice(["0.5M KHCO3", "1M KHCO3"]),
            "ph": round(_RNG.uniform(7.5, 8.5), 2),
            "solvent": "aqueous",
            "cell_type": "flow-cell",
            "stirring_rpm": None,
            "extra": {"reaction": "co2_reduction", "product": _RNG.choice(["C2H4", "CO"])},
        }
    # Perovskite OER lane.
    return {
        "temp_c": round(_RNG.uniform(20, 30), 1),
        "pressure_bar": 1.0,
        "electrolyte": "1M KOH",
        "ph": round(_RNG.uniform(13.5, 14), 2),
        "solvent": "aqueous",
        "cell_type": "H-cell",
        "stirring_rpm": round(_RNG.uniform(400, 800), 0),
        "extra": {"reaction": "oxygen_evolution", "product": "O2"},
    }


def build_bronze_records(
    labs: list[dict[str, Any]], cals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """30 silver-ready synthetic records with bronze provenance attached."""
    cohorts: list[tuple[str, str, str, tuple[float, float], tuple[float, float], float]] = []
    for comp, facet in _FE_BI_NI_SN_ZN:
        cohorts.append((comp, facet, _RNG.choice(["*N2", "*N2H"]), (85, 100), (0.5, 3.5), -0.9))
    for comp, facet in _CU_AL:
        cohorts.append((comp, facet, _RNG.choice(["*CO2", "*CO", "*OCCO"]), (55, 80), (0.2, 1.8), -0.7))
    for comp, facet in _PEROVSKITES:
        cohorts.append((comp, facet, "*O", (40, 75), (0.05, 0.6), -1.2))

    base_time = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    records: list[dict[str, Any]] = []
    for i, (comp, facet, ads, fe_range, tof_range, dft_center) in enumerate(cohorts):
        lab = labs[i % len(labs)]
        cal = cals[i % len(cals)]
        conds = _conditions(ads, i)
        fe = round(_RNG.uniform(*fe_range), 2)
        tof = round(_RNG.uniform(*tof_range), 4)
        dft = round(_RNG.gauss(dft_center, 0.15), 3)
        collected = base_time + timedelta(hours=i * 3)
        payload = f"synthetic-electro-{i}-{comp}-{ads}-{fe}"
        content_hash = hashlib.sha256(payload.encode()).hexdigest()
        records.append(
            {
                "lab_id": lab["id"],
                "calibration_id": cal["id"],
                "content_hash": content_hash,
                "collector_signature": f"ed25519:synthetic-sig-{i}",
                "collected_at": collected.isoformat(),
                "measured_at": collected.isoformat(),
                "conditions": conds,
                "composition": comp,
                "surface_facet": facet,
                "adsorbate": ads,
                "dft_adsorption_ev": dft,
                "experimental_tof": tof,
                "faradaic_efficiency": fe,
                "synthetic": True,
            }
        )
    return records


def dataset_version(row_count: int) -> dict[str, Any]:
    root = hashlib.sha256(f"synthetic-electro-v1.0.0-{row_count}".encode()).hexdigest()
    return {
        "lane": "electrocatalysis",
        "dataset_name": "twistor-electro-screening",
        "semver": "1.0.0",
        "row_count": row_count,
        "content_root_hash": root,
        "benchmark_pin": "catbench_v1",
        "doi": "10.0000/twistor.synthetic.v1",  # placeholder — real minting is Justynn's call
        "supersedes_id": None,
        "release_notes": (
            "V1 synthetic seed: 30 rows across Fe-Bi-Ni-Sn-Zn green-ammonia alloys, "
            "Cu-Al CO2-reduction catalysts, and perovskite OER catalysts. synthetic=true "
            "on every row. Reference implementation for TW-593."
        ),
    }


def gold_rows(
    silver_ids: list[str],
    version_id: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Gold mart rows pinned to a dataset version."""
    gold: list[dict[str, Any]] = []
    for sid, rec in zip(silver_ids, records):
        snap = f"{rec['composition']}|{rec['surface_facet']}|{rec['adsorbate']}|{rec['faradaic_efficiency']}|{rec['experimental_tof']}"
        gold.append(
            {
                "silver_id": sid,
                "dataset_version_id": version_id,
                "composition": rec["composition"],
                "surface_facet": rec["surface_facet"],
                "adsorbate": rec["adsorbate"],
                "faradaic_efficiency": rec["faradaic_efficiency"],
                "experimental_tof": rec["experimental_tof"],
                "attestation_summary": {"reproduced": 0, "partial": 0, "failed": 0},
                "row_hash": hashlib.sha256(snap.encode()).hexdigest(),
                "synthetic": True,
            }
        )
    return gold


def insert_all(db: Any) -> dict[str, Any]:
    """Insert the full V1 synthetic seed through a Supabase client. Used by
    the integration pass / local seeding; tests use the pure builders above."""
    labs = seed_labs()
    cals = seed_calibrations(labs)
    records = build_bronze_records(labs, cals)
    version = dataset_version(len(records))

    for lab in labs:
        db.table("sci_labs").insert(lab).execute()
    for cal in cals:
        db.table("sci_calibrations").insert(cal).execute()

    silver_ids: list[str] = []
    for rec in records:
        rec = dict(rec)
        artifact = {
            "lane": "electrocatalysis",
            "lab_id": rec.pop("lab_id"),
            "storage_path": f"synthetic/electrocatalysis/{rec['content_hash']}.json",
            "content_hash": rec.pop("content_hash"),
            "collector_signature": rec.pop("collector_signature"),
            "collected_at": rec.pop("collected_at"),
            "schema_version": 1,
        }
        res = db.table("sci_bronze_artifacts").insert(artifact).execute()
        artifact_id = (getattr(res, "data", [{}]) or [{}])[0].get("id") or str(uuid4())
        rec["artifact_id"] = artifact_id
        sres = db.table("sci_electro_measurements").insert(rec).execute()
        silver_id = (getattr(sres, "data", [{}]) or [{}])[0].get("id") or str(uuid4())
        silver_ids.append(silver_id)

    vres = db.table("sci_dataset_versions").insert(version).execute()
    version_id = (getattr(vres, "data", [{}]) or [{}])[0].get("id") or str(uuid4())

    for gold in gold_rows(silver_ids, version_id, records):
        db.table("sci_electro_gold").insert(gold).execute()

    return {
        "labs": len(labs),
        "calibrations": len(cals),
        "artifacts": len(records),
        "silver": len(silver_ids),
        "version_id": version_id,
        "gold": len(records),
    }
