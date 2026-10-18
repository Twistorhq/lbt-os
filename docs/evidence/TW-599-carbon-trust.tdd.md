# TDD Evidence: CARBON-TRUST v1 (TW-599 Wave 1)

**Prepared by Twistor Holdings LLC**
**Dre Coleman — 2026-10-18. Branch: dre/tw-599-carbon-trust**

Diego's TW-600 spec: the harmonized carbon-sequestration evidence layer —
soil profiles + flux towers + biomass pixels + biochar stability annex +
MRV-method runs + registry crosswalks — with method-code comparability
gating, plus MRV-BLIND, the blind predict-then-measure challenge for
soil-carbon MRV methods. Folded into Wave 1 (TW-599) alongside S2S-TRUST v1
and CLIMATESHIFT-1 v1, with the Wave 1 day-one paywall.

## RED

- Method-code comparability is the trust mechanism: without it, Walkley-Black
  and dry-combustion records get silently pooled and the layer repeats the
  literature's incomparability failure. Written as `method_comparable()` in
  models.py with a test asserting Walkley-Black vs dry-combustion never
  pool and 'unknown' is never comparable with anything.
- MRV-BLIND scoring without the dry-combustion gate would score model-only
  claims — the phantom-credit failure mode. Written as
  `score_mrv_blind()` with a test asserting a wildly wrong prediction on a
  non-dry-combustion plot is excluded from scoring.
- Ingest without a method-code gate would admit 'unknown' methods into
  silver silently. Written as a quarantine test first.

## GREEN

- `backend/app/data_layer/science/carbon/`:
  - `models.py` — SilverMeasurement/GoldRow pydantic types, provenance walk,
    immutability guard, correction builder, method_comparable(),
    MRV-BLIND scoring (interval_score + coverage_penalty - reversion_bonus).
  - `schemas.py` — ingest/read schemas + ScienceEnvelope §4 contract.
  - `router.py` — /datasets, /datasets/{version}, /query (record_class /
    carbon_pool / analytical_method / comparable_group / region filters),
    /benchmarks, /benchmarks/mrv-blind/plots, /benchmarks/mrv-blind/submit,
    /ingest, /datasets/{version}/download. Shared paywall wiring.
  - `seed.py` — 36 synthetic records across all 6 record classes, 0 errors;
    benchmark seed: 4 blind plots (3 dry-combustion-anchored, 1 planted
    known_reversion).
- `supabase/migration_sci_carbon_v1.sql` — silver/gold lane tables, gold
  immutability trigger, RLS, sci_carbon_blind_plots + submissions tables,
  gold CHECK (analytical_method <> 'unknown').
- main.py: router mounted at /data/v1/science/carbon.
- Tests: backend/tests/test_carbon.py — 44/44 green.
- Suite: 174/174 green across carbon + subseasonal + paywall + electro +
  pfas + high_tc test files. Ruff clean on all touched files.

## REFACTOR

- One silver table with a record_class discriminator (soil/flux/biomass/
  biochar/MRV/registry) instead of six tables: the sibling-lane pattern,
  and class-specific fields are first-class columns where queried with
  JSONB overflow. Keeps the method-code gate in one place.
- MRV-BLIND scoring is a pure function over (predictions, revealed plots):
  testable without the consortium, and the consortium can re-run it.

## Paywall (TW-599, day one)

Shared science/paywall.py: subscription gate (402 fail-closed), dynamic
derivative API pricing (volume multiplier 1.0x-0.7x on trailing-30d spend),
Excel download fees $6.00/$8.50/$22.50 on top of subscription, large tier
API-only. V1 seed (36 rows) is small tier: API + Excel.

## Experimental-gold interlab tier

Stays FLAGGED for the UF/IFAS consortium play per the task brief.
Engineering only on this build: no interlab campaign code, no lab outreach.

## Verification

- `python3 -m pytest tests/test_carbon.py` → 44 passed.
- Cross-lane suite → 174 passed.
- `ruff check` → all checks passed.
- `python3 app/data_layer/science/carbon/seed.py` → 36 records, 0 errors.
