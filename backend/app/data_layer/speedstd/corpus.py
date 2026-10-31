# Prepared by Twistor Holdings LLC
"""TW-840 speed standard: benchmark corpora.

Two artifacts, honestly labeled:

1. The REAL CLIMATESHIFT-1 seed (40 trial records) ships in the
   crop_climate lane; the benchmark uses it for correctness parity.
2. The SCALE corpus below is a schema-faithful scale-out of that seed:
   identical columns and value model, extended to telemetry cadence
   (readings per device-day) across the lane's five real sites plus
   fifteen synthetic sites. The shipped v1 seed is 40 rows; no honest
   latency claim can be made at 40 rows, so the latency proof runs on
   the scale-out and says so.

Bronze form: one verbatim JSONL file per (device, date) partition,
plus one monolithic JSON array, which is the current access pattern's
artifact (consumers load the whole file to answer anything).
"""

from __future__ import annotations

import json
import random
import zlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# CLIMATESHIFT-1 lane constants (crop_climate/seed.py), same value model.
GENOTYPES = [
    ("DKC-62-98", "maize", 11.5, 0.55),
    ("P1197AM", "maize", 10.8, 0.50),
    ("AG-38X9", "soybean", 4.1, 0.35),
    ("SY-517", "wheat", 6.3, 0.45),
]
SITES = {
    "Greeley, CO": (300, 1650, "irrigated"),
    "Ames, IA": (780, 1750, "rainfed"),
    "Lincoln, NE": (640, 1900, "rainfed"),
    "Lubbock, TX": (430, 2400, "irrigated"),
    "Urbana, IL": (890, 1800, "rainfed"),
}
# Scale-out sites (synthetic, for partition-count realism only).
EXTRA_SITES = {
    "Bozeman, MT": (360, 1500, "irrigated"),
    "Fargo, ND": (510, 1650, "rainfed"),
    "Brookings, SD": (570, 1700, "rainfed"),
    "Manhattan, KS": (860, 2050, "rainfed"),
    "Columbia, MO": (980, 1950, "rainfed"),
    "West Lafayette, IN": (930, 1850, "rainfed"),
    "Madison, WI": (880, 1600, "rainfed"),
    "Grand Forks, ND": (480, 1550, "rainfed"),
    "Kearney, NE": (590, 1950, "irrigated"),
    "Garden City, KS": (480, 2150, "irrigated"),
    "Amarillo, TX": (500, 2300, "irrigated"),
    "Fort Collins, CO": (380, 1700, "irrigated"),
    "Twin Falls, ID": (270, 1750, "irrigated"),
    "Pullman, WA": (460, 1500, "rainfed"),
    "Corvallis, OR": (1080, 1450, "rainfed"),
}
ALL_SITES = {**SITES, **EXTRA_SITES}

COLUMNS = (
    "device",
    "date",
    "ts",
    "genotype",
    "crop",
    "yield_t_ha",
    "temp_anomaly_c",
    "precip_mm",
    "gdd",
    "heat_days",
    "irrigation",
)


def _rng_for(seed: int, device: str, day: date) -> random.Random:
    stable = zlib.crc32(f"{seed}|{device}|{day.isoformat()}".encode())
    return random.Random(stable)


def make_rows(
    device: str, day: date, readings: int, seed: int = 840
) -> list[dict[str, Any]]:
    """Deterministic telemetry rows for one device-day, CLIMATESHIFT shape."""
    base_precip, base_gdd, irrigation = ALL_SITES.get(device, (600, 1800, "rainfed"))
    rng = _rng_for(seed, device, day)
    year_span = max(0, day.year - 2018)
    rows: list[dict[str, Any]] = []
    for i in range(readings):
        g_name, crop, base_yield, heat_sens = GENOTYPES[rng.randrange(len(GENOTYPES))]
        anomaly = 0.3 + 0.08 * year_span + rng.uniform(-0.15, 0.15)
        precip = round(base_precip * rng.uniform(0.82, 1.18) / 365.0, 2)
        heat_days = max(0, int(6 + 1.4 * year_span + rng.uniform(-4, 4)))
        drought = max(0.0, 1.0 - precip * 365.0 / base_precip)
        heat_drag = heat_sens * max(0.0, anomaly - 0.5) * 0.12
        mitigation = 0.45 if irrigation == "irrigated" else 0.0
        loss = max(0.0, heat_drag * (1 - mitigation) + drought * 0.10)
        yield_t_ha = max(
            0.5, round(base_yield * (1 - loss) * rng.uniform(0.94, 1.06), 2)
        )
        rows.append(
            {
                "device": device,
                "date": day.isoformat(),
                "ts": f"{day.isoformat()}T{i:02d}:15:00+00:00",
                "genotype": g_name,
                "crop": crop,
                "yield_t_ha": yield_t_ha,
                "temp_anomaly_c": round(anomaly, 3),
                "precip_mm": precip,
                "gdd": round(base_gdd / 365.0 * rng.uniform(0.9, 1.1), 2),
                "heat_days": heat_days,
                "irrigation": irrigation,
            }
        )
    return rows


def bronze_partition_path(
    bronze_root: Path, dataset: str, device: str, day: date
) -> Path:
    return (
        Path(bronze_root)
        / dataset
        / f"device={device}"
        / f"date={day.isoformat()}.jsonl"
    )


def write_bronze_partition(
    bronze_root: Path, dataset: str, device: str, day: date, rows: list[dict[str, Any]]
) -> Path:
    path = bronze_partition_path(bronze_root, dataset, device, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def generate_scale_corpus(
    out_root: Path,
    dataset: str = "climateshift-1",
    devices: list[str] | None = None,
    start: date = date(2018, 1, 1),
    days: int = 365,
    readings_per_day: int = 4,
    seed: int = 840,
) -> dict[str, Any]:
    """Write bronze JSONL partitions AND the monolithic JSON array.

    The monolith is the baseline artifact: one file, every row, loaded
    whole by the current access pattern. Returns corpus statistics.
    """
    devices = devices or list(ALL_SITES)
    bronze_root = Path(out_root) / "bronze"
    monolith = Path(out_root) / f"{dataset}.json"
    monolith.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with monolith.open("w", encoding="utf-8") as mono:
        mono.write("[\n")
        first = True
        for device in devices:
            for d in range(days):
                day = start + timedelta(days=d)
                rows = make_rows(device, day, readings_per_day, seed=seed)
                write_bronze_partition(bronze_root, dataset, device, day, rows)
                for row in rows:
                    if not first:
                        mono.write(",\n")
                    mono.write(json.dumps(row))
                    first = False
                    total += 1
        mono.write("\n]\n")
    bronze_bytes = sum(p.stat().st_size for p in bronze_root.rglob("*.jsonl"))
    return {
        "dataset": dataset,
        "rows": total,
        "devices": len(devices),
        "days": days,
        "readings_per_day": readings_per_day,
        "monolith_bytes": monolith.stat().st_size,
        "bronze_jsonl_bytes": bronze_bytes,
        "partitions": len(devices) * days,
    }
