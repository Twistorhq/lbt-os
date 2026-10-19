# Prepared by Twistor Holdings LLC
# TW-612 TDD Evidence: Real-data ingestors (Wave 1)

## What was built

Shared ingestion framework + lane adapters for all three Wave 1 lanes,
replacing synthetic seeds with real open data per Diego's TW-610 plan.

### Files
- `backend/app/data_layer/science/ingest/__init__.py` — lane constants
- `backend/app/data_layer/science/ingest/licenses.py` — license verdicts
  (GREEN/YELLOW/RED), 17-source registry, gold-promotion gate
- `backend/app/data_layer/science/ingest/bronze.py` — BronzeWriter:
  streaming fetch, sha256, idempotent re-runs, per-item fault isolation (TW-208)
- `backend/app/data_layer/science/ingest/s2s.py` — GEFS, 20CRv3, ERA5/WB2,
  S2S CC-BY whitelist (NC centers raise before any item is built), YELLOW checks
- `backend/app/data_layer/science/ingest/crops.py` — G2F (Figshare + CyVerse),
  Genesys, MuST-C, Missouri/NC State hemp tables, UVM/OSU PDFs, AgroNT exclusion
- `backend/app/data_layer/science/ingest/carbon.py` — WoSIS GraphQL, AmeriFlux,
  GEDI, ESA CCI, SSURGO, registry crosswalk (YELLOW, silver-only)
- `backend/requirements-ingest.txt` — worker deps (FOSS, $0)
- `supabase/migration_sci_ingest_v1.sql` — license columns on bronze +
  `sci_bronze_gold_eligible` view (GREEN only)
- `backend/tests/test_science_ingest.py` — 31 tests

### Verification
- 41/41 new tests green; 137/137 with carbon + subseasonal + paywall suites
- ruff check + format clean
- Manual spot-checks (2026-10-18, not committed scripts): real GEFS .idx
  bytes fetched from live S3 (noaa-gefs-retrospective) -> bronze,
  sha256-hashed, US-PD/green tagged; WoSIS GraphQL returned real profiles;
  WB2 ERA5 Zarr store reachable in GCS; 20CRv3 tarball URL pattern matches
  NERSC portal docs. Plausible and URL-exact, but label them honestly:
  spot-checks, not automated proveout.
- Registry: 21 sources (7 S2S + 6 crops + 8 carbon), all pinned in tests.

### License discipline (the money rule)
Gold admits GREEN only, enforced in code (can_promote_to_gold,
assert_gold_eligible, gold_gate re-resolves at promotion time, write()
validates declared licenses against the registry). The migration adds a
CHECK on the verdict enum plus a `sci_bronze_gold_eligible` view (GREEN
only) for future minting code to join against.

HONEST SCOPE (Rosa TW-612 round 1): the DB does NOT yet enforce license
lineage into `sci_dataset_versions` — the paywalled surface served by
paywall.py has no license FK or check. The enforcement today is
application-level. Follow-up ticketed: carry license lineage into dataset
versions (mint-time assertion or version->artifacts join).

### Account-gated sources (need Justynn to create)
- ECDS account (S2S database) — free
- CyVerse account (G2F bulk corpus) — free
- FLUXDATA account (AmeriFlux) — free
- CDS account (ERA5 API fallback) — free

### Open follow-ups
- Silver harmonization workers (numeric extraction per source) — next ticket
- YELLOW checks: AI Weather Quest bulk endpoint, WB2 IFS LICENSE files,
  Genesys endpoint/ToU at integration time, PGB license verification
- TW-609: legacy lanes still on uncapped bulk toll (out of scope)
