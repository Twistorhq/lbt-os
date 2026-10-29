-- =============================================================
-- TW-768 follow-up (merge-time reconciliation, 2026-10-29):
-- foreign keys from the permission ledger to the agent identity
-- record.
--
-- The TW-768 migration carried agent_identity_id BY VALUE on every
-- agt_ row because Spec 1's table did not exist yet, and stated
-- the reconciliation in its header: "the FK is added there or in
-- a follow-up once that table exists." TW-767 has now merged, so
-- agt_identities exists and this file is that follow-up.
--
-- Delete semantics follow the ledger's fail-closed doctrine:
-- - agt_grants is CURRENT STATE. Its FK cascades: if an identity
--   row is deleted, its grants die with it. The alternative
--   (SET NULL) would silently widen a per-agent grant into an
--   org-wide one, and no grant = denied is the safe direction.
-- - agt_permission_events is append-only history; agt_revocations
--   carries the purge clock; agt_retained_copies carries purge
--   obligations. Their FKs SET NULL: deleting an identity row
--   must never erase consent history, un-ring a revocation, or
--   silently drop a pending purge.
--
-- Run order: AFTER migration_agent_identity_v1.sql and
-- migration_agent_permissions_tw768.sql (the filename sorts
-- after both). Idempotent: safe to re-run (add-if-missing,
-- mirroring the cap-law DO block style of the TW-768 migration).
-- Adding an FK over orphan agent_identity_id values fails loud,
-- which is the point: orphans surface instead of gaining a pass.
-- =============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_grants_agent_identity_fk'
    ) THEN
        ALTER TABLE agt_grants
            ADD CONSTRAINT agt_grants_agent_identity_fk
            FOREIGN KEY (agent_identity_id) REFERENCES agt_identities(id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_permission_events_agent_identity_fk'
    ) THEN
        ALTER TABLE agt_permission_events
            ADD CONSTRAINT agt_permission_events_agent_identity_fk
            FOREIGN KEY (agent_identity_id) REFERENCES agt_identities(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_revocations_agent_identity_fk'
    ) THEN
        ALTER TABLE agt_revocations
            ADD CONSTRAINT agt_revocations_agent_identity_fk
            FOREIGN KEY (agent_identity_id) REFERENCES agt_identities(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_retained_copies_agent_identity_fk'
    ) THEN
        ALTER TABLE agt_retained_copies
            ADD CONSTRAINT agt_retained_copies_agent_identity_fk
            FOREIGN KEY (agent_identity_id) REFERENCES agt_identities(id) ON DELETE SET NULL;
    END IF;
END $$;
