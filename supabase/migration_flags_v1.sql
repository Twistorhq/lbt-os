-- =============================================================
-- TW-621 Twistor Flags V1: feature flag platform tables.
--
-- Pricing note (TW-630 wedge): evaluations are UNLIMITED on the flat
-- subscription. There is deliberately NO evaluation log table — logging
-- every evaluation would recreate the per-context meter LaunchDarkly
-- users hate. The audit table below is append-only: who flipped what,
-- when. No updates, no deletes on it, ever.
--
-- RLS deny-all (service role only). Flag definitions hold no PII —
-- targeting happens against caller-supplied context at eval time.
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Flag definitions. One key per org; the definition JSON is the
-- FlagDefinition model (variations, default_variation, targeting,
-- scheduled). Version bumps on every mutation.
CREATE TABLE IF NOT EXISTS flg_flags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    definition JSONB NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, key)
);
CREATE INDEX IF NOT EXISTS idx_flg_flags_org ON flg_flags(org_id);

-- Audit trail. Append-only: who flipped what, when.
CREATE TABLE IF NOT EXISTS flg_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    flag_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete', 'flip', 'schedule_applied')),
    actor TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_flg_audit_org_key ON flg_audit(org_id, flag_key, at DESC);

-- RLS: deny-all. The service role (backend) is the only writer/reader.
ALTER TABLE flg_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE flg_audit ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'flg_flags_deny_all'
    ) THEN
        CREATE POLICY flg_flags_deny_all ON flg_flags FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'flg_audit_deny_all'
    ) THEN
        CREATE POLICY flg_audit_deny_all ON flg_audit FOR ALL USING (false);
    END IF;
END $$;
