<!-- Prepared by Twistor Holdings LLC -->

# TW-569 TDD evidence — Niche 1: trades benchmark layer

- Branch: `dre/tw-569-benchmark-layer` · Ticket: TW-569 · Owner: Dre Coleman
- Architecture: TW-564 round-2 (Rosa APPROVED, on `dre/tw-564-revision`)

## What was built

| Piece | File |
|---|---|
| Framework migration (12 tables, 2 lineage functions, RLS, seed plan) | `supabase/migration_data_layer_framework.sql` |
| OpenSpec proposal + tasks | `openspec/changes/tw-569-benchmark-layer/` |
| Schema registry (metric batch contract) | `backend/app/data_layer/schemas/benchmarks/v1.json` |
| Redaction spec (Major 3) | `backend/app/data_layer/redaction/benchmarks_v1.yaml` |
| Byproduct emitters (M1/M2/quote/job metrics, consent-gated) | `backend/app/data_layer/benchmarks/emitters.py` |
| Silver promotion job (idempotent, fault-isolated) | `backend/app/data_layer/benchmarks/promote.py` |
| Gold rollup (k-anonymity, hold-and-suppress) | `backend/app/data_layer/benchmarks/rollup.py` |
| Toll worker (dynamic volume tiers, dedupe, retry/backoff) | `backend/app/data_layer/toll_worker.py` |
| Serving API | `backend/app/routers/data_v1.py` (+ main.py registration) |
| toll_service: `applied_tier` + `plan_id` params (backward compatible) | `backend/app/services/toll_service.py` |

## Test results

- New: `backend/tests/test_data_layer_benchmarks.py` — **20/20 pass**
- Full suite: **246 passed, 8 subtests passed** (was 226 + 8 before this ticket)
- Ruff check + format: clean (CI's ruff 0.16.8)
- Migration SQL: all 36 statements parse under pglast (real Postgres grammar)

## Rosa's TW-564 gates — how each is satisfied

- **Major 1 (lineage de-anonymization):** shared-asset `data_events` carry
  `artifact_id=NULL`, enforced by a CHECK constraint (not convention).
  `data_events_cohort()` returns batch-level aggregates only. Test
  `test_rollup_lineage_terminates_at_batch` asserts the NULL on the insert path.
- **Major 2 (unsalted hashes):** this niche has NO identifier columns —
  emitters aggregate only (no phone/email/name selected). The redaction spec
  HMACs `org_id` before any dead-letter write (secret ref documented).
- **Major 5 (double-toll):** Idempotency-Key REQUIRED (400 contract, tested);
  `toll_emit_queue` UNIQUE(idempotency_key); worker checks `data_toll_dedupe`
  before emitting; `record_toll_event` UNIQUE backstop. Test
  `test_duplicate_idempotency_key_never_double_tolls`.
- **Dynamic pricing LAW:** `toll_events.applied_tier` + `plan_id` columns;
  worker resolves `volume_tiers` at emit time and stamps the tier.
  Tests: tier resolution across thresholds, charge computation, tier stamp
  on the emitted toll.
- **Minor 13 (hold-and-suppress):** `consecutive_passes` + `published` on
  `benchmark_cohort_stats`; 3 consecutive passes to publish; below-threshold
  flips published=false without deleting (no differencing leak). Tested.

## API contract (for Sofia / partners)

```
GET /data/v1/benchmarks/{cohort_key}?metric=&period=30d
Headers: X-API-Key (required), Idempotency-Key (required)
Cohort key: trade:region:shopsize, e.g. hvac:co:smb (legacy trade:smb works)
Response: { cohort_key, period, metrics[{metric_name, p50, mean, n_orgs}],
            as_of, provenance{n_orgs, source, source_kind, contract_version, period} }
Errors: 400 missing Idempotency-Key / unknown metric; 401 bad key;
        403 key not scoped; 404 insufficient_cohort_data; 429 rate limited
```

Seed plan `benchmarks_payg`: 25c/call, volume tiers 1000→20c, 10000→15c.
Toll event type: `data.benchmark.query`, billed to the key's billing_org_id.

## Known non-blocking notes

- `promote.py` uses per-record try/except rather than the sh retry wrapper:
  it is a scheduled job (whole-run retry by the scheduler), not an inline
  request path — the inline emitter keeps the retry_with_backoff pattern.
- Real Storage bucket `data-bronze-trades_benchmarks` and Vault wiring are
  ops steps (env-configured); documented in code comments.
- `POST /data/v1/ingest/{niche}` is not shipped: niche 1 is byproduct-only.
  It lands with the first partner-fed niche.
