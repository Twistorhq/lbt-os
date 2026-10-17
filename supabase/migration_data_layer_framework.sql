-- =============================================================
-- TW-569: Data-layer framework + Niche 1 (trades benchmark layer)
-- Implements the Rosa-approved TW-564 architecture (round 2, on
-- dre/tw-564-revision). Framework tables are shared across all 7 niches;
-- niche 1 ships the byproduct benchmark pipeline on top.
--
-- Classes (TW-564 §4 RLS templates):
--   Framework tables (data_sources, data_ingest_runs, bronze_artifacts,
--   data_events, disputes, dead_letters): service_role only. The promotion
--   job and data API are the sole readers/writers. No JWT policies created
--   (deny-all by default once RLS is enabled).
--   Shared-asset tables: NO direct reads by any JWT role. The data API
--   (service_role) is the policy; it enforces key.niche_scope.
-- Run once against your Supabase project, after migration_toll_ledger.sql
-- and migration_benchmark_pipeline.sql.
-- =============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ----------------------------------------------------------
-- FRAMEWORK: ingestion
-- ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_sources (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche            TEXT NOT NULL,   -- 'trades_benchmarks' | 'biochar_mrv' | ...
    partner_name     TEXT NOT NULL,
    source_kind      TEXT NOT NULL,   -- 'byproduct' | 'partner_feed' | 'public'
    feed_mode        TEXT NOT NULL,   -- 'batch' | 'stream'
    contract_version INT  NOT NULL,
    status           TEXT NOT NULL,   -- 'active' | 'paused' | 'quarantined'
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_ingest_runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id        UUID NOT NULL REFERENCES data_sources(id),
    idempotency_key  TEXT,            -- client-supplied; UNIQUE per source
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    records_in       INT NOT NULL DEFAULT 0,
    records_accepted INT NOT NULL DEFAULT 0,
    records_rejected INT NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'running',
                     -- 'running' | 'ok' | 'partial' | 'failed'
    error_summary    TEXT,            -- never raw DB errors, never PII
    UNIQUE (source_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS bronze_artifacts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id           UUID NOT NULL REFERENCES data_ingest_runs(id),
    source_id        UUID NOT NULL REFERENCES data_sources(id),
    storage_path     TEXT NOT NULL,   -- data-bronze-{niche}/...
    content_hash     TEXT NOT NULL,   -- sha256 of the STORED payload
    source_name      TEXT NOT NULL,   -- provenance: who sent it
    collected_at     TIMESTAMPTZ NOT NULL,
    collector        TEXT NOT NULL,   -- 'product' | partner id | 'public-scrape'
    schema_version   INT NOT NULL,
    promotion_status TEXT NOT NULL DEFAULT 'pending',
                     -- 'pending' | 'promoted' | 'failed' | 'skipped'
    promoted_at      TIMESTAMPTZ,
    UNIQUE (source_id, content_hash)  -- push retries cannot duplicate
);
CREATE INDEX IF NOT EXISTS idx_bronze_artifacts_pending
    ON bronze_artifacts (source_id, promotion_status)
    WHERE promotion_status = 'pending';

-- ONE append-only log: lineage + verification (TW-564 Major 4).
-- Shared-asset events NEVER carry artifact_id: lineage terminates at the
-- promotion batch, so no reader can re-link a shared row to its contributor
-- (TW-564 Major 1). Enforced by CHECK below, not by convention.
CREATE TABLE IF NOT EXISTS data_events (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type         TEXT NOT NULL,
                       -- 'ingested' | 'promoted' | 'attested' | 'disputed'
                       -- | 'resolved' | 'quarantined'
    silver_table       TEXT,
    silver_id          UUID,
    promotion_batch_id UUID,          -- always set for promoted and later
    artifact_id        UUID REFERENCES bronze_artifacts(id),
    actor              TEXT NOT NULL, -- 'promotion_job' | partner id | 'system'
    contract_version   INT,
    payload_hash       TEXT,
    note               TEXT,          -- never PII
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT data_events_shared_no_artifact CHECK (
        -- Shared-asset lineage terminates at the batch. Org-private rows
        -- may carry artifact_id (the org reads its own lineage only).
        silver_table IS NULL
        OR silver_table NOT IN ('benchmark_cohort_stats', 'gold_cost_benchmarks',
                                'gold_followup_benchmarks', 'gold_mrv_credits',
                                'gold_composite_stats', 'jurisdiction_norms')
        OR artifact_id IS NULL
    )
);

CREATE TABLE IF NOT EXISTS disputes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    silver_table TEXT NOT NULL,
    silver_id   UUID NOT NULL,
    raised_by   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
                -- 'open' | 'investigating' | 'resolved_upheld' | 'resolved_rejected'
    resolution  TEXT,
    resolved_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dead letters: redaction happens BEFORE the write, per niche (Major 3).
-- redaction_spec_version pins which list was applied.
CREATE TABLE IF NOT EXISTS dead_letters (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                 UUID NOT NULL REFERENCES data_ingest_runs(id),
    niche                  TEXT NOT NULL,
    redaction_spec_version INT NOT NULL,
    raw_payload            TEXT,      -- redacted per the niche list; never raw PII
    reason                 TEXT NOT NULL,
    retried                INT NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------
-- SERVING: API keys, plans, toll plumbing
-- ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_api_plans (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,   -- 'benchmarks_payg' | ...
    base_cents        INT NOT NULL DEFAULT 0,
    per_call_cents    INT NOT NULL DEFAULT 0,
    derivative_type   TEXT,            -- null | 'found_money' | 'performance_put'
                                       -- | 'collar' | 'credits'
    derivative_params JSONB,           -- volume_tiers et al; see TW-564 §4
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_api_keys (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash           TEXT NOT NULL,   -- HMAC(raw key, server secret
                                       -- Vault: datalayer/signing/api_key_hmac)
    partner_id         TEXT NOT NULL,   -- identity: who holds the key
    billing_org_id     UUID NOT NULL REFERENCES organizations(id),
                                       -- who PAYS for this key's lookups
    niche_scope        TEXT[] NOT NULL, -- e.g. ARRAY['trades_benchmarks']
    plan_id            UUID REFERENCES data_api_plans(id),
    rate_limit_per_min INT NOT NULL,
    is_active          BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_data_api_keys_hash ON data_api_keys (key_hash);

CREATE TABLE IF NOT EXISTS data_api_subscriptions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_id     UUID NOT NULL REFERENCES data_api_keys(id),
    plan_id    UUID NOT NULL REFERENCES data_api_plans(id),
    status     TEXT NOT NULL DEFAULT 'active',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at   TIMESTAMPTZ
);

-- "Never double-toll" as mechanism (TW-564 Major 5b).
CREATE TABLE IF NOT EXISTS data_toll_dedupe (
    idempotency_key TEXT PRIMARY KEY,  -- the client's key, verbatim
    key_id          UUID NOT NULL REFERENCES data_api_keys(id),
    endpoint        TEXT NOT NULL,
    tolled_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Out-of-band toll queue (durable; worker drains with retry + backoff).
CREATE TABLE IF NOT EXISTS toll_emit_queue (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL,
    key_id         UUID NOT NULL REFERENCES data_api_keys(id),
    billing_org_id UUID NOT NULL REFERENCES organizations(id),
    endpoint       TEXT NOT NULL,
    plan_id        UUID REFERENCES data_api_plans(id),
    attempts       INT NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'queued',
                   -- 'queued' | 'processing' | 'done' | 'dead'
    next_retry_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idempotency_key)  -- the queue itself cannot double-enqueue
);

-- Reconciliation record for rate limits (flushed every 60s; NOT the
-- enforcement path — enforcement is the in-memory token bucket).
CREATE TABLE IF NOT EXISTS data_api_usage (
    key_id       UUID NOT NULL REFERENCES data_api_keys(id),
    window_start TIMESTAMPTZ NOT NULL,
    count        INT NOT NULL DEFAULT 0,
    PRIMARY KEY (key_id, window_start)
);

-- ----------------------------------------------------------
-- TOLL LEDGER: dynamic volume-tier stamp (DATA LAYER DOCTRINE, LAW)
-- ----------------------------------------------------------

ALTER TABLE toll_events
    ADD COLUMN IF NOT EXISTS applied_tier TEXT;
    -- e.g. 'tier_0', 'tier_1' — the volume tier in force when the toll
    -- was computed. Every data toll stamps it; product tolls leave it NULL.

ALTER TABLE toll_events
    ADD COLUMN IF NOT EXISTS plan_id UUID REFERENCES data_api_plans(id);
    -- the plan that priced this toll (data API tolls). NULL for product tolls.

-- ----------------------------------------------------------
-- NICHE 1: hold-and-suppress columns on the gold mart
-- ----------------------------------------------------------

ALTER TABLE benchmark_cohort_stats
    ADD COLUMN IF NOT EXISTS consecutive_passes INT NOT NULL DEFAULT 0;
    -- consecutive refreshes meeting the n>=5 threshold (Minor 13)

ALTER TABLE benchmark_cohort_stats
    ADD COLUMN IF NOT EXISTS published BOOLEAN NOT NULL DEFAULT true;
    -- below-threshold cohorts flip to false; the API serves published=true
    -- only. Disappearance is not an event: no differencing leak.

-- The emitter computes the full cohort key (trade:region:shopsize);
-- silver rows carry it so the rollup never re-derives dimensions.
ALTER TABLE benchmark_org_metrics
    ADD COLUMN IF NOT EXISTS cohort_key TEXT;

-- ----------------------------------------------------------
-- RLS: framework + serving tables are service_role only.
-- The promotion job, the toll worker, and the data API are the sole
-- readers/writers. NO JWT policies are created: with RLS enabled and no
-- policies, every direct role gets deny-all by default. The data API is
-- the policy (it enforces key.niche_scope before serving).
-- ----------------------------------------------------------

ALTER TABLE data_sources       ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_ingest_runs   ENABLE ROW LEVEL SECURITY;
ALTER TABLE bronze_artifacts   ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE disputes           ENABLE ROW LEVEL SECURITY;
ALTER TABLE dead_letters       ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_api_plans     ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_api_keys      ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_api_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_toll_dedupe   ENABLE ROW LEVEL SECURITY;
ALTER TABLE toll_emit_queue    ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_api_usage     ENABLE ROW LEVEL SECURITY;

-- ----------------------------------------------------------
-- LINEAGE READ FUNCTIONS (TW-564 Major 1, named mechanisms)
-- ----------------------------------------------------------

-- Org-private lineage: the org reads its OWN rows' full lineage.
-- Asserts org_id = auth_org_id() before returning artifact-level events.
CREATE OR REPLACE FUNCTION data_events_org_lineage(silver_table text, silver_id uuid)
RETURNS SETOF data_events
LANGUAGE plpgsql SECURITY DEFINER STABLE AS $$
DECLARE
    v_org_id uuid;
BEGIN
    IF silver_table = 'benchmark_org_metrics' THEN
        SELECT bom.org_id INTO v_org_id
        FROM benchmark_org_metrics AS bom
        WHERE bom.id = data_events_org_lineage.silver_id;
    ELSE
        RAISE EXCEPTION 'lineage not available for shared-asset table %', silver_table;
    END IF;
    IF v_org_id IS NULL OR v_org_id <> auth_org_id() THEN
        RAISE EXCEPTION 'not authorized for this lineage';
    END IF;
    RETURN QUERY
    SELECT * FROM data_events
    WHERE data_events.silver_table = data_events_org_lineage.silver_table
      AND data_events.silver_id = data_events_org_lineage.silver_id
    ORDER BY created_at;
END;
$$;

-- Shared-asset lineage: terminates at the promotion batch. Batch-level
-- rows only (counts, timestamps, contract versions) — never artifact_id,
-- never per-row contributor drill-down.
CREATE OR REPLACE FUNCTION data_events_cohort(promotion_batch_id uuid)
RETURNS TABLE (
    event_type text, actor text, contract_version int,
    event_count bigint, first_at timestamptz, last_at timestamptz
)
LANGUAGE sql SECURITY DEFINER STABLE AS $$
    SELECT event_type, actor, contract_version,
           COUNT(*), MIN(created_at), MAX(created_at)
    FROM data_events
    WHERE data_events.promotion_batch_id = data_events_cohort.promotion_batch_id
      AND artifact_id IS NULL
    GROUP BY event_type, actor, contract_version;
$$;

-- ----------------------------------------------------------
-- SEED: the byproduct source + the benchmarks pay-per-use plan
-- ----------------------------------------------------------

INSERT INTO data_sources (niche, partner_name, source_kind, feed_mode, contract_version, status)
SELECT 'trades_benchmarks', 'lbt-os product (byproduct)', 'byproduct', 'batch', 1, 'active'
WHERE NOT EXISTS (
    SELECT 1 FROM data_sources
    WHERE niche = 'trades_benchmarks' AND source_kind = 'byproduct'
);

-- benchmarks_payg: pay-per-use with DYNAMIC volume tiers (the LAW).
-- Rates step DOWN as aggregate platform volume crosses thresholds.
-- Declared in-plan: no surprise repricing.
INSERT INTO data_api_plans (name, base_cents, per_call_cents, derivative_type, derivative_params)
SELECT 'benchmarks_payg', 0, 25, NULL,
       '{"volume_tiers": [
           {"upto_calls": 1000,  "per_call_cents": 25, "tier": "tier_0"},
           {"upto_calls": 10000, "per_call_cents": 20, "tier": "tier_1"},
           {"upto_calls": null,  "per_call_cents": 15, "tier": "tier_2"}
       ]}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM data_api_plans WHERE name = 'benchmarks_payg');
