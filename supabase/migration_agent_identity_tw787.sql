-- =============================================================
-- TW-787 Spec 1 amendment: MULTI-AGENT per shop (roster).
--
-- TW-767 landed agt_identities with UNIQUE (org_id): exactly one
-- agent per shop. Justynn's caveat, recorded the day Spec 1 gated,
-- lifts that: the subscription includes ONE agent (the everything
-- agent); additional agents are hires, each with its own identity,
-- role, and archetype slot. This migration evolves the TW-767
-- schema in place — the v1 file is untouched (the TW-768 FK
-- follow-up pattern).
--
-- Changes:
-- - agent_slug: the agent's per-org slug. Identity is (org, slug);
--   email allocation derives from it (service layer). The legacy
--   single row per org backfills as slug 'primary', which the
--   service maps to the original TW-767 email formula, so no
--   already-issued address ever moves.
-- - included_with_subscription: TRUE for the subscription's one
--   included agent; hires are FALSE and carry the agent.hire toll.
-- - role: the agent's task seat, pinned to the roster vocabulary
--   (mirrors AGENT_ROLES in the model layer; the CHECK keeps the
--   schema and the gate from drifting apart, TW-768 cap-law style).
-- - archetype_key: the TW-788 archetype slot. NULL = generalist.
--   Format is validated in the model layer; the catalog is TW-788.
-- - The one-per-org unique becomes one-per-(org, slug). The global
--   UNIQUE (email_address) is untouched: one address, one agent.
-- - agt_audit's action vocabulary grows the roster actions.
--
-- NO ROSTER CAP: nothing here (or in the service layer) limits how
-- many agents an org may hire — Justynn's ruling, Oct 29, 2026.
--
-- Run order: AFTER migration_agent_identity_v1.sql. Idempotent:
-- safe to re-run (add-if-missing throughout, backfill guarded on
-- agent_slug IS NULL).
-- =============================================================

ALTER TABLE agt_identities
    ADD COLUMN IF NOT EXISTS agent_slug TEXT;
ALTER TABLE agt_identities
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'everything';
ALTER TABLE agt_identities
    ADD COLUMN IF NOT EXISTS archetype_key TEXT;
ALTER TABLE agt_identities
    ADD COLUMN IF NOT EXISTS included_with_subscription BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill: pre-amendment there is at most one row per org, and it
-- IS the subscription's included agent.
UPDATE agt_identities
SET agent_slug = 'primary',
    included_with_subscription = TRUE
WHERE agent_slug IS NULL;

ALTER TABLE agt_identities
    ALTER COLUMN agent_slug SET NOT NULL;

-- Roster uniqueness: one row per (org, agent slug).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_identities_org_id_key'
    ) THEN
        ALTER TABLE agt_identities DROP CONSTRAINT agt_identities_org_id_key;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_identities_org_agent_slug_key'
    ) THEN
        ALTER TABLE agt_identities
            ADD CONSTRAINT agt_identities_org_agent_slug_key
            UNIQUE (org_id, agent_slug);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_agt_identities_org_slug
    ON agt_identities(org_id, agent_slug);

-- Role vocabulary, pinned in the schema (mirrors the model layer).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_identities_role_check'
    ) THEN
        ALTER TABLE agt_identities
            ADD CONSTRAINT agt_identities_role_check
            CHECK (role IN (
                'everything', 'general', 'hr', 'sales', 'marketing',
                'ads', 'seo', 'inbox', 'payroll', 'frontend_dev',
                'backend_dev', 'executive'
            ));
    END IF;
END $$;

-- Audit vocabulary: the registry's actions plus the roster's.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_audit_action_check'
    ) THEN
        ALTER TABLE agt_audit DROP CONSTRAINT agt_audit_action_check;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_audit_action_check'
    ) THEN
        ALTER TABLE agt_audit
            ADD CONSTRAINT agt_audit_action_check
            CHECK (action IN (
                'provision', 'persona_update', 'coverage_update',
                'phone_bind', 'status_change', 'inbound_enqueued',
                'agent_hire', 'role_assign', 'archetype_assign'
            ));
    END IF;
END $$;
