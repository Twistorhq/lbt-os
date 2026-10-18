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
    """Build a demo store: lab + artifact + calibration + seeds + v1.0.0.

    Hemp-first (TW-599 / CLIMATESHIFT-1 v1): 30 hemp trials lead, the 40-row
    maize/soy/wheat corpus rides as the methodology reference. Synthetic,
    labeled.
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
    all_records = seed_hemp_records() + seed_records()
    for i, record in enumerate(all_records):
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
        release_notes=(
            "CLIMATESHIFT-1 v1 synthetic seed: 30 hemp trials (hemp-first) + "
            "40 maize/soy/wheat reference rows; 2035-like rows withheld"
        ),
        benchmark_pin=models.BENCHMARK_NAME,
    )
    return store


# ---------------------------------------------------------------------------
# TW-599 CLIMATESHIFT-1 v1: hemp-first seed
# ---------------------------------------------------------------------------
# Diego's TW-592 lane-5 spec: V1 launches on hemp — the crop with no incumbent
# standard and the sharpest provenance stakes (THC compliance moves with
# unrecorded management). Cultivars, sites, and the GxE pattern below mirror
# the public extension literature (UVM 2024-2025 fiber/grain, Oregon State
# EM 9434, Missouri multi-location 2026, Cornell). All values SYNTHETIC.

HEMP_CULTIVARS = [
    # (name, use, base_fiber_t_ha, base_cbd_pct, thc_baseline_pct, gxe_sensitivity)
    ("Futura 75", "fiber", 9.8, 1.2, 0.18, 0.35),
    ("Fedora 17", "fiber", 8.9, 1.8, 0.22, 0.42),
    ("Santhica 27", "fiber", 9.2, 0.9, 0.15, 0.30),
    ("Carmagnola", "fiber", 8.4, 1.5, 0.25, 0.48),
    ("CRS-1", "grain", 4.2, 3.8, 0.28, 0.55),
    ("Katani", "grain", 3.9, 4.5, 0.31, 0.60),
    ("X-59", "dual", 7.1, 2.9, 0.24, 0.50),
    ("Altair", "dual", 6.8, 3.2, 0.26, 0.52),
]

HEMP_SITES = [
    # (location, base_precip_mm, heat_bias, program)
    ("Burlington, VT", 860, -0.4, "UVM Extension"),
    ("Corvallis, OR", 1080, -0.2, "Oregon State"),
    ("Columbia, MO", 1020, 0.5, "Missouri"),
    ("Ithaca, NY", 930, -0.3, "Cornell"),
    ("Fort Collins, CO", 380, 0.3, "CSU"),
]

HEMP_YEARS = [2022, 2023, 2024, 2025, 2026]
HEMP_LABS = ["ISO-17025:VT-041", "ISO-17025:OR-118", "ISO-17025:MO-062",
             "ISO-17025:NY-033", "ISO-17025:CO-077"]


def seed_hemp_records() -> list[dict]:
    """Return 30 synthetic hemp trial records (kwargs for add_measurement).

    Every record carries a traits panel: cannabinoid percentages (THC/CBD/CBG),
    fiber quality, and the testing lab's accreditation ID. 8 of 30 rows carry
    2035-projected climate (the withheld test_future split). GxE instability is
    modeled per the Missouri 2026 finding: THC drifts with site-year heat.
    """
    rng = random.Random(599)  # deterministic seed
    records: list[dict] = []
    combo = 0
    for cultivar, use, base_yield, base_cbd, base_thc, gxe in HEMP_CULTIVARS:
        for site_idx, (loc, base_precip, heat_bias, program) in enumerate(HEMP_SITES):
            if combo >= 30:
                break
            future_like = combo % 15 < 4  # 8 of 30 rows: withheld split
            year = HEMP_YEARS[combo % len(HEMP_YEARS)]

            anomaly = 0.3 + 0.08 * (year - 2022) + heat_bias + rng.uniform(-0.2, 0.2)
            precip = base_precip * rng.uniform(0.80, 1.20)
            heat_days = max(0, int(8 + 1.6 * (year - 2022) + rng.uniform(-4, 4)))
            scenario = None
            if future_like:
                anomaly = 2.1 + rng.uniform(0.0, 0.7)
                precip *= 0.70
                heat_days += 15
                scenario = "ssp585"

            # GxE: heat pushes THC up toward (and sometimes over) the 0.3%
            # compliance line; CBD follows inversely. This is the documented
            # Missouri 2026 instability, synthesized.
            thc = base_thc * (1 + gxe * max(0.0, anomaly - 0.5)) * rng.uniform(0.92, 1.08)
            thc = round(max(0.05, thc), 3)
            cbd = round(base_cbd * (1 - 0.25 * gxe * max(0.0, anomaly - 0.5))
                        * rng.uniform(0.90, 1.10), 2)
            cbd = max(0.2, cbd)
            cbg = round(cbd * rng.uniform(0.03, 0.08), 3)

            drought = max(0.0, 1.0 - precip / base_precip)
            loss = max(0.0, 0.10 * max(0.0, anomaly - 0.6) + drought * 0.12)
            fiber_yield = round(base_yield * (1 - loss) * rng.uniform(0.93, 1.07), 2)
            fiber_yield = max(1.0, fiber_yield)

            conditions: dict = {
                "trial_design": "rcbd",
                "replicates": 4,
                "plot_size_m2": 25,
                "program": program,
                "crop": "hemp",
                "use_type": use,
            }
            if future_like:
                conditions["simulated_future_climate"] = True
                conditions["climate_scenario"] = scenario

            traits = {
                "thc_pct": thc,
                "cbd_pct": cbd,
                "cbg_pct": cbg,
                "thc_compliant_03": thc < 0.3,
                "fiber_quality_grade": rng.choice(["A", "A", "B", "B", "C"]),
                "lab_accreditation_id": HEMP_LABS[site_idx],
                "coa_hash": f"sha256:hemp-coa-{combo:02d}",
            }

            records.append({
                "conditions": conditions,
                "genotype": cultivar,
                "location": loc,
                "year": year,
                "season": "summer",
                "weather": {
                    "season_precip_mm": round(precip, 1),
                    "heat_days_gt35c": heat_days,
                    "mean_temp_c": round(19.0 + anomaly * 0.9, 2),
                    "temp_anomaly_c": round(anomaly, 2),
                    "drought_index": round(drought, 3),
                },
                "management": {
                    "irrigation": "rainfed" if base_precip > 500 else "irrigated",
                    "nitrogen_kg_ha": 90 + rng.randint(-15, 15),
                    "tillage": rng.choice(TILLAGE),
                    "planting_doy": 135 + rng.randint(-8, 8),
                    "previous_crop": "fallow",
                },
                "yield_t_ha": fiber_yield,
                "synthetic": True,
                "traits": traits,
            })
            combo += 1
        if combo >= 30:
            break
    assert len(records) == 30
    assert all(r["synthetic"] for r in records)
    assert all(r["traits"]["lab_accreditation_id"] for r in records)
    assert sum(1 for r in records
               if r["conditions"].get("simulated_future_climate")) == 8
    return records
