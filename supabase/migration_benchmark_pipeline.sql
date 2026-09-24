-- Benchmark aggregation pipeline (TW-201)
-- Consent-based, anonymized. Powers "shops shaped like yours" comparisons.
-- Run after migration_service_datamodel.sql.

-- Explicit opt-in. No consent, no aggregation. Ever.
ALTER TABLE organizations
    ADD COLUMN IF NOT EXISTS benchmark_consent BOOLEAN NOT NULL DEFAULT FALSE;

-- Per-org private metric snapshots. Raw material, never exposed cross-org.
CREATE TABLE IF NOT EXISTS benchmark_org_metrics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    vertical    TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    sample_size INTEGER,
    period      TEXT NOT NULL DEFAULT '30d',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bom_org_metric
    ON benchmark_org_metrics(org_id, metric_name, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_bom_vertical_metric
    ON benchmark_org_metrics(vertical, metric_name, computed_at DESC);

-- The ONLY cross-org table. A cohort row is written only when >= 5
-- consenting orgs contribute (k-anonymity, enforced by the writer).
CREATE TABLE IF NOT EXISTS benchmark_cohort_stats (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cohort_key  TEXT NOT NULL,   -- e.g. 'hvac:smb'
    metric_name TEXT NOT NULL,
    p50         DOUBLE PRECISION NOT NULL,
    mean        DOUBLE PRECISION NOT NULL,
    n_orgs      INTEGER NOT NULL,
    period      TEXT NOT NULL DEFAULT '30d',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cohort_key, metric_name, period)
);
CREATE INDEX IF NOT EXISTS idx_bcs_cohort
    ON benchmark_cohort_stats(cohort_key, metric_name, period DESC);
