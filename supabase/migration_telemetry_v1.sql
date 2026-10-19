-- =============================================================
-- TW-622 Twistor Telemetry V1: product analytics tables.
--
-- Pricing note (TW-630 wedge): capture is NOT per-event tolled. The flat
-- subscription covers capture up to the org's monthly tier; over the tier,
-- ingestion SAMPLES at a recorded rate (graceful degradation, never
-- silence, never a surprise bill). There is deliberately no per-event
-- meter anywhere in this schema.
--
-- PII note (TW-552): distinct_id is HMAC-hashed and properties are
-- scrubbed BEFORE insert (see app/telemetry/scrub.py). Raw PII never
-- reaches tlm_events. Query results carry aggregates only.
--
-- RLS deny-all (service role only). Idempotent: safe to re-run
-- (IF NOT EXISTS throughout).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Raw events (scrubbed at ingestion). One row per captured event;
-- sampled rows carry sampled=true and their sample_rate.
CREATE TABLE IF NOT EXISTS tlm_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    event_name TEXT NOT NULL,
    distinct_id_hash TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sampled BOOLEAN NOT NULL DEFAULT FALSE,
    sample_rate REAL NOT NULL DEFAULT 1.0 CHECK (sample_rate > 0 AND sample_rate <= 1)
);
CREATE INDEX IF NOT EXISTS idx_tlm_events_org_event_time
    ON tlm_events(org_id, event_name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tlm_events_org_time
    ON tlm_events(org_id, occurred_at DESC);

-- Saved insights: named trend/funnel/retention/breakdown definitions.
-- Versioned; every mutation attributed.
CREATE TABLE IF NOT EXISTS tlm_insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('trend', 'funnel', 'retention', 'breakdown')),
    definition JSONB NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, key)
);
CREATE INDEX IF NOT EXISTS idx_tlm_insights_org ON tlm_insights(org_id);

-- Monthly event budgets. The tier counts RECEIVED events (honest
-- metering — sampling never hides usage). warn_sent latches per period.
CREATE TABLE IF NOT EXISTS tlm_budgets (
    org_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    monthly_cap BIGINT NOT NULL DEFAULT 10000000,
    used BIGINT NOT NULL DEFAULT 0,
    period_start DATE NOT NULL DEFAULT CURRENT_DATE,
    warn_sent BOOLEAN NOT NULL DEFAULT FALSE
);

-- RLS: deny-all. The service role (backend) is the only writer/reader.
ALTER TABLE tlm_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tlm_insights ENABLE ROW LEVEL SECURITY;
ALTER TABLE tlm_budgets ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'tlm_events_deny_all'
    ) THEN
        CREATE POLICY tlm_events_deny_all ON tlm_events FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'tlm_insights_deny_all'
    ) THEN
        CREATE POLICY tlm_insights_deny_all ON tlm_insights FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'tlm_budgets_deny_all'
    ) THEN
        CREATE POLICY tlm_budgets_deny_all ON tlm_budgets FOR ALL USING (false);
    END IF;
END $$;
