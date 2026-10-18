# TDD Evidence: TW-593 scientific data layer V1 (six lanes)

**Prepared by Twistor Holdings LLC**

## 1. Source
- Ticket TW-593 (Justynn 2026-10-18): Twistor as the trusted data layer for
  scientific AI. Six lanes from Diego's TW-592 brief: electrocatalysis,
  subseasonal prediction, turbulence closure, PFAS destruction catalysis,
  crop climate-shift generalization, high-Tc superconductors.
- Architecture contract:
  `~/workspace/twistor-lab/data-layer/science/TW-593-science-datalayer-architecture.md`
- Branch: `dre/tw-593-science-v1` (six lane branches merged in).
- Rosa review round 1 verdict: **REQUEST_CHANGES** (5 majors, minors). This
  document covers the fix round. Nothing merges without Justynn's word.

## 2. What was built (V1)
Shared trust primitives (`backend/app/data_layer/science/auth.py`,
core migration): lab registry + signing keys (`sci_api_keys`, sha256-hashed,
scoped `science:{lane}:write`, optional lab binding), calibration chains,
bronze artifacts first, silver FK provenance chain, immutable gold versions
(merkle roots), correction-as-supersession, reproducibility manifests,
quality attestations, blind benchmarks. Six lane routers mounted under
`/data/v1/science/{lane}/`, toll-metered via `config.TOLL_RATES`
(`data.science.query` $0.05, `.bulk` $0.25/1k rows, `.ingest` $0.10,
`.benchmark` $0.50). V1 seeds are synthetic-only and labeled.

## 3. Rosa's 5 majors — fix evidence
1. **Turbulence ingest dead on arrival** (string IDs into uuid PK, no
   artifact/lab FKs). Rewrote to the electrocatalysis pattern:
   `_verify_lab` → unsigned quarantine → bronze artifact first (uuid) →
   silver with real FKs, DB-default id. Schema updated
   (`content_hash`/`collector_signature`/`collected_at`; client `artifact_id`
   dropped). Tests: `test_ingest_accepts_and_dead_letters` asserts the bronze
   row exists after ingest (provenance chain intact).
2. **crop_climate on in-memory store in production.** Wrote
   `supabase_store.py` (`SupabaseCropClimateStore`, full router-facing
   contract against Postgres, reuses dataclasses + validation);
   `get_store()` returns it in production; tests override with the in-memory
   double. `sci_corrections` now links `new_dataset_version_id` properly.
3. **Electrocatalysis gold missing immutability trigger.** Appended the
   gold immutability trigger to `migration_sci_electrocatalysis_v1.sql`;
   covered by `test_migration_declares_gold_immutable_trigger`.
4. **high_tc benchmark runs don't enforce manifest.** `submit_run` now
   404s on unknown `manifest_id`; covered by
   `test_benchmark_submit_unknown_manifest_404`.
5. **Unauthenticated mutation endpoints.** New `auth.py`:
   `require_science_key` (X-API-Key → sha256 → `sci_api_keys` → scope check),
   `enforce_lab_binding`. Wired into all 8 mutating endpoints across 6 lanes.
   Every lane test file has a `*_requires_api_key` test asserting 401 without
   the header (valid body, so the 401 proves the auth gate, not validation).

## 4. Minors closed
- F841 in `test_pfas.py` (CI-breaking unused variable) — fixed; ruff clean.
- Turbulence latest-version sort: `max()` over semver strings ordered
  lexicographically (10.0.0 < 2.0.0); now orders by `released_at`.
- PFAS enum triple-source drift: values agreed, but risk remained. Guardrails:
  import-time assert `set(DestructionMethod.__args__) ==
  set(DESTRUCTION_METHODS)` in `pfas/schemas.py` + test
  `test_method_vocab_matches_sql_check` asserting every method appears in the
  SQL CHECK constraint.
- Dead "rates not wired yet" comments/branches: updated — rates are wired in
  `config.TOLL_RATES`; the ValueError catch stays as defense, comment says so.
- Subseasonal test import hack (`sys.path` + top-level import) replaced with
  proper package import so the shared `..auth` import resolves.
- `docs/TW-564-data-layer-architecture.md` untracked — committed with this
  round (it is the referenced parent architecture).

## 5. Test report
Command (from `backend/`):
`python3 -m pytest tests/test_electrocatalysis.py tests/test_pfas.py
tests/test_turbulence.py tests/test_subseasonal.py tests/test_high_tc.py
app/data_layer/science/crop_climate/tests/`
Result: **169 passed** (was 162 pre-fix; +7: five 401 gate tests, one bronze
provenance assertion folded into an existing test, one SQL vocab guardrail).
Ruff: clean on all science code, config, main, and all six lane test files.

## 6. Known limitations (V1, documented, not blockers)
- ed25519 signature *verification* is deferred to pre-partner-onboarding;
  V1 presence-checks signatures (unsigned → quarantine, never promoted).
- high_tc ingest keeps client-supplied `artifact_id` (no bronze-first rewrite
  yet — turbulence was the Rosa-flagged lane; high_tc follows in V2).
- Crop-climate in-memory store keeps a minimal `table()` shim for the auth
  lookup in tests only; production path is `SupabaseCropClimateStore`.

## 7. Merge readiness
Rosa re-review requested on this diff. Merge requires Justynn's exact word.
