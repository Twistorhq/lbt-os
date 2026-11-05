-- Prepared by Twistor Holdings LLC
-- =============================================================
-- TW-954 (TW-948-B7, policy/floor half): unattended-operation
-- policy storage for the Tori persistence MVP in lbt-os.
--
-- FILE ONLY. Never applied by the author; application is a
-- separate, owner-gated step, exactly as TW-949's migration.
--
-- What lands here (additive only):
--   agt_unattended_policies — one row per (org, agent,
--     workspace): whether unattended operation is enabled
--     (DEFAULT FALSE, deny by default), the permitted job /
--     tool / action classes, and the mandate scope the owner
--     stated. Consulted by backend/app/agentperms/policy.py
--     AFTER the hard floor (code) and the ledger (TW-768).
--   agt_grants.extends_unattended — one additive column on the
--     TW-768 ledger table (DEFAULT FALSE): a grant records on
--     its own face whether it extends to unattended work. An
--     attended grant with this FALSE never authorizes
--     unattended work, whatever the policy row says.
--
-- What deliberately does NOT land here: any representation of
-- the hard floor. The floor (revocation supremacy, no DNC
-- override under TW-943, phone only through the TW-884 lane
-- under TW-885, no external send without mandate, no payment
-- beyond mandate, toll metering on tolled actions) is CODE in
-- backend/app/agentperms/floor.py. No column in this file can
-- switch it off, because no column describes it.
--
-- Scoping law (B1): the policy table carries org_id AND
-- agent_identity_id AND workspace_id; the backend filters all
-- three on every query and refuses a caller-supplied
-- workspace_id that differs from the derived key.
-- =============================================================

CREATE TABLE IF NOT EXISTS agt_unattended_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    permitted_job_classes JSONB NOT NULL DEFAULT '[]',
    permitted_tool_classes JSONB NOT NULL DEFAULT '[]',
    permitted_action_classes JSONB NOT NULL DEFAULT '[]',
    mandate_scope JSONB NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, agent_identity_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_agt_unattended_policies_scope
    ON agt_unattended_policies(org_id, agent_identity_id, workspace_id);

ALTER TABLE agt_grants ADD COLUMN IF NOT EXISTS extends_unattended BOOLEAN NOT NULL DEFAULT FALSE;

-- ---------------------------------------------------------------
-- RLS: deny-all. The service role (backend) is the only
-- reader/writer; the backend filters org, agent, and workspace
-- manually on every query.
-- ---------------------------------------------------------------
ALTER TABLE agt_unattended_policies ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_unattended_policies_deny_all'
    ) THEN
        CREATE POLICY agt_unattended_policies_deny_all ON agt_unattended_policies FOR ALL USING (false);
    END IF;
END $$;

-- =============================================================
-- DOWN section (manual rollback, reverse order). Nothing here
-- is executed by any tool; it documents the reversal.
--
-- DROP POLICY IF EXISTS agt_unattended_policies_deny_all ON agt_unattended_policies;
-- ALTER TABLE agt_grants DROP COLUMN IF EXISTS extends_unattended;
-- DROP TABLE IF EXISTS agt_unattended_policies;
-- =============================================================
