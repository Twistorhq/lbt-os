-- Internal product analytics event layer (TW-209 Phase 1, Dre Coleman).
--
-- PRIVACY BOUNDARY (hard rule — enforced by schema design, not just policy):
--   These tables store usage METADATA ONLY: which features an org uses, in
--   which vertical, and when. They NEVER store client business data:
--     * no job / quote / invoice contents or counts-as-money
--     * no customer PII (no names, emails, phones, addresses)
--     * no financial amounts (no dollars_at_stake, no revenue)
--   TEXT columns and their caller contracts (enforced in
--   backend/app/services/analytics.py, which drops anything else BEFORE the
--   write so a careless caller cannot leak PII into this schema by accident):
--     * vertical    — leak-engine vertical from the org lookup (e.g. 'hvac')
--     * feature_key — dotted lowercase path, validated by FEATURE_KEY_RE
--     * actor_role  — enum-ish: 'owner' | 'admin' | 'tech'; NEVER a user id
--     * session_id  — opaque token only; PII-shaped values (emails, blocked
--                     fragments, overlong) degrade to NULL at emit time
--     * context     — JSONB with allowlisted metadata keys only (result,
--                     duration_ms, source, metric, detector, ladder, count);
--                     string values are screened for PII-shaped content and
--                     metric/detector values must match identifier shape
--     * catalog description — maintainer-written reference text, never user
--                     input
--   The exact column sets are pinned by tests in
--   backend/tests/test_analytics_events.py — no PII-shaped column can sneak
--   in later without failing the suite.
--
-- ML / aggregate rollups (TW-209 Phase 3) read ONLY these tables. They must
-- NEVER query production tables. This schema is intentionally lean to fit
-- the Supabase free tier: two tables, three indexes, no partitions (yet).
-- Cross-vertical from day one: `vertical` is on every event row.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- The event log. One row per feature interaction.
CREATE TABLE IF NOT EXISTS analytics_feature_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    vertical    TEXT NOT NULL,          -- leak-engine vertical, e.g. 'hvac'
    feature_key TEXT NOT NULL,          -- dotted path, e.g. 'leak_brief.viewed'
    actor_role  TEXT,                   -- enum-ish: 'owner' | 'admin' | 'tech'; NEVER a user id
    session_id  TEXT,                   -- opaque token; never a login/email
    context     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- allowlisted metadata keys only (see header)
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_afe_org_time
    ON analytics_feature_events(org_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_afe_feature_time
    ON analytics_feature_events(feature_key, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_afe_vertical_feature_time
    ON analytics_feature_events(vertical, feature_key, occurred_at DESC);

-- Feature catalog: the dimension table Phase 3 rollups join against.
-- Lets ML answer "which features are heavily used / churning / deprecable"
-- without hardcoding feature keys in model code.
CREATE TABLE IF NOT EXISTS analytics_feature_catalog (
    feature_key TEXT PRIMARY KEY,       -- matches analytics_feature_events.feature_key
    name        TEXT NOT NULL,          -- human label, e.g. 'Morning Brief'
    ladder_rung TEXT NOT NULL
        CHECK (ladder_rung IN ('happened', 'will_happen', 'should_do', 'platform')),
    description TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the catalog with the features instrumented in Phase 1. More rows land
-- with Phase 2 wiring; the catalog is reference data, safe to extend.
-- NOTE: ON CONFLICT DO NOTHING means re-running this migration silently
-- ignores future description updates — descriptions are updated by a
-- dedicated migration, not by re-seeding.
INSERT INTO analytics_feature_catalog (feature_key, name, ladder_rung, description)
VALUES
    ('leak_brief.viewed',  'Morning Brief',          'happened',    'Daily leak-detection brief (question ladder)'),
    ('detectors.listed',   'Detector registry',      'platform',    'Which leak patterns are live for the org vertical'),
    ('benchmark.compare',  'Benchmark comparison',   'will_happen', 'Compare org against anonymized cohort')
ON CONFLICT (feature_key) DO NOTHING;

-- RLS: feature events are per-org private usage data (org isolation).
-- Written server-side via the service role; policies guard direct access.
ALTER TABLE analytics_feature_events ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'analytics_feature_events'
          AND policyname = 'org_isolation'
    ) THEN
        CREATE POLICY "org_isolation" ON analytics_feature_events
            FOR ALL USING (org_id = auth_org_id())
            WITH CHECK (org_id = auth_org_id());
    END IF;
END $$;

-- RLS: the catalog is reference data — readable by any authenticated org,
-- writable only server-side (no INSERT/UPDATE/DELETE policy for clients).
ALTER TABLE analytics_feature_catalog ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'analytics_feature_catalog'
          AND policyname = 'catalog_read'
    ) THEN
        CREATE POLICY "catalog_read" ON analytics_feature_catalog
            FOR SELECT USING (true);
    END IF;
END $$;
