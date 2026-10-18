"""TW-593 Track 2 crop-climate lane: synthetic V1 seed (TW-596).

40 realistic SYNTHETIC trial records (synthetic=true, labeled), spanning
genotypes x locations x years 2018-2026. Ten rows carry 2035-projected
climate (SSP5-8.5, temp_anomaly_c ~ +2.0..+2.8): these are the benchmark's
withheld 'test_future' split — the "predict 2035 performance from 2025
data" frontier from Diego's 2026-10-18 breakthrough-lanes memo.

No real lab data. No real yields. Everything here is invented.
"""

from __future__ import annotations

import random

from . import models

GENOTYPES = [
    # (name, crop, base_yield_t_ha, heat_sensitivity)
    ("DKC-62-98", "maize", 11.5, 0.55),
    ("P1197AM", "maize", 10.8, 0.50),
    ("AG-38X9", "soybean", 4.1, 0.35),
    ("SY-517", "wheat", 6.3, 0.45),
]

LOCATIONS = [
    # (name, base_precip_mm, base_gdd, irrigation)
    ("Greeley, CO", 300, 1650, "irrigated"),
    ("Ames, IA", 780, 1750, "rainfed"),
    ("Lincoln, NE", 640, 1900, "rainfed"),
    ("Lubbock, TX", 430, 2400, "irrigated"),
    ("Urbana, IL", 890, 1800, "rainfed"),
]

YEARS = [2018, 2020, 2022, 2024, 2026]
TILLAGE = ["no-till", "strip-till", "conventional"]
PREVIOUS_CROP = {"maize": "soybean", "soybean": "maize", "wheat": "fallow"}
NITROGEN = {"maize": 185, "soybean": 20, "wheat": 110}
PLANTING_DOY = {"maize": 118, "soybean": 135, "wheat": 100}


def seed_records() -> list[dict]:
    """Return 40 synthetic trial records (kwargs for add_measurement)."""
    rng = random.Random(593)  # deterministic seed
    records: list[dict] = []
    combo = 0
    for g_name, crop, base_yield, heat_sens in GENOTYPES:
        for loc, base_precip, base_gdd, irrigation in LOCATIONS:
            future_like = combo % 4 == 0  # 10 of 40 rows: withheld split
            y1 = YEARS[combo % len(YEARS)]
            y2 = YEARS[(combo + 2) % len(YEARS)]
            for year in (y1, y2):
                anomaly = 0.3 + 0.08 * (year - 2018) + rng.uniform(-0.15, 0.15)
                precip = base_precip * rng.uniform(0.82, 1.18)
                heat_days = max(0, int(6 + 1.4 * (year - 2018) + rng.uniform(-4, 4)))
                scenario = None
                if future_like:
                    # 2035-projected climate grafted onto a trial year:
                    # hotter, drier, more extreme heat days.
                    anomaly = 2.0 + rng.uniform(0.0, 0.8)
                    precip *= 0.72
                    heat_days += 14
                    scenario = "ssp585"

                # yield: base, penalized by heat (irrigation blunts it),
                # drought index drag, plus trial noise
                drought = max(0.0, 1.0 - precip / base_precip)
                heat_drag = heat_sens * max(0.0, anomaly - 0.5) * 0.12
                irrig_mitigation = 0.45 if irrigation == "irrigated" else 0.0
                loss = max(0.0, heat_drag * (1 - irrig_mitigation) + drought * 0.10)
                yield_t_ha = round(base_yield * (1 - loss) * rng.uniform(0.94, 1.06), 2)
                yield_t_ha = max(0.5, yield_t_ha)

                conditions: dict = {
                    "trial_design": "rcbd",
                    "replicates": 4,
                    "plot_size_m2": 30,
                }
                if future_like:
                    conditions["simulated_future_climate"] = True
                    conditions["climate_scenario"] = scenario

                records.append({
                    "conditions": conditions,
                    "genotype": g_name,
                    "location": loc,
                    "year": year,
                    "season": "summer",
                    "weather": {
                        "season_precip_mm": round(precip, 1),
                        "season_gdd": int(base_gdd * rng.uniform(0.95, 1.08)),
                        "heat_days_gt35c": heat_days,
                        "mean_temp_c": round(19.5 + anomaly * 0.9, 2),
                        "temp_anomaly_c": round(anomaly, 2),
                        "drought_index": round(drought, 3),
                    },
                    "management": {
                        "irrigation": irrigation,
                        "nitrogen_kg_ha": NITROGEN[crop] + rng.randint(-15, 15),
                        "tillage": rng.choice(TILLAGE),
                        "planting_doy": PLANTING_DOY[crop] + rng.randint(-6, 6),
                        "previous_crop": PREVIOUS_CROP[crop],
                    },
                    "yield_t_ha": yield_t_ha,
                    "synthetic": True,
                })
            combo += 1
    assert len(records) == 40
    assert all(r["synthetic"] for r in records)
    return records


def bootstrap_demo_store() -> models.CropClimateStore:
    """Build a demo store: lab + artifact + calibration + 40 seeds + v1.0.0.

    Used by the router's default store factory. Synthetic, labeled.
    """
    store = models.CropClimateStore()
    lab = store.register_lab(
        name="Twistor Synthetic Trials",
        kind="independent",
        verification_status="verified",
    )
    artifact = store.register_artifact(
        lab_id=lab.id,
        storage_path="s3://twistor-sci-bronze/crop_climate/seed_v1.csv",
        content_hash="sha256:seed-v1-demo",
        collector_signature="sig:seed-v1-demo",
        schema_version=1,
    )
    calibration = store.register_calibration(
        lab_id=lab.id,
        instrument_id="WS-01",
        instrument_kind="weather_station",
        calibrated_at="2024-01-15T00:00:00+00:00",
        standard_ref="NIST-traceable barometer/thermometer set",
    )
    for i, record in enumerate(seed_records()):
        store.add_measurement(
            artifact_id=artifact.id,
            lab_id=lab.id,
            # every 3rd trial is a pure field trial: no instrument calibration
            calibration_id=None if i % 3 == 0 else calibration.id,
            **record,
        )
    store.publish_version(
        models.DATASET_NAME,
        "1.0.0",
        release_notes="V1 synthetic seed: 40 trials, 2035-like rows withheld",
        benchmark_pin=models.BENCHMARK_NAME,
    )
    return store
