# Prepared by Twistor Holdings LLC
"""TW-612 carbon lane ingestors: real-data adapters, green sources first.

Ingestion sequence (Diego TW-610 / TW-600):
  1. ISRIC WoSIS          (YELLOW) - public tier -> gold; restricted tier -> flagged partition
  2. AmeriFlux / FLUXNET  (GREEN, CC-BY-4.0) - FLUXDATA account (Justynn)
  3. NASA GEDI            (GREEN) - ORNL DAAC, free
  4. ESA CCI Biomass v6   (GREEN) - open data portal
  5. USDA SSURGO          (GREEN, US-PD) - Web Soil Survey
  6. Registry crosswalk   (YELLOW) - silver only until independently replicated

Method-code comparability gating is the trust mechanism: Walkley-Black and
dry combustion never share a comparable group. The ingestor records the
analytical method on every row; rows with unknown methods quarantine to
provenance_incomplete and never reach gold.
"""

from __future__ import annotations

import logging

from .bronze import IngestItem
from .licenses import KNOWN_LICENSES

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- WoSIS
WOSIS_GRAPHQL = "https://graphql.isric.org/wosis/graphql"
# WoSIS attribute codes for carbon (Batjes et al. 2017, Table C1)
WOSIS_CARBON_ATTRS = ("ORGC", "TOTC")  # organic carbon, total carbon g/kg


def wosis_profiles_query(first: int = 100, after: str | None = None) -> dict:
    """Build a WoSIS GraphQL query for carbon-bearing profiles.

    The license tier rides the datasetCode: public-tier datasets -> GREEN,
    restricted-tier -> RED (flagged partition). The worker resolves each
    profile's dataset license tier at pull time and tags accordingly.
    """
    after_clause = f', after: "{after}"' if after else ""
    return {
        "query": (
            "query WosisCarbon($first: Int!) {"
            " wosisLatestProfiles(first: $first"
            + after_clause
            + ", filter: {layersExist: true}) {"
            " profileId datasetCode latitude longitude countryCode"
            " layers { upperDepth lowerDepth observedProperty value unit"
            "          methodCode methodDescription }"
            " } }"
        ),
        "variables": {"first": first},
    }


def plan_wosis(pages: int = 1, page_size: int = 100) -> list[IngestItem]:
    """WoSIS carbon profiles. YELLOW: the worker checks each profile's
    dataset license tier; public -> wosis-public (GREEN), restricted ->
    wosis-restricted (RED, flagged partition, never gold)."""
    return [
        IngestItem(
            source_key="wosis-public",
            remote_url=WOSIS_GRAPHQL,
            filename=f"wosis/wosis_carbon_page{i:04d}.json",
            license=KNOWN_LICENSES["wosis-public"],
            metadata={
                "page": i,
                "page_size": page_size,
                "query": "wosisLatestProfiles layersExist, ORGC/TOTC layers",
                "license_check": "per-profile datasetCode tier: public->green, restricted->red. "
                "Restricted profiles re-tagged to wosis-restricted at write time.",
                "method_gating": "methodCode preserved per layer; unknown -> provenance_incomplete",
            },
        )
        for i in range(pages)
    ]


# ---------------------------------------------------------------- AmeriFlux (account-gated)
AMERIFLUX_BASE = "https://ameriflux.lbl.gov"


def plan_ameriflux(site_ids: list[str]) -> list[IngestItem]:
    """AmeriFlux tower data (CC-BY-4.0, per-site DOIs). REQUIRES FLUXDATA account (Justynn)."""
    lic = KNOWN_LICENSES["ameriflux"]
    return [
        IngestItem(
            source_key="ameriflux",
            remote_url=f"{AMERIFLUX_BASE}/data/download/{site}",
            filename=f"ameriflux/{site}.zip",
            license=lic,
            metadata={
                "site": site,
                "note": "FLUXDATA account required. Worker uses stored credentials. "
                "Eddy-covariance flux: the ground-truth carbon flux record.",
            },
        )
        for site in site_ids
    ]


# ---------------------------------------------------------------- GEDI (ORNL DAAC)
# GEDI L4B gridded aboveground biomass density. ORNL DAAC, free, no auth for
# most holdings (Earthdata login for some; the L4B grids are open).
GEDI_DAAC = "https://daac.ornl.gov"
GEDI_L4B = f"{GEDI_DAAC}/cgi-bin/dataset_lister.pl?p=40"


def plan_gedi(tiles: list[str]) -> list[IngestItem]:
    """NASA GEDI L4B biomass tiles. Carry the GEDI/CCI uncertainty-framework
    misalignment (Hunka et al. 2023) as an explicit tag, not a hidden merge."""
    lic = KNOWN_LICENSES["gedi"]
    return [
        IngestItem(
            source_key="gedi",
            remote_url=f"{GEDI_L4B}&tile={tile}",
            filename=f"gedi/gedi_l4b_{tile}.tif",
            license=lic,
            metadata={
                "tile": tile,
                "product": "GEDI L4B aboveground biomass density",
                "uncertainty_framework": "gedi-v2",
                "misalignment_tag": "gedi-v2 vs cci-v6: documented misalignment, "
                "carried explicitly per record",
            },
        )
        for tile in tiles
    ]


# ---------------------------------------------------------------- ESA CCI Biomass
ESA_CCI = "https://climate.esa.int/en/projects/biomass"


def plan_esa_cci(years: list[int]) -> list[IngestItem]:
    """ESA CCI Biomass v6, 2007-2022, 100m AGB. Open data portal."""
    lic = KNOWN_LICENSES["esa-cci-biomass"]
    return [
        IngestItem(
            source_key="esa-cci-biomass",
            remote_url=f"{ESA_CCI}/data/v6/{year}",
            filename=f"esa_cci/biomass_v6_{year}.tif",
            license=lic,
            metadata={
                "year": year,
                "version": "v6",
                "resolution_m": 100,
                "uncertainty_framework": "cci-v6",
            },
        )
        for year in years
    ]


# ---------------------------------------------------------------- SSURGO
SSURGO_WSS = "https://websoilsurvey.sc.egov.usda.gov"


def plan_ssurgo(survey_areas: list[str]) -> list[IngestItem]:
    """USDA SSURGO soil survey exports (US-PD). Farm-scale CONUS baseline."""
    lic = KNOWN_LICENSES["ssurgo"]
    return [
        IngestItem(
            source_key="ssurgo",
            remote_url=f"{SSURGO_WSS}/api/survey/{area}",
            filename=f"ssurgo/{area}.zip",
            license=lic,
            metadata={
                "survey_area": area,
                "note": "CONUS soil survey; organic-matter / carbon fields harmonized to silver",
            },
        )
        for area in survey_areas
    ]


# ---------------------------------------------------------------- Registry crosswalk (YELLOW, silver only)
def plan_registry_crosswalk(
    registries: tuple[str, ...] = ("puro", "isometric", "carbonfuture"),
) -> list[IngestItem]:
    """Puro / Isometric / Carbonfuture public data. YELLOW: silver only until
    independently replicated. Model-only MRV claims with no measurement
    anchor are excluded from gold by construction."""
    lic = KNOWN_LICENSES["registry-crosswalk"]
    return [
        IngestItem(
            source_key="registry-crosswalk",
            remote_url=f"https://registry.twistor.co/crosswalk/{reg}",
            filename=f"registry/{reg}_public.json",
            license=lic,
            metadata={
                "registry": reg,
                "credibility": "registry-reported",
                "rule": "silver only; gold requires independent measurement replication",
            },
        )
        for reg in registries
    ]


# ---------------------------------------------------------------- silver mapping
CARBON_SILVER_MAPPING = {
    "wosis-public": {
        "record_class": "soil-profile",
        "carbon_pool": "soil-organic",
        "method_gating": "methodCode -> comparable_group; Walkley-Black vs dry-combustion never shared",
        "note": "The canonical demonstration: legacy/resample pairs on the same site "
        "(e.g. 1996 Walkley-Black + 2024 dry combustion) stay comparable-group-separated.",
    },
    "ameriflux": {
        "record_class": "flux-tower",
        "carbon_pool": "soil-organic",
        "note": "NEE partitioned fluxes; per-site DOI carried as the truth anchor.",
    },
    "gedi": {
        "record_class": "biomass-pixel",
        "carbon_pool": "aboveground-biomass",
        "uncertainty_framework": "gedi-v2",
    },
    "esa-cci-biomass": {
        "record_class": "biomass-pixel",
        "carbon_pool": "aboveground-biomass",
        "uncertainty_framework": "cci-v6",
    },
    "ssurgo": {
        "record_class": "soil-profile",
        "carbon_pool": "soil-organic",
        "note": "Map-unit aggregates; component-level carbon fields harmonized.",
    },
    "registry-crosswalk": {
        "record_class": "mrv-method-run",
        "note": "Silver only. Baseline credibility: registry-reported until replicated.",
    },
}
