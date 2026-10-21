# Prepared by Twistor Holdings LLC
"""Subseasonal lane V1 seed: 30 realistic SYNTHETIC forecast records.

Mirrors the Lane 2 narrative (breakthrough-lanes.md):
- base dynamical model (bias_correction='none') runs skill NEGATIVE vs
  climatology — the fifty-year "predictability desert".
- the learned probabilistic bias-correction layer ('pbc_v1') flips skill
  POSITIVE vs climatology — the MIT/Microsoft/ECMWF Oct 2026 breach.

All rows synthetic=true. No real lab data until partners sign.
Deterministic (seeded RNG) so re-runs produce identical fixtures.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

SYNTHETIC_LAB_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "twistor.synthetic.s2s-lab"))
SYNTHETIC_LAB_NAME = "Twistor Synthetic S2S Fixture Lab"

rng = random.Random(593)

_VARIABLES = {
    "t2m": {"unit": "C", "crps_lo": 0.9, "crps_hi": 2.4},      # 2m temperature
    "pr": {"unit": "mm/day", "crps_lo": 1.2, "crps_hi": 3.8},  # precipitation
    "mslp": {"unit": "hPa", "crps_lo": 1.5, "crps_hi": 4.0},   # mean sea-level pressure
}
_REGIONS = ["CONUS", "EU", "TROP", "NA"]
_MODEL_SOURCES = [
    # (model_source, base_model, ensemble_size, bias_correction,
    #  center, model_cycle, run_type, truth_source, truth_lag_days,
    #  assimilation_window, reforecast_design)
    ("ecmwf_ifs", "ecmwf_ifs_cy48r1", 51, "pbc_v1",
     "ecmwf", "IFS CY48R1", "reforecast",
     "ERA5:0.25deg:v1", 5, "+9h/-3h", "on-the-fly:20y:11m"),   # corrected: the breach
    ("ecmwf_ifs", "ecmwf_ifs_cy48r1", 51, "none",
     "ecmwf", "IFS CY48R1", "reforecast",
     "ERA5:0.25deg:v1", 5, "+9h/-3h", "on-the-fly:20y:11m"),   # raw: the desert
    ("graphcast", "graphcast_oper", 25, "none",
     "deepmind", "graphcast_oper", "realtime",
     "ERA5:0.25deg:v1", 5, "+9h/-3h", None),
    ("pangu", "pangu_weather_oper", 25, "none",
     "huawei", "pangu_weather_oper", "realtime",
     "ERA5:0.25deg:v1", 5, "+9h/-3h", None),
]


def _init_dates() -> list[str]:
    # 30 monthly init dates, 2024-01 through 2026-06.
    out = []
    y, m = 2024, 1
    for _ in range(30):
        out.append(f"{y:04d}-{m:02d}-15")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def build_seed_records() -> list[dict]:
    records = []
    variables = list(_VARIABLES)
    for i, init_date in enumerate(_init_dates()):
        variable = variables[i % len(variables)]
        region = _REGIONS[i % len(_REGIONS)]
        (model, base_model, ens, bias_corr, center, model_cycle, run_type,
         truth_source, truth_lag_days, assim_window,
         reforecast_design) = _MODEL_SOURCES[i % len(_MODEL_SOURCES)]
        spec = _VARIABLES[variable]
        crps = round(rng.uniform(spec["crps_lo"], spec["crps_hi"]), 3)
        if bias_corr == "pbc_v1":
            skill = round(rng.uniform(0.02, 0.18), 4)   # beat climatology: the breach
        else:
            skill = round(rng.uniform(-0.25, -0.05), 4)  # worse than climatology: the desert
        lead_weeks = 2 + (i % 5)
        records.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.s2s.synthetic.{i:02d}")),
                "artifact_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.s2s.artifact.{i:02d}")),
                "lab_id": SYNTHETIC_LAB_ID,
                "calibration_id": None,  # model runs: no instrument chain
                "conditions": {
                    "bias_correction": bias_corr,
                    "base_model": base_model,
                },
                "measured_at": datetime.fromisoformat(f"{init_date}T00:00:00+00:00")
                .astimezone(timezone.utc)
                .isoformat(),
                "model_source": model,
                "center": center,
                "model_cycle": model_cycle,
                "run_type": run_type,
                "truth_source": truth_source,
                "truth_lag_days": truth_lag_days,
                "assimilation_window": assim_window,
                "reforecast_design": reforecast_design,
                "lead_weeks": lead_weeks,
                "ensemble_size": ens,
                "variable": variable,
                "region": region,
                "crps": crps,
                "skill_vs_climatology": skill,
                "synthetic": True,
            }
        )
    return records


SEED_MEASUREMENTS: list[dict] = build_seed_records()


def validate_seed() -> list[str]:
    """Validate every seed record against the ingest schema. Returns errors."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from schemas import IngestMeasurement  # noqa: E402

    errors = []
    for i, rec in enumerate(SEED_MEASUREMENTS):
        try:
            m = IngestMeasurement(**{**rec, "collector_signature": "synthetic-fixture"})
            if not m.synthetic:
                errors.append(f"record {i}: synthetic flag not set")
        except Exception as exc:
            errors.append(f"record {i}: {exc}")
    return errors


if __name__ == "__main__":
    errs = validate_seed()
    print(f"{len(SEED_MEASUREMENTS)} records, {len(errs)} errors")
    for e in errs:
        print(" ", e)
