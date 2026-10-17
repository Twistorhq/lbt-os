<!-- Prepared by Twistor Holdings LLC -->

# TW-569: Niche 1 — trades benchmark layer (first data-layer implementation)

- Ticket: TW-569 · Reporter: Justynn · Owner: Dre Coleman (Senior Backend Engineer & DBA)
- Branch: `dre/tw-569-benchmark-layer` · Base: `origin/main`
- Architecture: `docs/TW-564-data-layer-architecture.md` (Rosa APPROVED round 2, on `dre/tw-564-revision` — merge to main is a separate step; this build implements the approved revision verbatim)
- Related: TW-201 (benchmark foundation), TW-549 (micro-tolling), TW-550 (toll ledger), TW-552 (PII doctrine), TW-556 (M2 emitters)

## Problem

The TW-564 data-layer architecture is approved but nothing implements it. The
trades benchmark layer is the first byproduct niche in the build order: the
foundation exists (TW-201: `benchmark_org_metrics` + `benchmark_cohort_stats`
with k-anonymity), but it lacks the data-layer framework (ingestion runs,
bronze artifacts, `data_events` lineage log, dead letters), the new M1/M2
metric emitters, richer cohort dimensions, the toll-metered serving API with
dynamic volume-tiered derivative pricing, and the hold-and-suppress
publication rule Rosa required.

## Scope (this ticket)

1. **Framework migration** (`supabase/migration_data_layer_framework.sql`):
   `data_sources`, `data_ingest_runs`, `bronze_artifacts` (UNIQUE
   (source_id, content_hash), promotion_status), `data_events` (append-only
   lineage + verification log; artifact_id NULL on shared-asset events),
   `disputes`, `dead_letters` (redaction_spec_version). Serving:
   `data_api_keys` (billing_org_id, key_hash, niche_scope), `data_api_plans`,
   `data_api_subscriptions`, `data_toll_dedupe`, `toll_emit_queue`
   (UNIQUE idempotency_key), `data_api_usage`. `toll_events.applied_tier`
   + `plan_id` (the LAW: stamp the applied volume tier at emit time).
   `benchmark_cohort_stats.consecutive_passes` + `published` (hold-and-suppress).
   All tables get the TW-547 RLS contract; shared-asset tables get NO direct
   read policies (the data API is the policy).
2. **Schema registry + redaction spec** (Major 3): 
   `backend/app/data_layer/schemas/benchmarks/v1.json` (metric batch
   contract) and `backend/app/data_layer/redaction/benchmarks_v1.yaml`
   (the exact fields stripped/HMAC'd before any dead_letters write).
3. **Byproduct emitters** (`backend/app/data_layer/benchmarks/emitters.py`):
   per-org 30d metric snapshots from operational tables, consent-gated
   (no consent, no emission, ever):
   - `missed_call_recovery_rate`, `time_to_first_response_min` (M2 call_logs
     + missed_call_recoveries)
   - `toll_spend_per_booked_job_cents` (M1 toll_events / completed jobs)
   - `estimate_close_rate`, `avg_job_value_cents` (quotes, jobs)
   Cohort key format `trade:region:shopsize` (e.g. `hvac:co:smb`);
   legacy `trade:smb` keys remain readable.
4. **Promotion job** (`promote.py`): idempotent bronze → silver. Writes the
   artifact registry row (content_hash = sha256 of canonical metric JSON;
   payload bytes go to Storage `data-bronze-trades_benchmarks/`), validates
   against the schema contract, writes `benchmark_org_metrics` (org-private),
   emits `data_events` (actor='product', attested skipped for byproduct),
   rejects → `dead_letters` (redacted per spec). Per-record fault isolation
   (TW-208): one bad org never kills the run.
5. **Gold rollup** (`rollup.py`): refresh `benchmark_cohort_stats` from
   silver. k-anonymity n>=5 enforced at write; hold-and-suppress: a cohort
   publishes only after 3 consecutive passing refreshes
   (consecutive_passes); below-threshold cohorts flip published=false
   (disappearance is not an event — no differencing leak).
6. **Serving API** (`backend/app/routers/data_v1.py`):
   `GET /data/v1/benchmarks/{cohort_key}?metric=&period=` — API-key auth
   (HMAC key_hash, niche_scope check), in-memory token-bucket rate limit,
   REQUIRED `Idempotency-Key` header (400 without it), reads gold
   (published=true only), response carries `as_of` + `provenance`
   (n_orgs, source_count, period). Serve first, then enqueue toll.
7. **Toll worker** (`backend/app/data_layer/toll_worker.py`): drains
   `toll_emit_queue` with retry + backoff (TW-208). Computes the charge
   from the key's plan at emit time with **dynamic volume-tiered pricing**:
   `derivative_params.volume_tiers` steps per-call rates down as aggregate
   platform volume crosses thresholds; stamps `applied_tier` on the
   toll_events row. Checks `data_toll_dedupe` first — never double-tolls.
   A failed toll insert retries; a failed read never blocks (already served).
8. **Seed plan**: `benchmarks_payg` (base 0, 25c/call, volume tiers
   1000→20c, 10000→15c, declared in-plan, no surprise repricing).

## Out of scope

- Niches 2–7. The framework tables are shared; niche models are theirs.
- Merging `dre/tw-564-revision` (the approved arch doc) to main — separate step.
- Real Storage bucket creation / Vault wiring (env-configured; documented).
- Frontend. This is API + jobs; Sofia gets the contract in the evidence doc.

## Rosa's gates this build must satisfy

- Major 1: shared-asset `data_events` carry NULL artifact_id; cohort reads
  terminate at promotion_batch_id. Test: no query path from a cohort row
  to a contributor org.
- Major 2: no bare sha256 of identifiers anywhere in this niche (no
  identifier columns exist here — state it in the evidence doc).
- Major 5: client-supplied Idempotency-Key required (400 contract);
  billing_org_id on every key; out-of-band toll emit; never double-toll.
- Dynamic pricing LAW: applied_tier stamped on every data toll row.
