"""TW-593 Track 2 · Turbulence lane — V1 synthetic seed data.

Prepared by Twistor Holdings LLC.

30 realistic SYNTHETIC DNS/LES/RANS records across the three canonical
closure-challenge case families: channel flow, boundary layer, jet.
Reynolds numbers span 1e3 - 1e5. Every row is stamped synthetic=True.
Deterministic (uuid5) so reseeds are idempotent.
"""
from __future__ import annotations

import uuid
from typing import Any

SEED_LAB_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "twistor.sci.turbulence.seed-lab"))
SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "twistor.sci.turbulence.measurement")
ARTIFACT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "twistor.sci.turbulence.artifact")

# (case_name, reynolds, solver, scheme, closure_model, grid_resolution)
_CASES: list[tuple[str, float, str, str, str | None, str]] = [
    # --- channel flow: Re_tau 1000 .. 5200 (the DNS workhorse) ---
    ("channel_flow", 1000.0, "DNS", "spectral", None, "512x257x512"),
    ("channel_flow", 2000.0, "DNS", "spectral", None, "1024x512x1024"),
    ("channel_flow", 5200.0, "DNS", "spectral", None, "10240x1536x7680"),
    ("channel_flow", 2000.0, "LES", "FVM", "smagorinsky", "256x128x256 (2.1M cells)"),
    ("channel_flow", 2000.0, "LES", "FVM", "dynamic-smagorinsky", "256x128x256 (2.1M cells)"),
    ("channel_flow", 5200.0, "LES", "FVM", "wale", "512x256x512 (16.8M cells)"),
    ("channel_flow", 10000.0, "RANS", "FVM", "k-epsilon", "1.2M cells"),
    ("channel_flow", 20000.0, "RANS", "FVM", "k-omega-sst", "2.4M cells"),
    ("channel_flow", 5200.0, "LES", "spectral-element", "gnn-learned", "512x256x512 (16.8M cells)"),
    # --- boundary layer: Re_theta 1e3 .. 1e5 (flat-plate, zero pressure gradient) ---
    ("boundary_layer", 1000.0, "DNS", "spectral", None, "320x385x320"),
    ("boundary_layer", 2500.0, "DNS", "spectral", None, "1152x513x1152"),
    ("boundary_layer", 6500.0, "DNS", "spectral", None, "2304x513x2304"),
    ("boundary_layer", 10000.0, "LES", "FVM", "wale", "384x192x384 (9.4M cells)"),
    ("boundary_layer", 25000.0, "LES", "FVM", "dynamic-smagorinsky", "768x384x768 (75M cells)"),
    ("boundary_layer", 50000.0, "RANS", "FVM", "k-omega-sst", "3.1M cells"),
    ("boundary_layer", 100000.0, "RANS", "FVM", "k-epsilon", "4.8M cells"),
    ("boundary_layer", 6500.0, "LES", "FDM", "gnn-learned", "1152x257x1152 (57M cells)"),
    # --- jet: Re_D 1e3 .. 1e5 (round free jet) ---
    ("jet", 1000.0, "DNS", "spectral-element", None, "1.8M elements"),
    ("jet", 5000.0, "DNS", "spectral-element", None, "12M elements"),
    ("jet", 11000.0, "DNS", "spectral-element", None, "48M elements"),
    ("jet", 25000.0, "LES", "FVM", "smagorinsky", "25M cells"),
    ("jet", 50000.0, "LES", "FVM", "wale", "64M cells"),
    ("jet", 100000.0, "RANS", "FVM", "k-epsilon", "6.2M cells"),
    ("jet", 100000.0, "DES", "FVM", "k-omega-sst", "18M cells"),
    ("jet", 11000.0, "LES", "FDM", "gnn-learned", "24M cells"),
    # --- closure-challenge extras: adverse pressure gradient + separated flow ---
    ("boundary_layer", 8000.0, "DNS", "spectral", None, "1536x513x1536"),
    ("channel_flow", 15000.0, "URANS", "FVM", "k-omega-sst", "1.8M cells"),
    ("channel_flow", 8000.0, "LES", "FDM", "dynamic-smagorinsky", "768x384x768 (37.7M cells)"),
    ("boundary_layer", 15000.0, "LES", "FVM", "smagorinsky", "512x256x512 (16.8M cells)"),
    ("jet", 20000.0, "DNS", "spectral-element", None, "64M elements"),
]

_DOMAINS = {
    "channel_flow": "2pi x 2 x pi channel (half-height h)",
    "boundary_layer": "flat plate, zero pressure gradient",
    "jet": "round free jet, D diameter nozzle",
}
_BCS = {
    "channel_flow": "periodic streamwise/spanwise, no-slip walls",
    "boundary_layer": "no-slip wall, freestream top, periodic spanwise",
    "jet": "inflow nozzle profile, outflow far-field, axisymmetric",
}


def _fidelity(solver: str, reynolds: float, i: int) -> dict[str, Any]:
    """Plausible fidelity metrics; DNS is ground truth (error ~ 0)."""
    if solver == "DNS":
        return {
            "l2_error_mean_velocity": 0.0,
            "reynolds_stress_mae": 0.0,
            "spectra_agreement": 1.0,
            "stability_hours": 400.0 + (i % 7) * 25.0,
        }
    base = {"LES": 0.02, "DES": 0.04, "URANS": 0.09, "RANS": 0.12}[solver]
    jitter = (i % 5) * 0.004
    return {
        "l2_error_mean_velocity": round(base + jitter, 4),
        "reynolds_stress_mae": round(base * 1.6 + jitter, 4),
        "spectra_agreement": round(1.0 - base * 1.2 - jitter, 4),
        "stability_hours": 120.0 + (i % 9) * 10.0,
    }


def build_seed_records() -> list[dict[str, Any]]:
    """Return the 30 V1 synthetic silver rows (deterministic)."""
    assert len(_CASES) == 30, f"seed table must hold exactly 30 rows, has {len(_CASES)}"
    records: list[dict[str, Any]] = []
    for i, (case, re, solver, scheme, closure, grid) in enumerate(_CASES):
        key = f"{case}:{re}:{solver}:{closure or 'none'}:{i}"
        records.append({
            "id": str(uuid.uuid5(SEED_NAMESPACE, key)),
            "artifact_id": str(uuid.uuid5(ARTIFACT_NAMESPACE, key)),
            "lab_id": SEED_LAB_ID,
            "calibration_id": None,  # simulation runs: no instrument
            "conditions": {
                "domain": _DOMAINS[case],
                "bc": _BCS[case],
                "extra": {"reynolds_definition": {"channel_flow": "Re_tau",
                                                  "boundary_layer": "Re_theta",
                                                  "jet": "Re_D"}[case]},
            },
            "measured_at": "2026-10-18T00:00:00Z",
            "case_name": case,
            "reynolds": re,
            "grid_resolution": grid,
            "solver": solver,
            "scheme": scheme,
            "closure_model": closure,
            "fidelity_metrics": _fidelity(solver, re, i),
            "synthetic": True,
        })
    return records


def seed_summary() -> dict[str, Any]:
    rows = build_seed_records()
    return {
        "lane": "turbulence",
        "row_count": len(rows),
        "synthetic": all(r["synthetic"] for r in rows),
        "cases": sorted({r["case_name"] for r in rows}),
        "solvers": sorted({r["solver"] for r in rows}),
        "reynolds_range": [min(r["reynolds"] for r in rows),
                           max(r["reynolds"] for r in rows)],
    }
