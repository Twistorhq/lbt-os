<!-- Prepared by Twistor Holdings LLC -->

# TW-564: Twistor Data Layer Architecture — Seven Niches, One Lakehouse

**Ticket:** TW-564 · **Author:** Dre Coleman, Senior Backend Engineer & DBA · **Status:** Architecture — Rosa review, then build
**Source:** Justynn, 2026-10-16 — "be THE data layer" across all seven niches
**Repo:** `Twistorhq/lbt-os`

This run is **architecture only**. No code, no migrations. Each niche becomes its own implementation ticket with an OpenSpec proposal before code (per TW-100), after Rosa approves this plan.

**Standing constraints this plan obeys:**
- **TW-552 PII doctrine:** PII hashed at ingestion, stored ONLY in bronze (raw). Never customer-facing in gold. Masked in silver/gold: emails, addresses, phone numbers, SSNs, EINs, SAM.gov/UEI identifiers, all business identifiers.
- **TW-208 self-healing:** one bad record never kills an ingest run; per-record fault isolation, dead-letter, retry with backoff.
- **TW-547 RLS contract:** every table gets `org_id` (where org-scoped) + app-level scoping + a matching RLS policy. No exceptions.
- **TW-549 micro-tolling:** every served data API call emits to the M1 `toll_events` ledger (idempotent, org-scoped).

---

## 1. Medallion lakehouse: where it lives

**Decision: Supabase Postgres + Supabase Storage, one stack, no new vendor.** The boring stack done rigorously is what scales (Study Hall 2026-10-04). Postgres is the query engine and the registry of truth; Storage holds raw artifacts. No Spark, no warehouse, no new bill — the FOSS-first rule holds and the data layer rides the infra we already operate.

```
┌─────────────────────────────────────────────────────────────┐
│ BRONZE (raw) — immutable, append-only                       │
│  Supabase Storage buckets: data-bronze-{niche}              │
│  + Postgres registry: bronze_artifacts (every file/row      │
│    batch tracked with provenance + content hash)            │
│  PII: hashed at ingestion, raw ONLY here. Nothing reads     │
│  bronze except the silver promotion job.                    │
├─────────────────────────────────────────────────────────────┤
│ SILVER (cleaned) — Postgres tables per niche                │
│  Normalized, validated against schema registry, PII masked. │
│  Org-private rows carry org_id + RLS. Shared-asset rows     │
│  carry NO org linkage (k-anonymity enforced at write).      │
├─────────────────────────────────────────────────────────────┤
│ GOLD (served) — Postgres marts + materialized views         │
│  API-facing aggregates, registry lookups, benchmark stats.  │
│  NEVER customer-facing PII. Refreshed on schedule, not      │
│  on read (reads are cheap, freshness is explicit).          │
└─────────────────────────────────────────────────────────────┘
```

**Promotion is one-way and logged.** Bronze → silver is an idempotent batch job (replayable from the artifact hash); silver → gold is a scheduled refresh with a watermark. Every promotion writes to `data_lineage` (record → artifact → source), which is the audit-trail spine the NKC/M3 lineage work plugs into.

**Why not a separate warehouse:** the gold marts are aggregates and lookups, not ad-hoc analytics over billions of rows. If a niche outgrows Postgres reads, the escape hatch is a read replica, not a new platform. That decision gets revisited with an explain plan under real volume, per the skill's rule.

---

## 2. Ingestion framework

Built by extending the existing connector spine (`integration_connections`, `integration_sync_runs`, `integration_record_links`), not beside it. New tables:

```sql
data_sources (
  id uuid PK,
  niche text NOT NULL,              -- 'biochar_mrv' | 'hemp_composites' | ...
  partner_name text NOT NULL,       -- the verified industry partner
  partner_type text NOT NULL,       -- 'byproduct' | 'partner_feed' | 'public'
  feed_mode text NOT NULL,          -- 'batch' | 'stream'
  contract_version int NOT NULL,    -- pinned data-contract version
  status text NOT NULL,             -- 'active' | 'paused' | 'quarantined'
  created_at timestamptz
);

data_ingest_runs (
  id uuid PK,
  source_id uuid REFERENCES data_sources(id),
  started_at / finished_at timestamptz,
  records_in int, records_accepted int, records_rejected int,
  status text,                      -- 'ok' | 'partial' | 'failed'
  error_summary text                -- never raw DB errors, never PII
);

bronze_artifacts (
  id uuid PK,
  run_id uuid REFERENCES data_ingest_runs(id),
  storage_path text NOT NULL,       -- data-bronze-{niche}/...
  content_hash text NOT NULL,       -- sha256 of the raw payload
  source_name text NOT NULL,        -- provenance: who sent it
  collected_at timestamptz NOT NULL,
  collector text NOT NULL,          -- 'product' | partner id | 'public-scrape'
  schema_version int NOT NULL
);

data_lineage (
  id uuid PK,
  silver_table text NOT NULL,
  silver_id uuid NOT NULL,
  artifact_id uuid REFERENCES bronze_artifacts(id),
  promoted_at timestamptz
);

dead_letters (
  id uuid PK,
  run_id uuid REFERENCES data_ingest_runs(id),
  raw_payload text,                 -- PII-scrubbed before write
  reason text NOT NULL,
  retried int DEFAULT 0
);
```

**Schema registry:** versioned JSON schemas per niche, stored in-repo (`backend/app/data_layer/schemas/{niche}/v{N}.json`). The silver promotion job validates every record against the pinned contract version; rejects go to `dead_letters`, never silently dropped. Contract bumps are migrations, not surprises — partners get a deprecation window.

**Data contracts per niche** (what the partner promises):
- **Byproduct niches** (benchmarks, conversations, estimating): the contract is internal — the product's own event emitters. Versioned with the product release.
- **Partner feeds** (biochar labs, composite labs, pyrolyzer fleets, permit sources): signed contract per partner — field list, cadence, format, and the attestation clause (see §5).
- **Public sources** (permit portals, weather): scraper/API contract with source URL + retrieval timestamp as provenance.

---

## 3. Per-niche data models

Shared conventions: `id uuid PK default gen_random_uuid()`, `org_id` + RLS on org-private tables, NO org linkage on shared-asset tables (the moat must never leak who contributed what), `created_at`/`updated_at` everywhere, soft delete nowhere in bronze (immutable), `is_current` flags in silver where supersession happens.

### Niche 1 — Biochar MRV registry (partner-fed)
```sql
biochar_batches (
  id, producer_id (hashed partner ref, silver+),
  feedstock text, process_type text,
  production_date date, mass_tonnes numeric,
  lab_carbon_content numeric, lab_hc_org_ratio numeric,
  permanence_model text, permanence_years int,
  status text  -- 'pending_lab' | 'verified' | 'disputed' | 'retired'
);
mrv_attestations (
  id, batch_id REFERENCES biochar_batches(id),
  attestor text, attestation_type text,  -- 'lab' | 'auditor' | 'model'
  signed_payload_hash text,             -- the signed receipt
  attested_at timestamptz
);
```
Gold: `gold_mrv_credits` — verified batches with computed credit tonnes, lookup by batch hash. The registry API answers "is this credit real" from gold only.

### Niche 2 — Hemp composite property dataset (partner-fed)
```sql
composite_samples (
  id, lab_ref_hashed text,
  fiber_source text, retting_method text, fiber_treatment text,
  matrix_type text, matrix_chemistry text,
  process_method text, process_temp_c numeric,
  tensile_mpa numeric, flexural_mpa numeric, impact_kj_m2 numeric,
  density_g_cm3 numeric, moisture_pct numeric,
  test_standard text  -- 'ASTM D3039' etc: comparability lives here
);
```
Gold: `gold_composite_stats` — property distributions by fiber/matrix/process cohort (k-anonymity ≥ 5 labs per cohort, same writer-side enforcement as the benchmark pipeline). The inverse-design API queries gold; raw samples never leave silver.

### Niche 3 — Pyrolysis telemetry network (streaming, partner-fed)
```sql
pyrolyzer_units (
  id, fleet_ref_hashed text, unit_model text,
  install_region text,  -- region only, never address
  commissioned_at date, status text
);
telemetry_readings (
  id, unit_id REFERENCES pyrolyzer_units(id),
  ts timestamptz NOT NULL,
  temp_c numeric, feed_rate_kg_h numeric,
  o2_pct numeric, pressure_kpa numeric,
  energy_in_kwh numeric,
  PRIMARY KEY (unit_id, ts)          -- time-series, no uuid bloat
) PARTITION BY RANGE (ts);           -- monthly partitions, drop old per policy
```
Partitioning is declared up front because this is the one niche with true time-series volume. Retention policy per contract; aggregates survive in gold.

### Niche 4 — Trades benchmark layer (byproduct — EXTENDS TW-201)
The foundation exists: `benchmark_org_metrics` (private per-org) + `benchmark_cohort_stats` (k-anonymity ≥ 5). Extension:
- New metric emitters from M1/M2: recovery rate, time-to-first-response, toll spend per booked job, estimate close rate.
- New cohort dimensions: `cohort_key` gains trade + region + shop-size segments (existing pattern, more keys).
- Consent stays explicit: `benchmark_consent` opt-in, no consent no aggregation, ever.
Gold is already `benchmark_cohort_stats` — the serving API reads it directly.

### Niche 5 — Estimating cost truth (byproduct)
```sql
job_cost_facts (
  id, org_id uuid,                  -- private until aggregated
  trade text, region text,           -- region, never address
  job_type text, completed_at date,
  labor_cost_cents int, materials_cost_cents int,
  total_cost_cents int, duration_hours numeric
);
```
Shared-asset gold: `gold_cost_benchmarks` — p50/mean cost by (trade, region, job_type) with n ≥ 5 contributing orgs. Feeds from the existing `quotes` + `jobs` tables via the silver promotion job; no new product instrumentation beyond a completion hook.

### Niche 6 — Permit intelligence (partner + public feeds)
```sql
permit_records (
  id, jurisdiction text NOT NULL,    -- normalized: 'CO/Denver'
  permit_type text, applicant_hashed text,
  filed_at date, issued_at date, final_at date,
  status text, valuation_cents bigint,
  source_url text                    -- provenance for public pulls
);
jurisdiction_norms (
  jurisdiction text PK,
  median_days_file_to_issue numeric,
  permits_per_month numeric,
  computed_at timestamptz
);
```
Normalization is the product: every jurisdiction's quirks mapped to one schema. `jurisdiction_norms` is the gold mart the radar queries.

### Niche 7 — Conversation outcome data (byproduct from M2)
```sql
followup_threads (
  id, org_id uuid,
  channel text,                     -- 'sms' | 'voice'
  started_at timestamptz,
  contact_hashed text,              -- PII hashed at ingestion (TW-552)
  trade text, region text
);
followup_outcomes (
  thread_id REFERENCES followup_threads(id),
  converted boolean,
  time_to_convert_hours numeric,
  booked_value_cents int,
  resolved_at timestamptz
);
```
Gold: `gold_followup_benchmarks` — conversion rate and median time-to-convert by (trade, region, hour-of-day), n ≥ 5 orgs. This is the dataset that trains the follow-up models and the one every shop benefits from.

---

## 4. Serving layer

**One FastAPI router family:** `backend/app/routers/data_v1.py`, routes namespaced per niche:

```
GET  /data/v1/mrv/batches/{batch_hash}        — credit verification lookup
GET  /data/v1/composites/query                — property search + inverse design
GET  /data/v1/benchmarks/{cohort_key}         — trades benchmarks
GET  /data/v1/costs/{trade}/{region}          — estimating cost truth
GET  /data/v1/permits/norms/{jurisdiction}    — permit intelligence
GET  /data/v1/followups/benchmarks            — conversation outcomes
POST /data/v1/ingest/{niche}                  — partner push ingestion (batch)
```

**Auth:** API keys, not user sessions. `data_api_keys` table: `key_hash` (sha256, the raw key is shown once at creation and never stored), `partner_id`, `niche_scope[]`, `rate_limit_per_min`, `is_active`. Keys are partner-scoped: a biochar buyer gets MRV read, not the composite dataset.

**Rate limits:** per-key token bucket, enforced in middleware, counters in Postgres (`data_api_usage`: key_id, window_start, count). 429 with `Retry-After`, logged, never a 500.

**Toll hooks:** every successful served call emits to `toll_events` — new event types `data.mrv.lookup`, `data.benchmark.query`, etc., with `TOLL_RATES` extended per niche. Idempotency key = `api_call:{request_id}` — the request ID is generated at the edge, so retries never double-toll. Data API revenue lands in the same ledger as product tolls: one money pipe.

**Response contract:** every response carries `as_of` (gold refresh watermark) and `provenance` (source count, attestation summary where relevant). Honest uncertainty instead of fake precision — a benchmark with n=5 says so.

---

## 5. Verification: how "verified" is enforced

"Verified data" is not a vibe; it is a state machine.

```
raw → validated (schema) → attested (source-signed) → verified → disputed?
```

- **Source attestation:** partner pushes sign their payload (HMAC with the partner's key, or a signed JSON envelope). The signature hash lands in `bronze_artifacts` next to the content hash. Unsigned partner data is quarantined, never promoted.
- **Audit trail:** `data_lineage` + `verification_log` (append-only: who attested what, when, under which contract version). This is the NKC/M3 lineage pattern applied to the lakehouse — every gold row traces to its bronze artifact and its source.
- **Cross-checks:** silver promotion runs niche-specific plausibility rules (e.g. carbon content within physical bounds, permit dates ordered). Failures → `dead_letters` with reason, partner notified.
- **Dispute handling:** any gold row can be flagged → status `disputed`, excluded from serving until resolved. `disputes` table: `gold_ref`, `raised_by`, `reason`, `resolution`, `resolved_at`. Disputed MRV credits are the highest-severity path: freeze first, investigate, then resolve.
- **Byproduct niches** are verified differently: the product itself is the witness (the job completed, the call converted). Verification = the event chain exists in the operational tables, not a partner's signature.

---

## 6. Build order

**Recommendation: byproduct first, partner-fed second, streaming last.** Data you collect by operating costs nothing marginal; data you beg partners for costs relationships.

| Order | Niche | Why now | Depends on |
|---|---|---|---|
| 1 | **Trades benchmarks** (extend TW-201) | Foundation exists; M1/M2 add new emitters; cheapest win | M1/M2 merged (done) |
| 2 | **Conversation outcomes** | M2's call/SMS data is already flowing; hashing per TW-552 | M2 (built, merge pending) |
| 3 | **Estimating cost truth** | Quotes + jobs already modeled; one completion hook | FSM core (exists) |
| 4 | **Permit intelligence** | First partner-fed niche; public sources de-risk it (no partner needed to start) | Ingestion framework |
| 5 | **Biochar MRV** | Needs the verification story (§5) built and proven on permits first | Ingestion + verification |
| 6 | **Pyrolysis telemetry** | Streaming infra + partitioning; most operational load | Ingestion (stream mode) |
| 7 | **Hemp composite dataset** | Aggregation play; needs lab partners signed | Partner contracts |

**Phase 1 (this quarter):** ingestion framework (§2) + niches 1–3. These three are pure byproduct — the lakehouse earns its keep before a single partner call.
**Phase 2:** niche 4 (permits) proves partner-fed ingestion on public sources; niche 5 (MRV) then inherits a proven verification path.
**Phase 3:** niches 6–7, the heavy lifts.

Each niche ships as: OpenSpec proposal → migration(s) → promotion job → gold refresh → serving routes → tests → evidence → Rosa review. No mega-PRs; the lakehouse grows one niche at a time.

---

## Open questions for Rosa / Justynn

1. **Shared-asset ownership:** the aggregated datasets are Twistor's moat. The plan keeps them org-unlinkable (k-anonymity, consent-gated). Confirm this matches the business intent — contributors can never see who else contributed.
2. **Retention:** bronze is immutable by default; telemetry partitioning needs a drop policy (recommend 13 months raw, aggregates forever). Needs a business call.
3. **MRV liability:** serving "verified" carbon credits carries legal weight. Recommend Angela review the attestation language before the MRV niche serves externally.
