# Prepared by Twistor Holdings LLC
"""TW-612 crops lane ingestors: real-data adapters, green sources first.

Ingestion sequence (Diego TW-610):
  1. G2F maize corpus      (GREEN, CC0) - CyVerse account (Justynn) + Figshare fast start
  2. GRIN/Genesys passport (GREEN, US-PD) - Genesys aggregator API / web Excel export
  3. MuST-C phenotyping    (GREEN, CC-BY) - wget, no auth
  4. Missouri + NC State hemp tables (GREEN) - scrapeable, machine-readable
  5. UVM + OSU extension PDFs (GREEN) - pdfplumber/tabula extraction
  6. AgroNT               (RED) - research only, NEVER in gold

The multi-year machine-readable hemp panel does not exist anywhere yet.
Building it from 4+5 is the product.
"""

from __future__ import annotations

import logging

from .bronze import IngestItem
from .licenses import KNOWN_LICENSES

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- G2F
# CyVerse Data Commons: per-year versioned DOIs (account required, Justynn).
# Figshare aggregate: fast start, no auth.
G2F_FIGSHARE_DOI = "10.6084/m9.figshare.22776806"
G2F_CYVERSE = "https://data.cyverse.org/dav-anon/iplant/home/shared/genomes2fields"


def plan_g2f_figshare() -> list[IngestItem]:
    """G2F aggregate via Figshare (no account). One artifact: the aggregate archive."""
    lic = KNOWN_LICENSES["g2f"]
    return [
        IngestItem(
            source_key="g2f",
            remote_url="https://figshare.com/ndownloader/articles/22776806/versions/1",
            filename="g2f/g2f_aggregate_figshare.zip",
            license=lic,
            metadata={
                "doi": G2F_FIGSHARE_DOI,
                "note": "Fast start. Full per-year corpus via CyVerse (account required).",
                "harmonization": "raw and unharmonized; free-text management fields are ours to clean",
            },
        )
    ]


def plan_g2f_cyverse(years: list[int]) -> list[IngestItem]:
    """G2F per-year corpus via CyVerse (REQUIRES CyVerse account: Justynn creates it)."""
    lic = KNOWN_LICENSES["g2f"]
    return [
        IngestItem(
            source_key="g2f",
            remote_url=f"{G2F_CYVERSE}/g2f_{year}_data.zip",
            filename=f"g2f/cyverse/g2f_{year}.zip",
            license=lic,
            metadata={
                "year": year,
                "doi": "per-year versioned DOI via CyVerse",
                "note": "CyVerse account required; bulk via iCommands/Cyberduck. "
                "Worker uses stored CyVerse credentials.",
            },
        )
        for year in years
    ]


# ---------------------------------------------------------------- Genesys / GRIN
# Public portal API. Endpoint pattern verified 2026-10-18: api.genesys-pgr.org
# serves the portal; GGCE /api/v2 is the authenticated genebank-system API.
# Diego: "Genesys API ToU review at integration time."
GENESYS_API = "https://api.genesys-pgr.org"


def plan_genesys(crop: str = "cannabis") -> list[IngestItem]:
    """GRIN/Genesys passport anchors. Falls back to web Excel export.

    NOTE (integration time): confirm the public search endpoint path and ToU
    before first production pull. The adapter is written against the portal
    search; if the path 404s, use the documented web Excel export instead.
    """
    lic = KNOWN_LICENSES["grin-genesys"]
    return [
        IngestItem(
            source_key="grin-genesys",
            remote_url=f"{GENESYS_API}/api/v1/accessions?crop={crop}&limit=10000",
            filename=f"genesys/passport_{crop}.json",
            license=lic,
            metadata={
                "crop": crop,
                "accessions_expected": "620K+ total; hemp-taxon coverage "
                "unverified (Diego open verification) -- record the hit count at pull time",
                "fallback": "web Excel export from genesys-pgr.org",
                "tou": "review at integration time",
            },
        )
    ]


# ---------------------------------------------------------------- MuST-C
MUSTC_BASE = "https://www.ipb.uni-bonn.de/data/MuST-C"
MUSTC_DOI = "10.60507/FK2/OX9XTM"


def plan_mustc() -> list[IngestItem]:
    """MuST-C multi-sensor phenotyping (CC-BY, no auth). Provenance template."""
    lic = KNOWN_LICENSES["must-c"]
    return [
        IngestItem(
            source_key="must-c",
            remote_url=f"{MUSTC_BASE}/mustc_sample_5gb.tar.gz",
            filename="mustc/mustc_sample.tar.gz",
            license=lic,
            metadata={
                "doi": MUSTC_DOI,
                "note": "Six crops, no hemp: phenotyping-provenance template, not product data. "
                "Full corpus via bonndata repo wget scripts.",
            },
        )
    ]


# ---------------------------------------------------------------- Hemp trial tables (the product substrate)
MISSOURI_HEMP = (
    "https://www.frontiersin.org/journals/agronomy/"
    "articles/10.3389/fagro.2026.1745927/full"
)
NCSTATE_HEMP = "https://content.ces.ncsu.edu/fiber-hemp-variety-trial-results-2023-2024"


def plan_hemp_tables() -> list[IngestItem]:
    """Missouri (3 sites, 32 cultivars, HPLC cannabinoid panel) + NC State
    (Cornell-led multi-state 2023-2024) HTML tables. The only machine-readable
    hemp trial data on the internet."""
    lic = KNOWN_LICENSES["hemp-missouri-ncstate"]
    return [
        IngestItem(
            source_key="hemp-missouri-ncstate",
            remote_url=MISSOURI_HEMP,
            filename="hemp/missouri_2026_supplementary.html",
            license=lic,
            metadata={
                "sites": 3,
                "cultivars": 32,
                "panel": "HPLC cannabinoid",
                "extraction": "supplementary tables; worker parses HTML tables to silver",
            },
        ),
        IngestItem(
            source_key="hemp-missouri-ncstate",
            remote_url=NCSTATE_HEMP,
            filename="hemp/ncstate_2023_2024.html",
            license=lic,
            metadata={
                "program": "Cornell-led multi-state fiber hemp variety trial",
                "years": "2023-2024",
                "extraction": "HTML tables; worker parses to silver",
            },
        ),
    ]


# ---------------------------------------------------------------- UVM / OSU PDFs
UVM_PDF = (
    "https://www.uvm.edu/d10-files/documents/2026-03/2025-Hemp-Fiber-VT-Report.pdf"
)
OSU_PDF = (
    "https://extension.oregonstate.edu/catalog/"
    "em-9434-comparison-stem-fiber-yield-industrial-trial"
)


def plan_hemp_pdfs() -> list[IngestItem]:
    """UVM + Oregon State extension PDFs. Text-based: pdfplumber/tabula, no OCR.
    Multi-year backfill becomes our proprietary digitized panel."""
    lic = KNOWN_LICENSES["hemp-uvm-osu-pdf"]
    return [
        IngestItem(
            source_key="hemp-uvm-osu-pdf",
            remote_url=UVM_PDF,
            filename="hemp/uvm_2025_fiber_vt.pdf",
            license=lic,
            metadata={
                "program": "UVM hemp fiber VT report 2025",
                "extraction": "pdfplumber/tabula; tables -> silver trial rows",
            },
        ),
        IngestItem(
            source_key="hemp-uvm-osu-pdf",
            remote_url=OSU_PDF,
            filename="hemp/osu_em9434.pdf",
            license=lic,
            metadata={
                "program": "OSU EM-9434 stem fiber yield industrial trial",
                "extraction": "pdfplumber/tabula; tables -> silver trial rows",
            },
        ),
    ]


# ---------------------------------------------------------------- RED: excluded
def agront_excluded() -> dict:
    """AgroNT weights are CC BY-NC-SA 4.0 (InstaDeep): RED for commercial.
    Documented here so the exclusion is explicit, not an oversight."""
    return {
        "source": "agront",
        "status": "excluded_from_gold",
        "license": KNOWN_LICENSES["agront"].tag,
        "rule": "Research/prototype use only. Revenue use needs a license. "
        "Do not let NC weights leak into any commercial artifact.",
    }


# ---------------------------------------------------------------- silver mapping
CROPS_SILVER_MAPPING = {
    "g2f": {
        "note": "CC0 maize phenotypes -> climate-shift benchmark substrate. "
        "Harmonize free-text management fields; genotype IDs anchor to GRIN.",
    },
    "grin-genesys": {
        "note": "Passport anchors: accession ID, taxon, origin. Genotype-identity anchor rows.",
    },
    "must-c": {
        "note": "Phenotyping-provenance template only: sensor lineage pattern, not product rows.",
    },
    "hemp-missouri-ncstate": {
        "trait_panels": ["THC", "CBD", "CBG"],
        "note": "Trial rows: cultivar x site x year with cannabinoid panel. "
        "Lab accreditation + COA hash carried per CLIMATESHIFT-1 v1 schema.",
    },
    "hemp-uvm-osu-pdf": {
        "note": "Extracted trial tables appended to the hemp panel. "
        "The multi-year machine-readable hemp dataset does not exist yet: this is it.",
    },
}
