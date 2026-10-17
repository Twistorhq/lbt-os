# TW-567: Public university data harvester (hemp composite corpus seed)

**Prepared by Twistor Holdings LLC**

- Ticket: TW-567 (brief called it TW-566; ticket system assigned TW-567) · Reporter: Justynn · Owner: Zeke Okafor (Lead Build Engineer)
- Branch: `zeke/tw-567-harvester` · Base: `main` @ `f6b51bb`
- Status: proposal, pre-implementation

## Problem

The data-layer program (TW-563) needs a seed corpus for the hemp/natural-fiber
composite data asset (niche #2), and Justynn wants theory proven from public
university output first: white papers, dissertations, published datasets, all
in one normalized store. Today that knowledge is scattered across publisher
APIs and university repositories with no Twistor-owned aggregation. The
prior-art sweep (TW-562) confirmed the white space: nobody owns the
hemp/natural-fiber training data. This ticket builds the harvester that starts
owning it.

## Scope (this slice)

Phase 0 only: discover, fetch, normalize, store. No API, no serving layer
(those belong to TW-564 after Rosa clears its revision). Public data only:
open APIs (Crossref, OpenAIRE, DOAJ, arXiv, Semantic Scholar enrichment,
OpenAlex with graceful degradation) and university OAI-PMH repositories.
No paywalled content, no ToS violations, robots.txt honored, rate-limited.

Out of scope: lab-contributed data ingestion (schema reserves for it),
PDF layout analysis beyond text extraction, the MRV/telemetry niches.

## Proposed change

A self-contained harvester package at `research/harvester/`:

1. **Pluggable source adapters** (`adapters/`) behind one `SourceAdapter`
   interface: `discover(query) -> [RawRecord]`, `fetch_fulltext(record)`.
   Ten enabled sources in `sources.yaml`; each entry records verification
   status and politeness parameters.
2. **Politeness layer** (`politeness.py`): per-host rate limiting, robots.txt
   check before any PDF fetch, identifying User-Agent with contact email.
   A source that 429s degrades to skipped-with-note, never retries hot.
3. **Metadata + property extraction** (`extract.py`): normalize title,
   authors, institution, year, DOI, license; heuristic regex extraction of
   material-property values (tensile/flexural strength and modulus, impact,
   elongation, density, fiber fraction) with units normalized (GPa -> MPa)
   and confidence labeled. Heuristic output is always marked
   `extraction_method='regex-heuristic'`, never presented as ground truth.
4. **Normalized store** (`store.py`): SQLite `research_corpus` with
   `sources`, `harvest_runs`, `documents`, `property_records`. Dedupe on
   external ID and DOI. `origin` column defaults to `'harvested'` so
   lab-contributed rows can join later without migration.
5. **Provenance on every record**: source URL, retrieval timestamp,
   license/access status. Harvest runs are logged with counts and notes.

## Acceptance

- `python -m research.harvester.cli run --query "hemp fiber composite"`
  completes against enabled sources and stores normalized documents.
- Tests cover: rate limiter, robots handling, property extraction,
  normalization, store dedupe, one adapter per family (API JSON, OAI-PMH XML).
- `pytest` green, `ruff` clean, TDD evidence report at
  `docs/evidence/TW-567-harvester.tdd.md`.
- Rosa reviews the diff before merge. Nothing merges on this ticket alone.

## Risks

- Sandbox egress IP has exhausted OpenAlex's free daily budget (429):
  adapter must degrade gracefully; real volume comes from Justynn's network.
- OAI-PMH sets vary by repository; adapter pages with resumption tokens and
  caps per-run record counts so one giant set cannot stall a run.
- Heuristic property extraction is lossy by design; confidence scores and
  the `regex-heuristic` label keep it honest.
