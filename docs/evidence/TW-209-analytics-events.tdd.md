# TW-209 Phase 1 evidence report — internal product analytics event layer

**Prepared by Twistor Holdings LLC**

Ticket: TW-209 · Branch: `feature/tw-209-analytics-schema` · Author: Dre Coleman
Reviewer: Rosa Delgado · Date: 2026-09-24

## What this is

Phase 1 of TW-209 (internal product analytics + ML platform): the analytics
data layer — a dedicated schema for feature-usage events, a single
`emit_event()` instrumentation API, and three router hook points proving the
pattern. Zeke leads the Phase 2 build-out; Maya owns models/rollups in
Phase 3. This phase ships:

- `supabase/migration_analytics_events.sql` — `analytics_feature_events`
  (org_id, vertical, feature_key, actor_role, session_id, context JSONB,
  occurred_at) + `analytics_feature_catalog` dimension table, seeded with the
  3 Phase-1 features. Org-isolated RLS on events; catalog read-only to
  clients. Privacy boundary documented in the migration header.
- `backend/app/services/analytics.py` — the single instrumentation API:
  dotted-path `feature_key` validation, allowlisted context keys, PII-shaped
  key AND value screening, identifier shape on `metric`/`detector` values,
  PII-shaped `session_id` degrading to NULL. Never raises (TW-208 spirit).
- `backend/app/leak_engine/engine.py` — new strict `vertical_for_org(db,
  org_id)`; `_vertical_for` delegates with its documented "hvac" fallback.
- `backend/app/routers/leak_engine.py` — hook points: `leak_brief.viewed`
  (emitted AFTER the endpoint's work so `result:"ok"` is truthful),
  `detectors.listed` (forgiving analytics-only vertical tagging),
  `benchmark.compare` (strict org lookup — a DB blip raises instead of
  silently serving an empty "unknown" cohort).

## RED / GREEN history

- GREEN (initial): 20/20 tests, ruff clean under the repo config.
- RED (Rosa round 1): REQUEST_CHANGES — 4 majors: (1) no TDD evidence
  report; (2) privacy boundary overclaimed — `sanitize_context` policed
  context keys but not values, and the "no free-text columns" claim was
  false; (3) `benchmark_compare` silently swallowed org-lookup DB failures
  into an "unknown" cohort + duplicated `engine._vertical_for`; (4)
  `leak_brief` emitted `result:"ok"` before `record_org_metrics` ran.
  Rosa verified the three hook points were clean — no active leak.
- GREEN (fix round, this report): all 4 majors + nits fixed —
  `sanitize_context` screens values (PII fragments, `@`, identifier shape
  for metric/detector), PII-shaped `session_id` degrades to NULL, one strict
  `vertical_for_org` core with documented forgiving wrappers (product path
  strict, analytics tagging forgiving), `leak_brief` emits just before
  `return brief`, plus this evidence file. 30/30 tests, ruff clean.

## Verification (run at commit time, this round)

- `pytest tests/test_analytics_events.py` — 30/30 pass (10 new this round:
  PII-shaped value drops, identifier shape, session_id degradation,
  allowlist/blocked-fragment disjointness, brief/compare e2e wiring,
  strict-vs-forgiving vertical behavior pins).
- `pytest tests/` (full backend suite) — 102 passed, 8 subtests passed.
- `ruff check app tests` (repo config, CI's invocation) — clean.
- Migration SQL syntax-validated via pglast; idempotent (`IF NOT EXISTS`,
  `ON CONFLICT DO NOTHING`, `pg_policies` guard). NOT applied to a live
  Supabase — no DB credentials in scope; the `psql` apply + smoke query is a
  merge-checklist item pre-deploy (Rosa: syntax + convention verification
  suffices for merge).

## Guarantees table

| Claim | Proven by |
|---|---|
| Only allowlisted context keys are stored | `test_unknown_keys_dropped` |
| PII-shaped context VALUES are dropped (not just keys) | `test_pii_shaped_values_dropped` |
| metric/detector values must be identifiers | `test_identifier_shape_enforced_on_metric_and_detector` |
| PII-shaped session_id degrades to NULL, event still lands | `test_pii_shaped_session_id_degrades_to_null` |
| Good opaque session_id is kept | `test_good_session_id_kept` |
| allowlist can never overlap the blocked-fragment set | `test_allowlist_blocked_fragments_disjoint` |
| No PII-shaped column can enter the schema later | `test_events_table_has_exact_column_set`, `test_no_banned_column_names` |
| Events land only in `analytics_feature_events` | `test_only_analytics_tables_written`, router wiring tests |
| emit_event never breaks the product path | `test_db_failure_never_raises` |
| `result:"ok"` is truthful (emit after endpoint work) | `test_brief_endpoint_emits_after_success` (emit is last before return) |
| Product path is strict: org-lookup failure raises, never a silent empty cohort | `test_compare_org_lookup_failure_raises_not_silent_cohort` |
| Analytics tagging is forgiving: lookup failure → "unknown" tag, endpoint still serves | `test_detectors_endpoint_survives_org_lookup_failure` |
| Migration is idempotent and RLS-guarded | `test_rls_enabled`, `test_expected_tables_exist` |

## Known limits / Phase 2

- Only 3 hook points wired (brief, detectors, benchmark compare) — full
  endpoint wiring is Phase 2 with Zeke.
- `actor_role` is contract-documented ('owner'|'admin'|'tech', never a user
  id) but not enum-enforced in code; current hook points don't pass it.
- Catalog seed `ON CONFLICT DO NOTHING` silently ignores future description
  updates — descriptions get their own migration (noted in the migration
  header).
- No concurrent-emit test: `emit_event` is stateless; Rosa agreed it's
  unnecessary.
- Notification-path self-healing is TW-208 follow-up territory, not this
  ticket.
