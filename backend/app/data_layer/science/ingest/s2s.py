# Prepared by Twistor Holdings LLC
"""TW-612 S2S lane ingestors: real-data adapters, green sources first.

Ingestion sequence (Diego TW-610):
  1. GEFSv12 reforecasts  (GREEN)  - AWS S3, no auth
  2. 20CRv3               (GREEN)  - NERSC portal tarballs, no auth
  3. ERA5 via WB2 Zarr    (GREEN*) - GCS public bucket, attribution
  4. S2S database         (GREEN*) - CC-BY whitelist centers ONLY; needs ECDS account (Justynn)
  5. AI Weather Quest     (YELLOW) - package docs check before any scrape
  6. WB2 IFS baselines    (YELLOW) - read per-dataset LICENSE first

Each adapter exposes plan_*() -> list[IngestItem]. The BronzeWriter does the
fetching, hashing, and license-tagged recording. Silver harmonization maps
are documented per source; numeric extraction runs in the ingest worker.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from .bronze import IngestItem
from .licenses import KNOWN_LICENSES

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- GEFS
GEFS_BUCKET = "noaa-gefs-retrospective"
GEFS_S3 = f"https://{GEFS_BUCKET}.s3.amazonaws.com"
# S2S-relevant variables, surface + minimal levels (Diego: variable-subsets first)
GEFS_VARIABLES = ("tmp_2m", "acpcp_sfc", "prmsl_msl")
GEFS_DAYS = ("Days:1-10", "Days:11-16")  # weeks 2-6 ~= days 8-42; reforecast ships 1-16


def gefs_list_keys(prefix: str, max_keys: int = 1000) -> list[str]:
    """List S3 keys under a prefix (machine API, no auth). Returns grib2 keys."""
    import urllib.request

    url = f"{GEFS_S3}/?list-type=2&prefix={prefix}&max-keys={max_keys}"
    req = urllib.request.Request(url, headers={"User-Agent": "Twistor-Ingest/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        root = ET.fromstring(resp.read())
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    return [
        c.find("s3:Key", ns).text
        for c in root.findall("s3:Contents", ns)
        if c.find("s3:Key", ns).text.endswith(".grib2")
    ]


def plan_gefs(init_dates: list[str], member: str = "c00") -> list[IngestItem]:
    """Plan GEFS reforecast pulls. init_dates like ['2000010100'] (YYYYMMDDHH).

    One .grib2 per (date, variable, days-window). The .idx sidecars let the
    worker byte-range the exact variable without downloading 30MB files.
    """
    lic = KNOWN_LICENSES["gefs-reforecast"]
    items: list[IngestItem] = []
    for init in init_dates:
        y = init[:4]
        for var in GEFS_VARIABLES:
            for days in GEFS_DAYS:
                key = (
                    f"GEFSv12/reforecast/{y}/{init}/{member}/{days}/"
                    f"{var}_{init}_{member}.grib2"
                )
                items.append(
                    IngestItem(
                        source_key="gefs-reforecast",
                        remote_url=f"{GEFS_S3}/{key}",
                        filename=f"gefs/{init}/{var}_{days.replace(':', '-')}_{member}.grib2",
                        license=lic,
                        metadata={
                            "init": init,
                            "variable": var,
                            "days": days,
                            "member": member,
                            "center": "NCEP",
                            "run_type": "reforecast",
                            "model_cycle": "GEFSv12",
                            "truth_source": None,
                        },
                    )
                )
    return items


# ---------------------------------------------------------------- 20CRv3
CR20_PORTAL = "https://portal.nersc.gov"
# v3 tarballs: everymember_anal_netcdf/{subdaily|daily|mnmean}/{VAR}/{VAR}_{YEAR}.tar
CR20_V3_VARS = ("TMP", "PRMSL", "PRATE")  # 2m temp, MSLP, precip rate
CR20_V3_FREQ = "daily"  # daily means keep the pull small; subdaily available


def plan_20crv3(years: list[int]) -> list[IngestItem]:
    """Plan 20CRv3 tarball pulls (one tarball per variable per year)."""
    lic = KNOWN_LICENSES["20crv3"]
    items: list[IngestItem] = []
    for year in years:
        for var in CR20_V3_VARS:
            url = (
                f"{CR20_PORTAL}/dods/20C_Reanalysis_version3/"
                f"everymember_anal_netcdf/{CR20_V3_FREQ}/{var}/{var}_{year}.tar"
            )
            items.append(
                IngestItem(
                    source_key="20crv3",
                    remote_url=url,
                    filename=f"20crv3/{CR20_V3_FREQ}/{var}_{year}.tar",
                    license=lic,
                    metadata={
                        "year": year,
                        "variable": var,
                        "freq": CR20_V3_FREQ,
                        "truth_source": "20CRv3",
                        "assimilation_window": "surface-only",
                        "note": "trend-safe truth anchor; 80-member ensemble mean+spread",
                    },
                )
            )
    return items


# ---------------------------------------------------------------- ERA5 via WB2
WB2_GCS = "https://storage.googleapis.com/storage/v1/b/weatherbench2/o"
WB2_ZARR_PREFIX = (
    "datasets/era5/1959-2022-1h-240x121_equiangular_with_poles_conservative.zarr"
)
# S2S truth variables in the WB2 Zarr store
WB2_VARIABLES = (
    "2m_temperature",
    "mean_sea_level_pressure",
    "total_precipitation_6hr",
)


def plan_era5_wb2() -> list[IngestItem]:
    """Plan ERA5 truth pulls. The Zarr store is one logical dataset; bronze
    records the store manifest + per-variable .zattrs as the raw artifact,
    and the worker reads variable chunks via xr.open_zarr (no full download).

    Provenance caveats carried into silver (Diego TW-610):
      ERA5T lags real time ~5 days; ERA5 precipitation is modelled, not assimilated.
    """
    lic = KNOWN_LICENSES["era5-wb2"]
    items: list[IngestItem] = []
    for var in WB2_VARIABLES:
        items.append(
            IngestItem(
                source_key="era5-wb2",
                remote_url=(f"{WB2_GCS}/{WB2_ZARR_PREFIX}/{var}/.zarray?alt=media"),
                filename=f"era5/wb2_manifest_{var}.zarray.json",
                license=lic,
                metadata={
                    "variable": var,
                    "zarr_store": f"gs://weatherbench2/{WB2_ZARR_PREFIX}",
                    "truth_source": "ERA5",
                    "truth_lag_days": 5,
                    "assimilation_window": "12h 4D-Var",
                    "caveats": [
                        "ERA5T ~5d real-time lag",
                        "ERA5 precipitation is modelled, not assimilated",
                    ],
                    "attribution": "Copernicus Climate Change Service / WeatherBench 2",
                },
            )
        )
    return items


def era5_zarr_url(variable: str | None = None) -> str:
    """Public GCS URL for the WB2 ERA5 Zarr store (xr.open_zarr target)."""
    base = f"gs://weatherbench2/{WB2_ZARR_PREFIX}"
    return f"{base}/{variable}" if variable else base


# ---------------------------------------------------------------- S2S database (account-gated)
S2S_CCBY_CENTERS = ("ECMWF", "NCEP", "KMA", "ECCC", "UKMO", "HMCR", "CPTEC")
S2S_NC_CENTERS = (
    "BoM",
    "CMA",
    "CNR-ISAC",
    "CNRM",
    "IAP-CAS",
    "JMA",
)  # RED: never ingested
S2S_ECDS = "https://ecds.ecmwf.int/datasets/s2s-forecasts"


def plan_s2s_database(centers: list[str] | None = None) -> list[IngestItem]:
    """Plan S2S database pulls. REQUIRES an ECDS account (Justynn creates it).

    The NC-center exclusion is enforced here, not downstream: requesting a RED
    center raises before any IngestItem is built. The whitelist is published
    as a trust feature of the dataset.
    """
    lic = KNOWN_LICENSES["s2s-db-ccby"]
    wanted = tuple(centers) if centers else S2S_CCBY_CENTERS
    blocked = [c for c in wanted if c in S2S_NC_CENTERS]
    if blocked:
        raise ValueError(
            f"S2S NC centers are RED and excluded from paywalled ingest: {blocked}. "
            f"Whitelist: {S2S_CCBY_CENTERS}"
        )
    unknown = [c for c in wanted if c not in S2S_CCBY_CENTERS]
    if unknown:
        raise ValueError(f"unknown S2S centers (not on whitelist): {unknown}")
    return [
        IngestItem(
            source_key="s2s-db-ccby",
            remote_url=f"{S2S_ECDS}?tab=overview",
            filename=f"s2s/{center.lower()}_reforecast_manifest.json",
            license=lic,
            metadata={
                "center": center,
                "run_type": "reforecast",
                "note": "ECDS account required; reforecasts first (no access delay), "
                "real-time second (48h-1wk delay per center). "
                "Worker uses the ECDS API with stored credentials.",
            },
        )
        for center in wanted
    ]


# ---------------------------------------------------------------- YELLOW sources (checks before ingest)
def check_ai_weather_quest() -> dict:
    """YELLOW: verify the package exposes a bulk scores download before any scrape.

    Returns the check outcome; the ingestor is built only on a positive check.
    """
    return {
        "source": "ai-weather-quest",
        "status": "check_required",
        "action": "Read https://ecmwf-ai-weather-quest.readthedocs.io/en/latest/ "
        "for a download function. Do not scrape leaderboards without one.",
        "license": KNOWN_LICENSES["ai-weather-quest"].tag,
    }


def check_wb2_ifs_baselines() -> dict:
    """YELLOW: per-dataset LICENSE files must be read before ingesting IFS baselines."""
    return {
        "source": "wb2-ifs-baseline",
        "status": "check_required",
        "action": "Read each dataset's LICENSE file in gs://weatherbench2/datasets/. "
        "WB2: 'Some datasets allow commercial use. Others only permit research use.'",
        "license": KNOWN_LICENSES["wb2-ifs-baseline"].tag,
    }


# ---------------------------------------------------------------- silver mapping
S2S_SILVER_MAPPING = {
    "gefs-reforecast": {
        "center": "metadata.center",
        "model_cycle": "metadata.model_cycle",
        "run_type": "metadata.run_type",
        "lead_weeks": "derived: days window -> weeks 2-6",
        "variable": "metadata.variable",
        "ensemble_size": "11 (GEFSv12 reforecast members)",
        "truth_source": "paired at scoring time (ERA5/20CRv3)",
    },
    "20crv3": {
        "truth_source": "20CRv3",
        "assimilation_window": "metadata.assimilation_window",
        "note": "truth anchor rows, not forecast rows: lead_weeks null, crps null",
    },
    "era5-wb2": {
        "truth_source": "ERA5",
        "truth_lag_days": 5,
        "note": "truth anchor rows; precipitation rows carry the modelled-not-assimilated flag",
    },
}
