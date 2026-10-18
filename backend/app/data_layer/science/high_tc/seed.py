"""High-Tc lane synthetic seed (TW-593 Track 2).

30 realistic SYNTHETIC superconductor records spanning the schema:
cuprates, iron pnictides/chalcogenides, high-pressure hydrides, nickelates,
a perovskite, and conventional anchors. Every record is stamped
synthetic=True with filled conditions — honest placeholders until partner
labs sign on.

Usage: ``seed_to_supabase(db)`` inserts the rows into
sci_hightc_measurements. Requires the shared core tables
(sci_bronze_artifacts, sci_labs) from the electrocatalysis lane's core
migration plus a registered "twistor-synthetic" lab identity — Dre wires
that in the integration pass. ``synthetic_records()`` alone needs nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

# Deterministic artifact ids so re-seeds are idempotent-friendly.
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "twistor.co")


def _artifact(tag: str) -> str:
    return str(uuid.uuid5(_NS, f"high_tc/synthetic/{tag}"))


def synthetic_records() -> list[dict]:
    """Return 30 synthetic high-Tc measurement dicts (insert-ready)."""
    now = datetime.now(timezone.utc).isoformat()
    recs: list[dict] = []

    def add(tag, composition, structure, route, tc, method, pressure=0.0, **cond):
        recs.append(
            {
                "id": str(uuid.uuid5(_NS, f"high_tc/synthetic/silver/{tag}")),
                "artifact_id": _artifact(tag),
                "lab_id": None,  # wired by Dre: twistor-synthetic lab identity
                "calibration_id": None,
                "conditions": {
                    "temp_c": 22.0,
                    "field_t": 0.0,
                    "sample_form": "pellet",
                    **cond,
                },
                "measured_at": now,
                "composition": composition,
                "structure": structure,
                "synthesis_route": route,
                "tc_k": tc,
                "measurement_method": method,
                "pressure_gpa": pressure,
                "synthetic": True,
            }
        )

    # --- Cuprates (ambient pressure workhorses) ---
    add("ybco-123", "YBa2Cu3O7-delta", "cuprate", "solid-state reaction", 92.0,
        "resistivity", sample_form="pellet", oxygen_anneal_c=450)
    add("bi-2212", "Bi2Sr2CaCu2O8+delta", "cuprate", "flux growth (single crystal)",
        95.0, "susceptibility", sample_form="single_crystal")
    add("hg-1223", "HgBa2Ca2Cu3O8+delta", "cuprate", "high-pressure synthesis",
        133.0, "resistivity", sample_form="pellet")
    add("tl-2223", "Tl2Ba2Ca2Cu3O10", "cuprate", "solid-state reaction", 125.0,
        "susceptibility", sample_form="pellet")
    add("lsco-015", "La1.85Sr0.15CuO4", "cuprate", "solid-state reaction", 38.0,
        "resistivity", sample_form="pellet")
    add("ybco-60k", "YBa2Cu3O6.5", "cuprate", "solid-state reaction", 60.0,
        "susceptibility", sample_form="pellet", doping="underdoped")
    add("ncco", "Nd1.85Ce0.15CuO4", "cuprate", "solid-state reaction", 24.0,
        "resistivity", sample_form="pellet", doping="electron-doped")
    add("hg-1201", "HgBa2CuO4+delta", "cuprate", "high-pressure synthesis", 97.0,
        "susceptibility", sample_form="single_crystal")
    add("bi-2223", "Bi2Sr2Ca2Cu3O10", "cuprate", "powder-in-tube", 110.0,
        "resistivity", sample_form="tape")
    add("cu-c-1234", "(Cu,C)Ba2Ca3Cu4O11", "cuprate", "high-pressure synthesis",
        117.0, "susceptibility", sample_form="pellet")
    add("ybco-film", "YBa2Cu3O7", "cuprate", "pulsed laser deposition", 90.0,
        "resistivity", sample_form="thin_film", substrate="SrTiO3")

    # --- Iron pnictides / chalcogenides ---
    add("la-1111", "LaFeAsO0.89F0.11", "iron_pnictide", "solid-state reaction",
        26.0, "resistivity", sample_form="pellet")
    add("sm-1111", "SmFeAsO0.9F0.1", "iron_pnictide", "solid-state reaction",
        55.0, "susceptibility", sample_form="pellet")
    add("ba122-k", "Ba0.6K0.4Fe2As2", "iron_pnictide", "solid-state reaction",
        38.0, "specific_heat", sample_form="single_crystal")
    add("ba122-p", "BaFe2(As0.7P0.3)2", "iron_pnictide", "self-flux growth",
        30.0, "resistivity", sample_form="single_crystal")
    add("fese", "FeSe", "iron_chalcogenide", "chemical vapor transport", 8.0,
        "susceptibility", sample_form="single_crystal")
    add("fese-te", "FeSe0.5Te0.5", "iron_chalcogenide", "Bridgman growth", 14.0,
        "resistivity", sample_form="single_crystal")
    add("kfs-122", "K0.8Fe1.6Se2", "iron_chalcogenide", "self-flux growth", 32.0,
        "susceptibility", sample_form="single_crystal")

    # --- Hydrides (diamond-anvil pressure) ---
    add("h3s", "H3S", "hydride", "diamond-anvil laser heating", 203.0,
        "resistivity", 150.0, sample_form="dac_gasket", pressure_medium="H2")
    add("lah10", "LaH10", "hydride", "diamond-anvil laser heating", 250.0,
        "resistivity", 170.0, sample_form="dac_gasket", pressure_medium="H2")
    add("yh9", "YH9", "hydride", "diamond-anvil laser heating", 243.0,
        "susceptibility", 200.0, sample_form="dac_gasket")
    add("cah6", "CaH6", "hydride", "diamond-anvil laser heating", 215.0,
        "resistivity", 172.0, sample_form="dac_gasket")
    add("ceh9", "CeH9", "hydride", "diamond-anvil laser heating", 110.0,
        "resistivity", 120.0, sample_form="dac_gasket")
    add("thh10", "ThH10", "hydride", "diamond-anvil laser heating", 161.0,
        "susceptibility", 174.0, sample_form="dac_gasket")
    add("lad10", "LaD10", "hydride", "diamond-anvil laser heating", 180.0,
        "resistivity", 150.0, sample_form="dac_gasket", isotope="D",
        note="isotope-effect counterpart to LaH10")

    # --- Nickelates (pressure) ---
    add("la327", "La3Ni2O7", "nickelate", "floating-zone + diamond anvil", 80.0,
        "resistivity", 20.0, sample_form="single_crystal")
    add("la4310", "La4Ni3O10", "nickelate", "floating-zone + diamond anvil",
        30.0, "susceptibility", 30.0, sample_form="single_crystal")

    # --- Perovskite + conventional anchors ---
    add("bkbo", "Ba0.6K0.4BiO3", "perovskite", "solid-state reaction", 30.0,
        "susceptibility", sample_form="pellet")
    add("mgb2", "MgB2", "mgb2", "solid-state reaction", 39.0, "susceptibility",
        sample_form="wire")
    add("nb3sn", "Nb3Sn", "conventional", "bronze process", 18.0, "resistivity",
        sample_form="wire")

    assert len(recs) == 30, f"seed drift: {len(recs)} records"
    return recs


def seed_to_supabase(db) -> dict:
    """Insert the synthetic seed into sci_hightc_measurements. Returns counts."""
    recs = synthetic_records()
    result = db.table("sci_hightc_measurements").insert(recs).execute()
    inserted = len(result.data) if result and result.data else 0
    return {"attempted": len(recs), "inserted": inserted, "synthetic": True}
