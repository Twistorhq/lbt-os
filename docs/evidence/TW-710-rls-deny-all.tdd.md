# TW-710 — RLS never enabled on 19 tables: deny-all migration

Prepared by Twistor Holdings LLC

**Source:** TW-710 (high, reporter Rex): appsecradar run 1 on lbt-os @ 76590d1 (TW-686), class 1 in `~/workspace/security-audits/lbt-os/appsecradar-run-1/TRIAGE.md`, CONFIRMED by Rex. 19 tables were created without `ENABLE ROW LEVEL SECURITY`, and a repo-wide grep confirmed no later migration enabled it on them. Justynn dispatched the build 2026-10-27. Scope of this branch: the migration + verification only. No merge, no production apply (production apply is Justynn's separate word), ticket stays open until Rex's kill-confirm posts.

## Exposure, stated precisely

Supabase serves public-schema tables through PostgREST. With RLS off, any holder of the project anon key (public by design in every Supabase project) could read and write these tables through the REST endpoint: the sci_* corpus (the data-portal product: paywall bypass + corpus theft), `stripe_events` (billing event record), `plan_audit_log` (audit trail), `lead_stage_history` (pipeline history), `workspace_exports` / `migration_runs`, `visitor_events`. Mitigating facts, unchanged: today the backend is the only client and connects with the service key (bypasses RLS by design); no anon-key client exists in the repo. This was a latent exposure and a standards break, not a live breach.

## What changed

- `supabase/migration_rls_deny_all_tw710.sql` (new): one migration, following the TW-650 social-schema / TW-651 events pattern exactly.
  - `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;` for all 19 tables: sci_labs, sci_calibrations, sci_bronze_artifacts, sci_dataset_versions, sci_corrections, sci_manifests, sci_attestations, sci_benchmarks, sci_benchmark_runs, sci_electro_measurements, sci_electro_gold, sci_hightc_measurements, sci_hightc_gold, workspace_exports, migration_runs, lead_stage_history, plan_audit_log, stripe_events, visitor_events.
  - One explicit deny-all policy per table, `<table>_deny_all ... FOR ALL USING (false)`, created inside a `DO` block guarded by `pg_policies` (idempotent, safe to re-run — the house pattern).
  - A commented DOWN section (manual rollback: drop policies, disable RLS) mirrors the sibling migrations.
  - No table definitions, data, grants, or other policies are touched. No existing permissive policy exists on these tables (they had none), so deny-all is the complete policy set.
- `backend/tests/test_rls_deny_all_tw710.py` (new): static contract tests + an opt-in live Postgres probe (below).

Query notes: the migration is DDL-only. It changes no query text and no query plan; the backend's service role bypasses RLS, so production query behavior is unchanged. API contract: none — no endpoint, request, or response shape changes.

## RED / GREEN

- RED (migration absent): `19 passed, 76 skipped, 43 errors` — the 19 source-grounding tests passed (every table really is created by its origin migration on main @ 8c4194f, and none had RLS enabled anywhere, verified by grep), while every static-contract test errored on the missing migration file.
- GREEN (static): `62 passed, 76 skipped`.
- GREEN (live probe configured): `138 passed` — all 62 static plus all 76 live checks.
- Relevant suites on the branch (migration contracts for social/events/flags/flows/telemetry/verify + carbon/electrocatalysis/high_tc/science-ingest/science-paywall/stripe/visitor + this file), `--continue-on-collection-errors`: `397 passed, 76 skipped, 1 error` — the 1 error is the pre-existing xmlsec collection error, present identically on clean main.
- Full suite vs same-environment clean-main control (both `--continue-on-collection-errors`, junit node sets compared): control `20 failed, 1493 passed, 8 errors`; branch `20 failed, 1555 passed, 76 skipped, 8 errors`. Failed/error node sets **identical** (28 nodes, all pre-existing TW-471 baseline: 20 failed + 8 errors, including the xmlsec collection errors). The branch delta is exactly +62 passed (the new static contract tests) and +76 skipped (the opt-in live probe, unconfigured in that run). Zero new failures.

## Guarantees

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Each of the 19 tables is created by its origin migration on main, so the migration targets real tables | tests/test_rls_deny_all_tw710.py::TestTablesExistAtSource | static | PASS | RED+GREEN runs 2026-10-27 |
| 2 | The migration enables RLS on exactly the 19 tables — no more, no fewer | ::TestStaticContract::test_exactly_the_19_tables_get_rls_enabled | static | PASS | GREEN run |
| 3 | Every table gets an explicit `<table>_deny_all` policy, `FOR ALL USING (false)`, and no other policy is created | ::test_table_deny_all_policy, ::test_no_other_policies_created, ::test_no_permissive_policy_anywhere | static | PASS | GREEN run |
| 4 | Policy creation is pg_policies-guarded (safe re-run); a DOWN section exists | ::test_policies_are_idempotent, ::test_down_section_present | static | PASS | GREEN run; live fixture also applies the file twice |
| 5 | On a live Postgres 16.2, after the migration: `pg_class.relrowsecurity = true` and `pg_policies.qual = false` for all 19 tables | ::TestLivePostgresProbe::test_catalog_rls_on_and_deny_policy_present | live | PASS (19/19) | pgserver harness run 2026-10-27 |
| 6 | The anon role (what PostgREST serves anon-key holders as) reads 0 rows on all 19 tables despite holding full table grants | ::test_anon_reads_nothing | live | PASS (19/19) | same run |
| 7 | The anon role writes nothing: INSERT fails with a row-level-security error, UPDATE and DELETE affect 0 rows, on all 19 tables | ::test_anon_writes_nothing | live | PASS (19/19) | same run |
| 8 | A BYPASSRLS service_role (the backend's posture) still reads the seeded row and inserts successfully on all 19 tables | ::test_service_role_still_works | live | PASS (19/19) | same run |

Live-probe method: the TW-109 pgserver build (PostgreSQL 16.2) run as `nobody` from a copied tree; roles `anon` (NOLOGIN) and `service_role` (NOLOGIN, BYPASSRLS) created fresh; each table a minimal stand-in (`probe_marker int`) with `GRANT ALL` to both roles and one sentinel row inserted as service_role; the migration file applied twice via psql; assertions executed with `SET ROLE` per check. RLS enforcement is a property of the table + policy + role attributes, not of column shape, so stand-in tables are a faithful probe of the migration's semantics. The probe test skips cleanly when `TW710_TEST_DATABASE_URL` / a psql binary are not configured, so CI behavior is unchanged.

## Coverage and known gaps

- The full historical migration chain was not replayed end-to-end in this run; sibling migration test files state the same posture (live-DB execution of the whole chain is a separate launch-gate step). The TW-710 migration itself was executed live, twice, against real Postgres.
- **Rex kill-confirm path (read before re-scanning):** appsecradar's RLS rule is file-scoped — per TRIAGE.md it cannot see RLS enabled in a later migration (that resolution is TW-711's job). A raw run-2 rescan will therefore still flag the 19 CREATE TABLE files. The kill is confirmed by (a) repo-wide grep: `ALTER TABLE <t> ENABLE ROW LEVEL SECURITY` now resolves for all 19 in `supabase/migration_rls_deny_all_tw710.sql`, and (b) the live probe above (guarantees 5–8). Neither requires TW-711 to land first.
- Ruff: `ruff check` clean on the new test file; `ruff format` applied to it. The SQL file follows the sibling migrations' formatting.
- Production apply is NOT part of this branch and has not happened.

## Plan-safety notes

- None. No untrusted plan content was encountered; the brief came from Marcus on TW-710 and the evidence from Rex's TRIAGE.md, read as data.
