-- =============================================================
-- TW-787 permission ledger: per-agent grant scoping (schema half).
--
-- TW-768 landed agt_grants with UNIQUE (org_id, action_class): one
-- grant row per class per org, agent_identity_id carried by value
-- (FK added at the TW-768 merge reconciliation). The TW-787 roster
-- makes grants scopable per agent, so the unique becomes
-- (org_id, agent_identity_id, action_class).
--
-- NULLS NOT DISTINCT: the org-wide row carries agent_identity_id
-- NULL, and plain UNIQUE treats NULLs as distinct — two org-wide
-- rows for one class could coexist and the gate's pick would be
-- arbitrary. NULLS NOT DISTINCT (Postgres 15+) makes the org-wide
-- row unique too. The service layer resolves scope by picking the
-- acting agent's row first, else the org-wide row.
--
-- No other change: the cap-law CHECK constraints, the append-only
-- history, and the FK delete semantics are exactly as TW-768 left
-- them. Per-agent revocation shadowing lives in the service layer.
--
-- Run order: AFTER migration_agent_permissions_tw768.sql and
-- migration_agent_permissions_tw768_fk.sql (the filename sorts
-- after both). Idempotent: safe to re-run (add-if-missing).
-- =============================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_grants_org_id_action_class_key'
    ) THEN
        ALTER TABLE agt_grants DROP CONSTRAINT agt_grants_org_id_action_class_key;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_grants_org_agent_class_key'
    ) THEN
        ALTER TABLE agt_grants
            ADD CONSTRAINT agt_grants_org_agent_class_key
            UNIQUE NULLS NOT DISTINCT (org_id, agent_identity_id, action_class);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_agt_grants_org_agent
    ON agt_grants(org_id, agent_identity_id);
