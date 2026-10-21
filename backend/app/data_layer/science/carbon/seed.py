# Prepared by Twistor Holdings LLC
"""Carbon lane V1 seed: 36 realistic SYNTHETIC sequestration evidence records.

Mirrors the Lane 7 dossier narrative (carbon-sequestration.md, TW-600):
- soil profiles with method codes (dry-combustion vs walkley-black never
  silently pooled), including a Florida Grunwald-style legacy+resample pair
- flux-tower records (AmeriFlux-style NEE/GPP partitions)
- biomass pixels from GEDI and CCI with their uncertainty-framework tags
  recorded (the Hunka et al. misalignment, surfaced not hidden)
- biochar stability annex (H/Corg, aromatic C, 100-yr persistence estimates)
- MRV-method runs, one with weak baseline_credibility (the Verra lesson)
- registry crosswalks, clearly labeled registry-reported (the fox is not
  guarding this henhouse)

All rows synthetic=true. No real lab data until partners sign.
Deterministic (seeded RNG) so re-runs produce identical fixtures.
"""

from __future__ import annotations

import hashlib
import random
import uuid

SYNTHETIC_LAB_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "twistor.synthetic.carbon-lab"))
SYNTHETIC_LAB_NAME = "Twistor Synthetic Carbon Fixture Lab"

rng = random.Random(599)

_BASE = {
    "lab_id": SYNTHETIC_LAB_ID,
    "calibration_id": None,
    "conditions": {"sampling_protocol": "twistor:synthetic-v1"},
    "synthetic": True,
    "collector_signature": "synthetic-fixture",
}


def _soil(i: int, **kw) -> dict:
    d = dict(_BASE)
    d.update(
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.synthetic.soil.{i:02d}")),
            "artifact_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.artifact.soil.{i:02d}")),
            "measured_at": "2024-06-15T00:00:00+00:00",
            "record_class": "soil-profile",
            "carbon_pool": "soil-organic",
            "region": "FL",
            "site_id": f"FL-SYN-{i:02d}",
            "depth_top_cm": 0,
            "depth_bottom_cm": 30,
            "bulk_density_g_cm3": round(rng.uniform(1.1, 1.6), 2),
            "texture": rng.choice(["sandy-loam", "loamy-sand", "silt-loam"]),
            "ph": round(rng.uniform(5.5, 7.5), 1),
            "land_use": "pasture",
            "management_history": "rotational-grazing-10y",
        }
    )
    d.update(kw)
    return d


def _flux(i: int, **kw) -> dict:
    d = dict(_BASE)
    d.update(
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.synthetic.flux.{i:02d}")),
            "artifact_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.artifact.flux.{i:02d}")),
            "measured_at": "2024-01-01T00:00:00+00:00",
            "record_class": "flux-tower",
            "carbon_pool": "soil-organic",
            "analytical_method": "ec-tower",
            "region": "SE-US",
            "tower_id": f"US-SYN{i:02d}",
            "tower_doi": f"10.17190/AMF/SYN{i:02d}",
            "nee_g_c_m2_yr": round(rng.uniform(-350, -80), 1),
            "gpp_g_c_m2_yr": round(rng.uniform(1200, 2100), 1),
            "respiration_g_c_m2_yr": round(rng.uniform(900, 1600), 1),
            "gap_filling_method": "mds",
        }
    )
    d.update(kw)
    return d


def _biomass(i: int, **kw) -> dict:
    d = dict(_BASE)
    d.update(
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.synthetic.bio.{i:02d}")),
            "artifact_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.artifact.bio.{i:02d}")),
            "measured_at": "2022-07-01T00:00:00+00:00",
            "record_class": "biomass-pixel",
            "carbon_pool": "aboveground-biomass",
            "analytical_method": "lidar-agb",
            "region": "AMAZON",
            "site_id": f"AMZ-{i:03d}",
            "agb_mg_ha": round(rng.uniform(180, 420), 1),
            "epoch": "2022",
        }
    )
    d.update(kw)
    return d


def _biochar(i: int, **kw) -> dict:
    d = dict(_BASE)
    d.update(
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.synthetic.char.{i:02d}")),
            "artifact_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.artifact.char.{i:02d}")),
            "measured_at": "2023-03-01T00:00:00+00:00",
            "record_class": "soil-profile",
            "carbon_pool": "biochar-stable",
            "analytical_method": "dry-combustion",
            "region": "FL",
            "site_id": f"FL-BC-{i:02d}",
            "feedstock": rng.choice(["pine", "citrus-prunings", "sugarcane-bagasse"]),
            "pyrolysis_temp_c": round(rng.uniform(450, 650), 0),
            "residence_time_min": round(rng.uniform(20, 90), 0),
            "h_c_org_ratio": round(rng.uniform(0.25, 0.55), 2),
            "aromatic_c_pct": round(rng.uniform(65, 92), 1),
            "o_c_ratio": round(rng.uniform(0.08, 0.25), 2),
            "persistence_100yr_pct": round(rng.uniform(78, 94), 1),
            "persistence_method": "h-c-ratio-model",
        }
    )
    d.update(kw)
    return d


def _mrv(i: int, **kw) -> dict:
    d = dict(_BASE)
    d.update(
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.synthetic.mrv.{i:02d}")),
            "artifact_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.artifact.mrv.{i:02d}")),
            "measured_at": "2024-09-01T00:00:00+00:00",
            "record_class": "mrv-method-run",
            "carbon_pool": "soil-organic",
            "analytical_method": "model-derived",
            "region": "CONUS",
            "mrv_method_class": "daycent-modeled",
            "baseline_definition": "regional-standard-daycent",
            "additionality_claim": "no-till-adoption",
            "additionality_evidence_level": "modeled",
            "permanence_assumption": "100y-buffer-10pct",
            "buffer_terms": "10pct-buffer-pool",
            "uncertainty_quantification": "monte-carlo-1000",
            "baseline_credibility": "weak",
            "permanence_evidence": "assumed",
        }
    )
    d.update(kw)
    return d


def _registry(i: int, **kw) -> dict:
    d = dict(_BASE)
    d.update(
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.synthetic.reg.{i:02d}")),
            "artifact_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.artifact.reg.{i:02d}")),
            "measured_at": "2023-11-01T00:00:00+00:00",
            "record_class": "registry-crosswalk",
            "carbon_pool": "aboveground-biomass",
            "analytical_method": "registry-reported",
            "region": "GLOBAL",
            "mrv_method_class": "registry-certified",
            "baseline_definition": "standardized-regional",
            "additionality_claim": "avoided-deforestation",
            "additionality_evidence_level": "registry-attested",
            "baseline_credibility": "standardized-fiction",
            "permanence_evidence": "buffered",
            "class_payload": {"registry": "synthetic-verra-analog", "note": "registry-reported: not independently replicated"},
        }
    )
    d.update(kw)
    return d


def build_seed_records() -> list[dict]:
    records: list[dict] = []

    # --- 12 soil profiles: method-code spread (the comparability story) ---
    # Florida Grunwald-style legacy (1996, Walkley-Black) + resample (2024,
    # dry-combustion): same site, different methods — never silently pooled.
    records.append(_soil(0, measured_at="1996-04-01T00:00:00+00:00",
                         analytical_method="walkley-black",
                         soc_g_kg=round(rng.uniform(8, 14), 1),
                         site_id="FL-GRUN-LEGACY-01"))
    records.append(_soil(1, measured_at="2024-04-01T00:00:00+00:00",
                         analytical_method="dry-combustion",
                         soc_g_kg=round(rng.uniform(10, 18), 1),
                         site_id="FL-GRUN-LEGACY-01"))
    for i in range(2, 8):
        records.append(_soil(i,
                             analytical_method="dry-combustion",
                             soc_g_kg=round(rng.uniform(6, 22), 1)))
    for i in range(8, 10):
        records.append(_soil(i,
                             analytical_method="walkley-black",
                             soc_g_kg=round(rng.uniform(5, 16), 1)))
    for i in range(10, 12):
        records.append(_soil(i,
                             analytical_method="spectroscopy",
                             soc_g_kg=round(rng.uniform(7, 20), 1),
                             class_payload={"spectrometer": "vis-nir", "calibration_r2": 0.89}))

    # --- 6 flux towers ---
    for i in range(6):
        records.append(_flux(i))

    # --- 6 biomass pixels: GEDI vs CCI, framework tags recorded ---
    for i in range(3):
        records.append(_biomass(i, biomass_source="gedi",
                                agb_uncertainty_mg_ha=round(rng.uniform(25, 60), 1),
                                uncertainty_framework="gedi-v2"))
    for i in range(3, 6):
        records.append(_biomass(i, biomass_source="cci",
                                agb_uncertainty_mg_ha=round(rng.uniform(30, 75), 1),
                                uncertainty_framework="cci-v6"))

    # --- 6 biochar stability annex ---
    for i in range(6):
        records.append(_biochar(i, ebc_cert_id=f"EBC-SYN-{i:03d}" if i % 2 == 0 else None))

    # --- 4 MRV-method runs ---
    records.append(_mrv(0))  # daycent-modeled, weak baseline: the phantom-credit lesson
    records.append(_mrv(1, mrv_method_class="direct-sampling",
                        analytical_method="dry-combustion",
                        baseline_definition="paired-control-plots",
                        additionality_claim="cover-crop-adoption",
                        additionality_evidence_level="measured",
                        baseline_credibility="measured",
                        permanence_evidence="measured"))
    records.append(_mrv(2, mrv_method_class="remote-sensing-hybrid",
                        baseline_definition="satellite-baseline-2015",
                        additionality_evidence_level="modeled",
                        baseline_credibility="regional-standard"))
    records.append(_mrv(3, mrv_method_class="registry-certified",
                        analytical_method="registry-reported",
                        baseline_definition="standardized-regional",
                        baseline_credibility="standardized-fiction",
                        permanence_evidence="buffered"))

    # --- 2 registry crosswalks ---
    records.append(_registry(0))
    records.append(_registry(1, region="SE-US",
                             additionality_claim="improved-forest-management"))

    return records


SEED_MEASUREMENTS: list[dict] = build_seed_records()


def build_benchmark_seed() -> dict:
    """MRV-BLIND v1 benchmark + 4 synthetic blind plots.

    Applied by the integration pass to sci_benchmarks / sci_carbon_blind_plots.
    Two plots carry dry-combustion endpoints (scorable); one is a planted
    known_reversion additionality-failure case.
    """
    plots = []
    specs = [
        ("BLIND-FL-01", "FL", "sandy-loam", "subtropical", True, False),
        ("BLIND-FL-02", "FL", "loamy-sand", "subtropical", True, True),   # reversion case
        ("BLIND-IA-01", "IA", "silt-loam", "temperate", False, False),     # not dry-combustion: unscorable
        ("BLIND-CA-01", "CA", "clay-loam", "mediterranean", True, False),
    ]
    for code, region, texture, climate, dc, rev in specs:
        plots.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.carbon.blind.{code}")),
                "plot_code": code,
                "region": region,
                "soil_texture": texture,
                "climate_zone": climate,
                "management_history": "cover-crop-3y" if not rev else "cover-crop-3y-then-tilled",
                "measurement_window_start": "2024-01-01",
                "measurement_window_end": "2026-01-01",
                "sealed_measurement_hash": hashlib.sha256(
                    f"twistor.carbon.sealed.{code}".encode()
                ).hexdigest(),
                "dry_combustion_endpoints": dc,
                "known_reversion": rev,
                "status": "open",
            }
        )
    return {
        "benchmark": {
            "lane": "carbon",
            "name": "mrv-blind_v1",
            "metric": "interval_calibrated_error_soc_change",
            "status": "open",
        },
        "plots": plots,
    }


def validate_seed() -> list[str]:
    """Validate every seed record against the ingest schema. Returns errors."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from schemas import IngestMeasurement  # noqa: E402

    errors = []
    for i, rec in enumerate(SEED_MEASUREMENTS):
        try:
            m = IngestMeasurement(**rec)
            if not m.synthetic:
                errors.append(f"record {i}: synthetic flag not set")
        except Exception as exc:
            errors.append(f"record {i}: {exc}")
    # benchmark seed sanity
    bench = build_benchmark_seed()
    if not bench["plots"]:
        errors.append("benchmark seed: no plots")
    codes = [p["plot_code"] for p in bench["plots"]]
    if len(set(codes)) != len(codes):
        errors.append("benchmark seed: duplicate plot codes")
    return errors


if __name__ == "__main__":
    errs = validate_seed()
    print(f"{len(SEED_MEASUREMENTS)} records, {len(errs)} errors")
    for e in errs:
        print("  ", e)
