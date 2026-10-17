<!-- Prepared by Twistor Holdings LLC -->

# TW-564: Twistor Data Layer Architecture  -  Seven Niches, One Lakehouse

**Ticket:** TW-564 · **Author:** Dre Coleman, Senior Backend Engineer & DBA · **Status:** Architecture revision  -  Rosa re-review (round 2)
**Source:** Justynn, 2026-10-16  -  "be THE data layer" across all seven niches
**Repo:** `Twistorhq/lbt-os` · **Branch:** `dre/tw-564-revision`

This run is **architecture only**. No code, no migrations. Each niche becomes its own implementation ticket with an OpenSpec proposal before code (per TW-100), after Rosa approves this plan.

**Revision note (round 2):** Rosa returned REQUEST_CHANGES on round 1  -  8 majors, 7 minors. Every finding is answered below with mechanism, not prose. The load-bearing changes: lineage terminates at batch level for shared assets (Major 1); HMAC with per-niche Vault secrets and a per-source-type hash boundary (Major 2); bronze access as RLS + audit-logged reader function (Major 3); lineage and verification merged into one append-only `data_events` log with real DDL (Major 4); billing accounts, client-supplied idempotency keys, and out-of-band toll emit (Major 5); ingestion idempotency (Major 6); an attested pilot feed in Phase 2 (Major 7); merged followup tables (Major 8); plus Justynn's addition  -  toll-metered pay-per-use APIs with derivative pricing in the serving layer.

**Standing constraints this plan obeys:**
- **TW-552 PII doctrine:** PII hashed at ingestion, stored ONLY in bronze (raw). Never customer-facing in gold. Masked in silver/gold: emails, addresses, phone numbers, SSNs, EINs, SAM.gov/UEI identifiers, all business identifiers.
- **TW-208 self-healing:** one bad record never kills an ingest run; per-record fault isolation, dead-letter, retry with backoff. Toll emission is out-of-band: a failed toll write never fails a data read.
- **TW-547 RLS contract:** every table gets `org_id` (where org-scoped) + app-level scoping + a matching RLS policy. No exceptions. Policy templates per table class are in §4.
- **TW-549 micro-tolling:** every served data API call emits to the M1 `toll_events` ledger with a client-supplied idempotency key. Pay-per-use plans carry derivative pricing (base + % of value, collars, credits).

---

## 0. Hash boundary and key management (answers Major 2)

TW-552 says "hashed at ingestion." This section says exactly where, per source type, and with what.

**The rule: HMAC-SHA256, never bare sha256.** Bare sha256 of a phone number or a small-set partner ref is dictionary-reversible. Every hashed identifier in this lakehouse is:

```
HMAC-SHA256(secret_niche_v{k},  niche || '|' || field_name || '|' || value)
```

- `secret_niche_v{k}`: per-niche secret from the Vault (`datalayer/hmac/{niche}`), versioned. Domain separation via the niche + field context string means a hash from one niche/field is useless in another.
- Every hashed column carries a sibling `hmac_key_version` so rotation does not break joins: new writes use the new version, old versions stay readable until backfill.
- Rotation is a DBA runbook item (Dre owns it), not an application concern.

**Where hashing happens, per source type  -  picked and written down:**

| Source type | Hash boundary | Raw PII persistence |
|---|---|---|
| **Byproduct** (product emitters: M2 call logs, job events) | In product code, BEFORE the bronze write. The emitter HMACs PII fields in the application layer. | None. Bronze Storage and Postgres never see raw PII from byproduct sources. |
| **Partner feeds** (POST /data/v1/ingest/{niche} or pulled) | At the ingest edge worker (service_role), on receipt, BEFORE the bronze write. The worker applies the niche's redaction/HMAC spec to the incoming payload. | None. Raw PII exists only in worker memory for the duration of the request. It is never written to Storage, Postgres, or logs. |
| **Public sources** (scrapes, public portals) | Same edge treatment as partner feeds. PII is unexpected here but handled identically if encountered. | None. |

**Consequence:** there is no "hash at promotion" path anywhere. If raw PII ever lands in bronze, that is a bug, not a design choice  -  the promotion job asserts hashed-only on PII fields and quarantines violations.

---

## 1. Medallion lakehouse: where it lives

**Decision: Supabase Postgres + Supabase Storage, one stack, no new vendor.** The boring stack done rigorously is what scales (Study Hall 2026-10-04). Postgres is the query engine and the registry of truth; Storage holds raw artifacts. No Spark, no warehouse, no new bill  -  the FOSS-first rule holds and the data layer rides the infra we already operate.

```
┌─────────────────────────────────────────────────────────────┐
│ BRONZE (raw)  -  append-only, service_role eyes only          │
│  Supabase Storage buckets: data-bronze-{niche}              │
│  + Postgres registry: bronze_artifacts (every file/row      │
│    batch tracked with provenance + content hash)            │
│  PII: HMAC'd at the boundary (§0). Raw PII never persists.  │
│  NOTHING reads bronze except through bronze_read_artifact() │
│  (SECURITY DEFINER, service_role only, audit-logged).       │
├─────────────────────────────────────────────────────────────┤
│ SILVER (cleaned)  -  Postgres tables per niche                │
│  Normalized, validated against schema registry, PII masked. │
│  Org-private rows carry org_id + RLS. Shared-asset rows     │
│  carry NO org linkage (k-anonymity enforced at write,       │
│  hold-and-suppress per §3). Every silver table carries      │
│  verification_status (pending|attested|verified|disputed|    │
│  quarantined).                                              │
├─────────────────────────────────────────────────────────────┤
│ GOLD (served)  -  Postgres marts + materialized views         │
│  API-facing aggregates, registry lookups, benchmark stats.  │
│  NEVER customer-facing PII. Refreshed on schedule with      │
│  as_of watermarks, never on read.                           │
└─────────────────────────────────────────────────────────────┘
```

**Promotion is one-way and logged.** Bronze → silver is an idempotent batch job (replayable from the artifact hash, deduped per §2); silver → gold is a scheduled refresh with a watermark. Every promotion writes to `data_events` (the single append-only lineage + verification log, §2), which is the audit-trail spine the NKC/M3 lineage work plugs into.

**Bronze access control  -  the mechanism (answers Major 3):**

```sql
-- 1. Storage buckets: service_role only, no JWT roles.
--    Supabase storage.objects policy (one per data-bronze-{niche} bucket):
CREATE POLICY bronze_service_role_only ON storage.objects
  FOR ALL TO service_role
  USING (bucket_id LIKE 'data-bronze-%')
  WITH CHECK (bucket_id LIKE 'data-bronze-%');
-- No policies exist for anon or authenticated: access is denied by default.

-- 2. bronze_artifacts registry: RLS deny-all except service_role.
ALTER TABLE bronze_artifacts ENABLE ROW LEVEL SECURITY;
-- No USING policies for any JWT role. service_role bypasses RLS.

-- 3. The ONLY read path: one SECURITY DEFINER function, audit-logged.
CREATE TABLE bronze_access_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  accessor text NOT NULL,          -- service_role session context (set_config)
  artifact_id uuid NOT NULL REFERENCES bronze_artifacts(id),
  purpose text NOT NULL,           -- 'promotion' | 'dispute_investigation' | 'audit'
  accessed_at timestamptz NOT NULL DEFAULT now()
);
-- bronze_read_artifact(artifact_id, purpose):
--   asserts current_setting('request.jwt.role') = 'service_role'
--   inserts into bronze_access_log, then returns the bytes.
-- Direct SELECT on bronze_artifacts by any JWT role returns zero rows. Period.
```

**Bronze immutability carve-outs (answers Minor 14, Minor 10):**
- Bronze is immutable by default: no UPDATE, no DELETE in the application path.
- **Carve-out A  -  deletion requests (crypto-shredding):** a verified deletion request for partner data destroys the HMAC key version scoped to that partner (hashes become unlinkable noise) and tombstones the artifacts (`promotion_status='skipped'`, Storage object deleted, registry row retained with a `tombstoned_at`). The bytes are gone; the hashes are meaningless.
- **Carve-out B  -  telemetry retention window:** raw `telemetry_readings` partitions drop after 13 months per contract; gold aggregates survive forever. This is the declared exception to immutability, not a contradiction of it.

**Why not a separate warehouse:** the gold marts are aggregates and lookups, not ad-hoc analytics over billions of rows. If a niche outgrows Postgres reads, the escape hatch is a read replica, not a new platform. That decision gets revisited with an explain plan under real volume, per the skill's rule.

---

## 2. Ingestion framework

Built by extending the existing connector spine (`integration_connections`, `integration_sync_runs`, `integration_record_links`), not beside it. Framework DDL  -  complete, no missing tables:

```sql
data_sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  niche text NOT NULL,              -- 'biochar_mrv' | 'hemp_composites' | ...
  partner_name text NOT NULL,       -- the verified industry partner
  source_kind text NOT NULL,        -- 'byproduct' | 'partner_feed' | 'public' (Minor 12)
  feed_mode text NOT NULL,          -- 'batch' | 'stream'
  contract_version int NOT NULL,    -- pinned data-contract version
  status text NOT NULL,             -- 'active' | 'paused' | 'quarantined'
  created_at timestamptz NOT NULL DEFAULT now()
);

data_ingest_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id uuid NOT NULL REFERENCES data_sources(id),
  idempotency_key text,             -- client-supplied; UNIQUE per source (Major 6)
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  records_in int NOT NULL DEFAULT 0,
  records_accepted int NOT NULL DEFAULT 0,
  records_rejected int NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'running',  -- 'running'|'ok'|'partial'|'failed'
  error_summary text,               -- never raw DB errors, never PII
  UNIQUE (source_id, idempotency_key)
);

bronze_artifacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES data_ingest_runs(id),
  source_id uuid NOT NULL REFERENCES data_sources(id),
  storage_path text NOT NULL,       -- data-bronze-{niche}/...
  content_hash text NOT NULL,       -- sha256 of the STORED payload (already HMAC'd)
  source_name text NOT NULL,        -- provenance: who sent it
  collected_at timestamptz NOT NULL,
  collector text NOT NULL,          -- 'product' | partner id | 'public-scrape'
  schema_version int NOT NULL,
  promotion_status text NOT NULL DEFAULT 'pending',
                                    -- 'pending'|'promoted'|'failed'|'skipped' (Major 6)
  promoted_at timestamptz,
  UNIQUE (source_id, content_hash)  -- partner push retries cannot duplicate (Major 6)
);

-- ONE append-only log replaces data_lineage + verification_log (Major 4).
-- Shared-asset events NEVER carry artifact_id: lineage terminates at the
-- promotion batch, so no reader can re-link a shared row to its contributor (Major 1).
data_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type text NOT NULL,  -- 'ingested'|'promoted'|'attested'|'disputed'|'resolved'|'quarantined'
  silver_table text,         -- null for ingested events
  silver_id uuid,            -- null for shared-asset cohort-level events
  promotion_batch_id uuid,   -- always set for promoted and later; the shared-asset terminus
  artifact_id uuid REFERENCES bronze_artifacts(id),
                             -- NULL for shared-asset events. Non-null only for
                             -- org-private lineage, which the org may read for its own rows.
  actor text NOT NULL,       -- 'promotion_job' | partner id | 'system'
  contract_version int,
  payload_hash text,         -- hash of the attestation payload, where applicable
  note text,                 -- never PII
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Dispute case tracker. Lifecycle transitions are ALSO data_events
-- (disputed/resolved), so the case and the audit trail cannot disagree.
disputes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  silver_table text NOT NULL,
  silver_id uuid NOT NULL,
  raised_by text NOT NULL,
  reason text NOT NULL,
  status text NOT NULL DEFAULT 'open',  -- 'open'|'investigating'|'resolved_upheld'|'resolved_rejected'
  resolution text,
  resolved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Dead letters: redaction happens BEFORE the write, per niche (Major 3).
dead_letters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES data_ingest_runs(id),
  niche text NOT NULL,
  redaction_spec_version int NOT NULL,  -- which per-niche redaction list was applied
  raw_payload text,          -- redacted per the niche list; never raw PII
  reason text NOT NULL,
  retried int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
-- RLS: service_role only (framework class, §4). The promotion job is the sole writer.
```

**Per-niche redaction lists (Major 3):** each niche's OpenSpec proposal MUST include its redaction field list  -  the exact fields the promotion job strips or HMACs before any `dead_letters` write. Dre (DBA) owns the canonical lists in-repo at `backend/app/data_layer/redaction/{niche}_v{N}.yaml`; the `redaction_spec_version` on the dead letter pins which list was applied. No list, no niche ships.

**Lineage read rules (Major 1, spelled out):**
- **Org-private rows:** the org may read its own rows' full lineage (silver row → artifact → source). The org already knows its own data; nothing leaks.
- **Shared-asset rows:** lineage terminates at `promotion_batch_id`. Reads go through the `data_events_cohort(promotion_batch_id)` SECURITY DEFINER function, which returns batch-level rows only (counts, timestamps, contract versions)  -  never `artifact_id`, never per-row contributor drill-down.
- `bronze_artifacts.source_name` / `collector` are unreachable from any shared-asset read path: the only join key (`artifact_id`) is NULL on those events, and the registry table is service_role-only.

**Ingestion idempotency (Major 6):** `POST /data/v1/ingest/{niche}` accepts an `Idempotency-Key` header. The key is stored on `data_ingest_runs`; a duplicate key returns the original run's receipt (status + record counts) without re-ingesting. The `UNIQUE (source_id, content_hash)` on `bronze_artifacts` is the backstop: even a retry with a new key cannot duplicate the artifact, and the promotion job skips rows where `promotion_status != 'pending'`.

**Schema registry:** versioned JSON schemas per niche, stored in-repo (`backend/app/data_layer/schemas/{niche}/v{N}.json`). The silver promotion job validates every record against the pinned contract version; rejects go to `dead_letters`, never silently dropped. Contract bumps are migrations, not surprises  -  partners get a deprecation window.

**Data contracts per niche** (what the partner promises):
- **Byproduct niches** (benchmarks, conversations, estimating): the contract is internal  -  the product's own event emitters. Versioned with the product release.
- **Partner feeds** (biochar labs, composite labs, pyrolyzer fleets, permit sources): signed contract per partner  -  field list, cadence, format, and the attestation clause (see §5).
- **Public sources** (permit portals, weather): scraper/API contract with source URL + retrieval timestamp as provenance.

---

## 3. Per-niche data models

Shared conventions: `id uuid PK default gen_random_uuid()`, `org_id` + RLS on org-private tables, NO org linkage on shared-asset tables (the moat must never leak who contributed what), `created_at`/`updated_at` everywhere, `verification_status text NOT NULL DEFAULT 'pending'` on every silver table (Major 4: 'pending'|'attested'|'verified'|'disputed'|'quarantined'), `hmac_key_version int` alongside every HMAC'd column (§0), soft delete nowhere in bronze (immutable, carve-outs in §1), `is_current` flags in silver where supersession happens.

**K-anonymity, hold-and-suppress (Minor 13):** a cohort publishes to gold only after meeting the n≥5 contributor threshold for 3 consecutive refreshes. The rollup tracks `consecutive_passes`; a cohort that drops below threshold stops publishing but its disappearance is not itself an event  -  no differencing leak, because publication was never promised per-refresh.

### Niche 1  -  Biochar MRV registry (partner-fed)
```sql
biochar_batches (
  id, producer_id_hmac text, hmac_key_version int,   -- HMAC'd partner ref (§0)
  verification_status text NOT NULL DEFAULT 'pending',
  feedstock text, process_type text,
  production_date date, mass_tonnes numeric,
  lab_carbon_content numeric, lab_hc_org_ratio numeric,
  permanence_model text, permanence_years int,
  status text  -- 'pending_lab' | 'verified' | 'disputed' | 'retired'
);
mrv_attestations (
  id, batch_id REFERENCES biochar_batches(id),
  attestor_partner_id uuid REFERENCES data_sources(id),  -- FK, not free text (Minor 11)
  attestation_type text,  -- 'lab' | 'auditor' | 'model'
  signed_payload_hash text,             -- the signed receipt
  verification_status text NOT NULL DEFAULT 'pending',
  attested_at timestamptz
);
```
Gold: `gold_mrv_credits`  -  verified batches with computed credit tonnes, lookup by batch hash. The registry API answers "is this credit real" from gold only. Disputed batches flip `verification_status='disputed'`, drop from gold, and open a `disputes` case (freeze first, investigate, then resolve).

### Niche 2  -  Hemp composite property dataset (partner-fed)
```sql
composite_samples (
  id, lab_ref_hmac text, hmac_key_version int,
  verification_status text NOT NULL DEFAULT 'pending',
  fiber_source text, retting_method text, fiber_treatment text,
  matrix_type text, matrix_chemistry text,
  process_method text, process_temp_c numeric,
  tensile_mpa numeric, flexural_mpa numeric, impact_kj_m2 numeric,
  density_g_cm3 numeric, moisture_pct numeric,
  test_standard text  -- 'ASTM D3039' etc: comparability lives here
);
```
Gold: `gold_composite_stats`  -  property distributions by fiber/matrix/process cohort (k-anonymity ≥ 5 labs per cohort, hold-and-suppress per above). The inverse-design API queries gold; raw samples never leave silver.

### Niche 3  -  Pyrolysis telemetry network (streaming, partner-fed)
```sql
pyrolyzer_units (
  id, fleet_ref_hmac text, hmac_key_version int,
  verification_status text NOT NULL DEFAULT 'pending',
  unit_model text,
  install_region text,  -- region only, never address
  commissioned_at date, status text
);
telemetry_readings (
  unit_id uuid REFERENCES pyrolyzer_units(id),
  ts timestamptz NOT NULL,                        -- sensor clock
  received_at timestamptz NOT NULL DEFAULT now(), -- ingest clock (Minor 10)
  temp_c numeric, feed_rate_kg_h numeric,
  o2_pct numeric, pressure_kpa numeric,
  energy_in_kwh numeric,
  PRIMARY KEY (unit_id, ts, received_at)  -- no uuid bloat, no same-ts collision
) PARTITION BY RANGE (ts);               -- monthly partitions
```
Partitioning is declared up front because this is the one niche with true time-series volume. **Partition management:** a pg_cron job `datalayer_partition_maint` runs monthly, creating next month's partition and dropping partitions older than the 13-month raw retention window (the §1 carve-out B). Dre owns the job; pg_partman is the fallback if the hand-rolled job misbehaves. Aggregates survive in gold forever.

### Niche 4  -  Trades benchmark layer (byproduct  -  EXTENDS TW-201)
The foundation exists: `benchmark_org_metrics` (private per-org) + `benchmark_cohort_stats` (k-anonymity ≥ 5). Extension:
- New metric emitters from M1/M2: recovery rate, time-to-first-response, toll spend per booked job, estimate close rate.
- New cohort dimensions: `cohort_key` gains trade + region + shop-size segments (existing pattern, more keys).
- Consent stays explicit: `benchmark_consent` opt-in, no consent no aggregation, ever.
- Hold-and-suppress (Minor 13) applies to new cohorts the same as existing ones.
Gold is already `benchmark_cohort_stats`  -  the serving API reads it directly.

### Niche 5  -  Estimating cost truth (byproduct)
```sql
job_cost_facts (
  id, org_id uuid,                  -- private until aggregated
  verification_status text NOT NULL DEFAULT 'pending',
  trade text, region text,           -- region, never address
  job_type text, completed_at date,
  labor_cost_cents int, materials_cost_cents int,
  total_cost_cents int, duration_hours numeric
);
```
Shared-asset gold: `gold_cost_benchmarks`  -  p50/mean cost by (trade, region, job_type) with n ≥ 5 contributing orgs, hold-and-suppress. Feeds from the existing `quotes` + `jobs` tables via the silver promotion job; no new product instrumentation beyond a completion hook.

### Niche 6  -  Permit intelligence (partner + public feeds)
```sql
permit_records (
  id,
  verification_status text NOT NULL DEFAULT 'pending',
  jurisdiction text NOT NULL,    -- normalized: 'CO/Denver'
  permit_type text, applicant_hmac text, hmac_key_version int,
  filed_at date, issued_at date, final_at date,
  status text, valuation_cents bigint,
  source_url text                    -- provenance for public pulls
);
jurisdiction_norms (
  jurisdiction text NOT NULL,
  window_start date NOT NULL, window_end date NOT NULL,  -- norms over WHAT span (Minor 11)
  median_days_file_to_issue numeric,
  permits_per_month numeric,
  computed_at timestamptz,
  PRIMARY KEY (jurisdiction, window_start)
);
```
Normalization is the product: every jurisdiction's quirks mapped to one schema. `jurisdiction_norms` is the gold mart the radar queries.

### Niche 7  -  Conversation outcome data (byproduct from M2)
```sql
-- Merged: followup_threads + followup_outcomes were a 1:1 split (Major 8  -  Dre, Rosa caught you again).
followup_threads (
  id, org_id uuid,
  verification_status text NOT NULL DEFAULT 'pending',
  channel text,                     -- 'sms' | 'voice'
  started_at timestamptz,
  contact_hmac text, hmac_key_version int,  -- PII HMAC'd in product code (§0)
  trade text, region text,
  converted boolean,                -- nullable until resolved
  time_to_convert_hours numeric,
  booked_value_cents int,
  resolved_at timestamptz
);
```
Gold: `gold_followup_benchmarks`  -  conversion rate and median time-to-convert by (trade, region, hour-of-day), n ≥ 5 orgs, hold-and-suppress. This is the dataset that trains the follow-up models and the one every shop benefits from.

---

## 4. Serving layer

**One FastAPI router family:** `backend/app/routers/data_v1.py`, routes namespaced per niche:

```
GET  /data/v1/mrv/batches/{batch_hash}         -  credit verification lookup
GET  /data/v1/composites/query                 -  property search + inverse design
GET  /data/v1/benchmarks/{cohort_key}          -  trades benchmarks
GET  /data/v1/costs/{trade}/{region}           -  estimating cost truth
GET  /data/v1/permits/norms/{jurisdiction}     -  permit intelligence
GET  /data/v1/followups/benchmarks             -  conversation outcomes
POST /data/v1/ingest/{niche}                   -  partner push ingestion (batch)
```

**Auth:** API keys, not user sessions.

```sql
data_api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key_hash text NOT NULL,       -- HMAC with the server key-signing secret (§0 spirit)
  partner_id text NOT NULL,     -- identity: who holds the key
  billing_org_id uuid NOT NULL REFERENCES organizations(id),
                                -- who PAYS for this key's lookups (Major 5a).
                                -- Partner keys bill to the partner's org;
                                -- internal Twistor keys bill to Twistor's own org.
                                -- toll_events is org-scoped (M1): every toll row
                                -- carries billing_org_id. No orphan tolls.
  niche_scope text[] NOT NULL,  -- a biochar buyer gets MRV read, not composites
  plan_id uuid REFERENCES data_api_plans(id),
  rate_limit_per_min int NOT NULL,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

**Toll-metered pay-per-use APIs with derivative pricing (Justynn's addition):** every API product is a plan. The plan sets the micro-toll; the derivative sets the shape of the bill.

```sql
data_api_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,            -- 'mrv_payg' | 'benchmarks_found_money' | ...
  base_cents int NOT NULL DEFAULT 0,     -- flat base per month (the land)
  per_call_cents int NOT NULL DEFAULT 0, -- the micro-toll per served call (the expand)
  derivative_type text,          -- null | 'found_money' | 'collar' | 'credits'
  derivative_params jsonb,       -- see semantics below
  created_at timestamptz NOT NULL DEFAULT now()
);
data_api_subscriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key_id uuid NOT NULL REFERENCES data_api_keys(id),
  plan_id uuid NOT NULL REFERENCES data_api_plans(id),
  status text NOT NULL DEFAULT 'active',
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz
);
```

Pricing semantics (TW-549 doctrine, applied to data APIs):
- **Pay-per-use (derivative_type null):** `per_call_cents × calls`. The meter runs; the bill is arithmetic.
- **Found-money call option (`found_money`):** `base_cents` + `pct` of attributed value above `baseline`. `derivative_params = {pct_of_value, baseline_cents, attribution_field}`. Example: MRV verification API  -  base $50/mo + 2% of verified credit value above the buyer's monthly baseline, attributed from `gold_mrv_credits` itself. The customer pays big only when we find big.
- **Collar:** monthly min/max on the variable portion. `derivative_params = {floor_cents, cap_cents}`. Budget certainty for the buyer, a floor for us.
- **Opportunity credits (`credits`):** pre-bought toll credits at a 20% discount, drawn down per call. `derivative_params = {discount_pct, block_cents}`. Locks in spend, funds our float.

The toll worker computes the charge from the key's plan at emit time; the `toll_events` row carries the computed amount + plan reference. One money pipe: product tolls and data tolls land in the same ledger.

**Idempotency  -  "never double-toll" as mechanism (Major 5b):**
- The edge **requires** an `Idempotency-Key` header on every tollable endpoint. It is mapped 1:1 to `request_id`. Serve the read, then enqueue the toll (below). The toll worker checks `data_toll_dedupe` before emitting:
```sql
data_toll_dedupe (
  idempotency_key text PRIMARY KEY,   -- the client's key, verbatim
  key_id uuid NOT NULL REFERENCES data_api_keys(id),
  endpoint text NOT NULL,
  tolled_at timestamptz NOT NULL DEFAULT now()
);
```
- If the client omits the header, the server generates a `request_id` AND applies fingerprint-window dedupe: `fingerprint = sha256(method|path|sorted_query_params)`; a toll for the same `(key_id, endpoint, fingerprint)` inside a 60-second window is dropped. Documented fallback, not the primary path  -  clients are told to send the header.
- **Out-of-band toll emit (TW-208):** the API serves the read FIRST, then enqueues a toll job to `toll_emit_queue` (durable Postgres queue table). A worker drains it with retry + backoff. If the `toll_events` insert fails, the read already succeeded; the queue retries. A failed toll write never fails a data read, and no toll is ever emitted twice for one idempotency key.

**Rate limits (Minor 9):** per-key token bucket. Enforcement lives in worker memory (in-process bucket per API worker); `data_api_usage` (key_id, window_start, count) is the reconciliation record, flushed every 60 seconds  -  not the enforcement path, so there is no hot-row write per read. 429 with `Retry-After`, logged, never a 500. The flush cadence and the in-memory fallback are both in the runbook.

**Response contract:** every response carries `as_of` (gold refresh watermark) and `provenance` (source count, attestation summary where relevant, cohort n). Honest uncertainty instead of fake precision  -  a benchmark with n=5 says so.

**RLS policy templates per table class (Minor 15):** the TW-547 contract, written for the three data-layer classes. `auth_org_id()` is the JWT claim pattern from TW-547.

```sql
-- Class 1: org-private (job_cost_facts, followup_threads, benchmark_org_metrics).
-- The org sees exactly its own rows.
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON <t>
  FOR ALL TO authenticated
  USING (org_id = auth_org_id())
  WITH CHECK (org_id = auth_org_id());

-- Class 2: shared-asset (gold_* marts, composite_samples, biochar_batches,
-- permit_records, telemetry_readings, pyrolyzer_units).
-- NO direct reads by any JWT role: deny-all by default (no policies created).
-- All reads go through the data API (service_role), which enforces
-- key.niche_scope @> ARRAY[niche] before serving. The API is the policy.

-- Class 3: framework (data_sources, data_ingest_runs, bronze_artifacts,
-- data_events, disputes, dead_letters, data_api_keys, data_toll_dedupe,
-- toll_emit_queue, bronze_access_log).
-- service_role only. The single exception is the SECURITY DEFINER function
-- data_events_cohort(promotion_batch_id), which returns batch-level
-- data_events rows (counts, timestamps, contract versions) for shared-asset
-- lineage queries  -  never artifact_id, never per-row contributor detail.
```

---

## 5. Verification: how "verified" is enforced

"Verified data" is not a vibe; it is a state machine, and the state machine now has DDL (§2).

```
raw → validated (schema) → attested (source-signed) → verified → disputed?
```

- **Source attestation:** partner pushes sign their payload (HMAC with the partner's key, or a signed JSON envelope). The signature hash lands in `bronze_artifacts` next to the content hash, and an `attested` event lands in `data_events` with the `payload_hash`. Unsigned partner data is quarantined (`verification_status='quarantined'`, never promoted).
- **Audit trail:** `data_events` is the single append-only log  -  every promotion, attestation, dispute, and resolution is an event. This is the NKC/M3 lineage pattern applied to the lakehouse: every gold row traces to its promotion batch, and org-private rows trace further to their bronze artifact.
- **Cross-checks:** silver promotion runs niche-specific plausibility rules (e.g. carbon content within physical bounds, permit dates ordered, telemetry within sensor ranges). Failures → `dead_letters` with reason (redacted per the niche list), partner notified.
- **Dispute handling:** any gold row can be flagged → `verification_status='disputed'`, excluded from serving until resolved, `disputes` case opened, `disputed` event logged. Disputed MRV credits are the highest-severity path: freeze first, investigate, then resolve (`resolved` event + case closure must agree).
- **Byproduct niches** are verified differently: the product itself is the witness (the job completed, the call converted). Verification = the event chain exists in the operational tables, not a partner's signature. Their `data_events` carry `actor='product'` and skip attestation.

**The attestation path gets exercised before MRV needs it (Major 7):** Phase 2 lands one small attested pilot feed  -  a cooperating jurisdiction's permit feed (or a small partner) pushing HMAC-signed payloads through the full path: sign → ingest → verify signature → promote → `attested` event → serve. Permits on public sources prove ingestion breadth; the pilot proves attestation. MRV inherits both, and the "proven verification path" claim is earned, not asserted.

---

## 6. Build order

**Recommendation: byproduct first, partner-fed second, streaming last.** Data you collect by operating costs nothing marginal; data you beg partners for costs relationships.

| Order | Niche | Why now | Depends on |
|---|---|---|---|
| 1 | **Trades benchmarks** (extend TW-201) | Foundation exists; M1/M2 add new emitters; cheapest win | M1/M2 merged (done) |
| 2 | **Conversation outcomes** | M2's call/SMS data is already flowing; HMAC per §0 | M2 (built, merge pending) |
| 3 | **Estimating cost truth** | Quotes + jobs already modeled; one completion hook | FSM core (exists) |
| 4 | **Permit intelligence** | First partner-fed niche; public sources de-risk it (no partner needed to start) | Ingestion framework |
| 4a | **Attested pilot feed** (NEW, Major 7) | One cooperating jurisdiction/partner exercising HMAC attestation end to end | Ingestion framework |
| 5 | **Biochar MRV** | Needs the verification story (§5) built and proven on 4 + 4a first | Ingestion + verification |
| 6 | **Pyrolysis telemetry** | Streaming infra + partitioning; most operational load | Ingestion (stream mode) |
| 7 | **Hemp composite dataset** | Aggregation play; needs lab partners signed | Partner contracts |

**Phase 1 (this quarter):** ingestion framework (§2) + niches 1–3. These three are pure byproduct  -  the lakehouse earns its keep before a single partner call.
**Phase 2:** niche 4 (permits, public sources) + niche 4a (attested pilot feed). Together they prove both ingestion breadth and the attestation path.
**Phase 3:** niches 5–7, the heavy lifts  -  MRV on a proven verification path, telemetry on proven streaming, composites on signed lab partners.

Each niche ships as: OpenSpec proposal (MUST include its per-niche redaction list, §2) → migration(s) → promotion job → gold refresh → serving routes → tests → evidence → Rosa review. No mega-PRs; the lakehouse grows one niche at a time.

---

## Open questions for Rosa / Justynn

1. **Shared-asset ownership:** the aggregated datasets are Twistor's moat. The plan now enforces org-unlinkability mechanically (batch-terminated lineage, hold-and-suppress, service_role-only bronze). Confirm this matches the business intent  -  contributors can never see who else contributed.
2. **Retention:** bronze immutable by default; telemetry raw drops after 13 months (carve-out B), everything else kept. Deletion requests handled by crypto-shredding (carve-out A). Needs a business nod.
3. **MRV liability:** serving "verified" carbon credits carries legal weight. Recommend Angela review the attestation language before the MRV niche serves externally. (Unchanged  -  still the right escalation.)
4. **Derivative pricing parameters:** the found-money `% of value` and collar bounds per API product are business calls Tommy should price (TW-549). The mechanism is built; the numbers are his.

---

## Revision changelog (round 2)

| # | Rosa finding | Fix location |
|---|---|---|
| Major 1 | Lineage re-links shared assets to contributors | §2 `data_events` (artifact_id NULL on shared events) + lineage read rules; §4 Class 2/3 RLS |
| Major 2 | Hash boundary unspecified; bare sha256 reversible | New §0: HMAC-SHA256, per-niche Vault secrets, per-source-type boundary table |
| Major 3 | Bronze access a sentence; dead_letters a second PII store | §1: bucket RLS + deny-all + `bronze_read_artifact()` + `bronze_access_log`; §2: per-niche redaction lists |
| Major 4 | Verification state machine half prose; DDL missing | §2: `data_events` merges lineage + verification log; `disputes` table; `verification_status` on all silver tables (§3) |
| Major 5 | No billing account; double-toll possible | §4: `billing_org_id`; required `Idempotency-Key` + `data_toll_dedupe` + fingerprint fallback; out-of-band `toll_emit_queue` |
| Major 6 | Ingestion idempotency | §2: `UNIQUE (source_id, content_hash)`, `promotion_status`, ingest `Idempotency-Key` |
| Major 7 | Permits can't prove the attestation path | §5 + §6: Phase 2a attested pilot feed; claim restated honestly |
| Major 8 | followup_threads + followup_outcomes 1:1 split | §3: merged into one table (Rosa caught me again) |
| Minor 9 | Hot-row counter on every read | §4: in-memory bucket, 60s flush to `data_api_usage` |
| Minor 10 | Telemetry PK collision; partition ownership; retention vs immutability | §3: PK `(unit_id, ts, received_at)`; pg_cron `datalayer_partition_maint`; §1 carve-out B |
| Minor 11 | jurisdiction_norms window; attestor free text | §3: `window_start/window_end`; `attestor_partner_id` FK |
| Minor 12 | partner_type mixes kinds | §2: renamed `source_kind` |
| Minor 13 | K-anonymity flapping via differencing | §3: hold-and-suppress, 3 consecutive refreshes |
| Minor 14 | Bronze immutability vs deletion requests | §1: carve-out A (crypto-shredding) |
| Minor 15 | RLS templates missing | §4: three class templates with `auth_org_id()` |
| Justynn | Toll-metered pay-per-use APIs + derivative pricing | §4: `data_api_plans`, `data_api_subscriptions`, four pricing semantics |
