# Prepared by Twistor Holdings LLC
"""TW-612 license verdicts: the tag that rides every record.

GREEN  = clean for commercial/paywalled use (public domain, CC0, CC-BY,
         US federal data, open redistribution terms).
YELLOW = usable only after a per-source check Dre performs at ingest time
         (tiered licenses, attribution requirements, LICENSE-file review).
RED    = excluded from any revenue-touching product. May live in bronze for
         research/provenance, NEVER in gold, NEVER behind the paywall.

The paywall must never sell what the license does not allow. Gold promotion
is refused for anything that is not GREEN -- enforced in code
(can_promote_to_gold, assert_gold_eligible, BronzeWriter.gold_gate, and
write()-time registry validation). The migration constrains the verdict
enum and exposes a GREEN-only view; full license lineage into dataset
versions is a follow-up (the paywalled surface is application-gated today).
"""

from __future__ import annotations

from dataclasses import dataclass

VERDICT_GREEN = "green"
VERDICT_YELLOW = "yellow"
VERDICT_RED = "red"

VERDICTS = (VERDICT_GREEN, VERDICT_YELLOW, VERDICT_RED)


@dataclass(frozen=True)
class LicenseTag:
    """The license identity of one ingested source artifact."""

    tag: str  # e.g. "CC0-1.0", "CC-BY-4.0", "CC-BY-NC-4.0", "US-PD", "custom"
    verdict: str  # green | yellow | red
    source_url: str  # canonical source page, for the published whitelist
    notes: str = ""  # the specific check performed / restriction observed

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if not self.tag:
            raise ValueError("license tag must be non-empty")
        if not self.source_url:
            raise ValueError("source_url must be non-empty: the whitelist is published")


def can_promote_to_gold(tag: LicenseTag) -> bool:
    """Gold (the paywalled tier) admits GREEN licenses only. No exceptions."""
    return tag.verdict == VERDICT_GREEN


def assert_gold_eligible(tag: LicenseTag, *, context: str = "") -> None:
    """Raise if a record carrying this license tag must not reach gold."""
    if not can_promote_to_gold(tag):
        where = f" ({context})" if context else ""
        raise LicenseQuarantineError(
            f"license {tag.tag} [{tag.verdict}] is quarantined out of gold{where}: "
            f"{tag.notes}"
        )


class LicenseQuarantineError(RuntimeError):
    """Raised when NC/restricted/unknown-license data attempts gold promotion."""


# ---------------------------------------------------------------------------
# Registry of known source licenses (Diego TW-610 brief). The ingestor for
# each source references its entry here so the verdict is declared once,
# in one place, reviewable by Rosa.
# ---------------------------------------------------------------------------

KNOWN_LICENSES: dict[str, LicenseTag] = {}


def register(key: str, tag: LicenseTag) -> LicenseTag:
    KNOWN_LICENSES[key] = tag
    return tag


# -- S2S lane ---------------------------------------------------------------
register(
    "gefs-reforecast",
    LicenseTag(
        tag="US-PD",
        verdict=VERDICT_GREEN,
        source_url="https://psl.noaa.gov/forecasts/reforecast2/wx_maps/index.html",
        notes="US federal data, no usage restrictions, attribution requested. S3: noaa-gefs-retrospective, no sign-in.",
    ),
)
register(
    "20crv3",
    LicenseTag(
        tag="public-no-restrictions",
        verdict=VERDICT_GREEN,
        source_url="https://portal.nersc.gov/project/20C_Reanalysis/",
        notes="Stated 'Usage Restrictions: None.' Mirrors at PSL/NCAR/NERSC, no auth.",
    ),
)
register(
    "era5-wb2",
    LicenseTag(
        tag="Copernicus-adapted",
        verdict=VERDICT_GREEN,
        source_url="https://weatherbench2.readthedocs.io/en/latest/data-guide.html",
        notes="WB2 GCS Zarr copy. Copernicus licence permits redistribution of adapted products with attribution. "
        "Carry provenance caveats: ERA5T ~5d lag; ERA5 precipitation is modelled, not assimilated.",
    ),
)
register(
    "s2s-db-ccby",
    LicenseTag(
        tag="CC-BY-4.0",
        verdict=VERDICT_GREEN,
        source_url="https://ecds.ecmwf.int/datasets/s2s-forecasts?tab=overview",
        notes="Whitelist centers only: ECMWF, NCEP, KMA, ECCC, UKMO, HMCR, CPTEC. "
        "NC centers (BoM, CMA, CNR-ISAC, CNRM, IAP-CAS, JMA) are RED and excluded by the ingestor.",
    ),
)
register(
    "s2s-db-ccbync",
    LicenseTag(
        tag="CC-BY-NC-4.0",
        verdict=VERDICT_RED,
        source_url="https://confluence.ecmwf.int/pages/viewpage.action?pageId=639218258",
        notes="BoM, CMA, CNR-ISAC, CNRM, IAP-CAS, JMA. Research use only; must never sit behind the paywall.",
    ),
)
register(
    "ai-weather-quest",
    LicenseTag(
        tag="Apache-2.0",
        verdict=VERDICT_YELLOW,
        source_url="https://ecmwf-ai-weather-quest.readthedocs.io/en/latest/",
        notes="Package is Apache-2.0; bulk scores endpoint unverified. Check package docs for a download function before scraping.",
    ),
)
register(
    "wb2-ifs-baseline",
    LicenseTag(
        tag="per-dataset",
        verdict=VERDICT_YELLOW,
        source_url="https://weatherbench2.readthedocs.io/en/latest/data-guide.html",
        notes="WB2: 'Some datasets allow commercial use. Others only permit research use.' "
        "Read each dataset LICENSE file in the bucket before ingesting.",
    ),
)

# -- Crops lane --------------------------------------------------------------
register(
    "g2f",
    LicenseTag(
        tag="CC0-1.0",
        verdict=VERDICT_GREEN,
        source_url="https://www.genomes2fields.org/resources",
        notes="Phenotypes/agronomic/metadata under CC0 public domain dedication. CyVerse account required for bulk.",
    ),
)
register(
    "grin-genesys",
    LicenseTag(
        tag="US-PD",
        verdict=VERDICT_GREEN,
        source_url="https://catalog.data.gov/dataset/national-plant-germplasm-system-9c1dd",
        notes="USDA NPGS passport data, US public domain. Bulk via Genesys aggregator REST API.",
    ),
)
register(
    "must-c",
    LicenseTag(
        tag="CC-BY-4.0",
        verdict=VERDICT_GREEN,
        source_url="https://www.ipb.uni-bonn.de/data/MuST-C/",
        notes="Multi-sensor phenotyping, CC BY 4.0, no auth. Phenotyping-provenance template (no hemp).",
    ),
)
register(
    "hemp-missouri-ncstate",
    LicenseTag(
        tag="open-scrapeable",
        verdict=VERDICT_GREEN,
        source_url="https://www.frontiersin.org/journals/agronomy/articles/10.3389/fagro.2026.1745927/full",
        notes="Missouri Frontiers in Agronomy 2026 supplementary tables + NC State Extension HTML tables. "
        "Published trial data, machine-readable, no redistribution restriction stated.",
    ),
)
register(
    "hemp-uvm-osu-pdf",
    LicenseTag(
        tag="open-extension",
        verdict=VERDICT_GREEN,
        source_url="https://www.uvm.edu/d10-files/documents/2026-03/2025-Hemp-Fiber-VT-Report.pdf",
        notes="Public extension PDFs. Facts extracted (not creative expression); digitized panel is our compilation.",
    ),
)
register(
    "agront",
    LicenseTag(
        tag="CC-BY-NC-SA-4.0",
        verdict=VERDICT_RED,
        source_url="https://huggingface.co/InstaDeepAI/agro-nucleotide-transformer-1b",
        notes="InstaDeep weights NC-SA. Research/prototype use only; revenue use needs a license. Never in gold.",
    ),
)

# -- Carbon lane ---------------------------------------------------------------
register(
    "wosis-public",
    LicenseTag(
        tag="CC-BY-4.0",
        verdict=VERDICT_GREEN,
        source_url="https://www.isric.org/accessing-wosis-using-r",
        notes="Public-tier WoSIS records only (datasetCode in the public tier). "
        "Assigned per-profile by retier_wosis_artifact(), never at page plan time.",
    ),
)
register(
    "wosis-untiered",
    LicenseTag(
        tag="tier-pending",
        verdict=VERDICT_YELLOW,
        source_url="https://www.isric.org/accessing-wosis-using-r",
        notes="Page-level artifact before per-profile tier resolution. YELLOW until "
        "retier_wosis_artifact() splits public (GREEN) from restricted (RED). "
        "Never promotable to gold in this state.",
    ),
)
register(
    "wosis-restricted",
    LicenseTag(
        tag="restricted",
        verdict=VERDICT_RED,
        source_url="https://www.isric.org/accessing-wosis-using-r",
        notes="Standardized but not redistributable. Flagged partition in bronze/silver only; never gold.",
    ),
)
register(
    "ameriflux",
    LicenseTag(
        tag="CC-BY-4.0",
        verdict=VERDICT_GREEN,
        source_url="https://ameriflux.lbl.gov/about/about-ameriflux/",
        notes="800+ towers, per-site DOIs. FLUXDATA account required.",
    ),
)
register(
    "gedi",
    LicenseTag(
        tag="open-nasa",
        verdict=VERDICT_GREEN,
        source_url="https://daac.ornl.gov/cgi-bin/dataset_lister.pl?p=40",
        notes="NASA open data via ORNL DAAC, free. Carry GEDI/CCI uncertainty-framework misalignment as an explicit tag.",
    ),
)
register(
    "esa-cci-biomass",
    LicenseTag(
        tag="open-esa",
        verdict=VERDICT_GREEN,
        source_url="https://climate.esa.int/",
        notes="ESA CCI Biomass v6, 2007-2022, 100m, open data portal.",
    ),
)
register(
    "ssurgo",
    LicenseTag(
        tag="US-PD",
        verdict=VERDICT_GREEN,
        source_url="https://websoilsurvey.sc.egov.usda.gov/",
        notes="USDA soil survey, US federal data, no restrictions.",
    ),
)
register(
    "registry-crosswalk",
    LicenseTag(
        tag="registry-reported",
        verdict=VERDICT_YELLOW,
        source_url="https://www.twistor.co",
        notes="Puro/Isometric/Carbonfuture public data, clearly labeled registry-reported. "
        "Silver only until independently replicated; model-only claims with no measurement anchor excluded from gold.",
    ),
)
