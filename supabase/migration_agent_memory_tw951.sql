-- TW-951 (TW-948-B2): bounded curated memory with receipts.
--
-- Additive to migration_persistence_tw949.sql (B1). Three new
-- agt_ tables carry the Section 8.1 memory contract:
--   agt_memory_entries        the live curated collections
--   agt_memory_audit_events   the append-only receipt trail
--   agt_memory_gate_settings  the per-Tori approval gate mode
-- Staging itself stays on B1's agt_staged_writes (kind
-- 'memory'); this migration adds no staging shape.
--
-- Entry bodies are stored in masked projection shape only
-- (body_masked), the same discipline as B1 staged writes:
-- there is no bronze column on these tables by design.
-- Collection bounds (entry counts and total characters) are
-- enforced in the service on the batch result of a write;
-- they span rows, so they cannot be a single-row CHECK.
--
-- DOWN section (documented reversal, not executed by the
-- migration runner): run in reverse order after B2 code is
-- rolled back:
--   DROP TABLE IF EXISTS agt_memory_gate_settings;
--   DROP TABLE IF EXISTS agt_memory_audit_events;
--   DROP TABLE IF EXISTS agt_memory_entries;

-- ---------------------------------------------------------------
-- Curated memory entries. One row per live entry, unique per
-- (org, agent, collection, entry_key); version increments on
-- every applied update; receipt_id and staged_write_id name
-- the approved write that produced the current version.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_memory_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    collection TEXT NOT NULL
        CHECK (collection IN ('business_facts', 'owner_preferences')),
    entry_key TEXT NOT NULL,
    body_masked TEXT NOT NULL DEFAULT '',
    body_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    staged_write_id UUID REFERENCES agt_staged_writes(id),
    receipt_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, agent_identity_id, collection, entry_key)
);
CREATE INDEX IF NOT EXISTS idx_agt_memory_entries_org_agent
    ON agt_memory_entries(org_id, agent_identity_id, collection);

-- ---------------------------------------------------------------
-- Memory audit events. Append-only: every approval decision
-- lands here, and an applied write's receipt id is recorded
-- with the version it produced, so a memory claim can be
-- verified against the trail (and only against the trail).
-- Gate-mode changes land here too (memory_gate_mode_changed,
-- with old mode, new mode, and actor in detail): relaxing the
-- gate is the most security-sensitive transition in this
-- feature, so it must never leave the trail blank. Gate events
-- have no staged write, collection, or entry, so those three
-- columns are nullable; write events always populate them.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_memory_audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN (
            'memory_write_applied',
            'memory_write_rejected',
            'memory_write_refused_stale',
            'memory_gate_mode_changed'
        )),
    staged_write_id UUID REFERENCES agt_staged_writes(id),
    receipt_id TEXT,
    collection TEXT
        CHECK (collection IN ('business_facts', 'owner_preferences')),
    entry_key TEXT,
    detail JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agt_memory_audit_org_agent
    ON agt_memory_audit_events(org_id, agent_identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agt_memory_audit_receipt
    ON agt_memory_audit_events(receipt_id);

-- ---------------------------------------------------------------
-- Gate settings. One row per (org, agent); no row means the
-- default, gated. Only owner actors write this table (the
-- service refuses every non-owner change), so the agent can
-- never relax its own gate or remove an owner's tightening.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_memory_gate_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    gate_mode TEXT NOT NULL DEFAULT 'gated'
        CHECK (gate_mode IN ('gated', 'relaxed')),
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, agent_identity_id)
);

-- ---------------------------------------------------------------
-- RLS: deny-all. The service role (backend) is the only
-- reader/writer; the backend filters org, agent, and workspace
-- manually on every query.
-- ---------------------------------------------------------------
ALTER TABLE agt_memory_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_memory_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_memory_gate_settings ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_memory_entries_deny_all'
    ) THEN
        CREATE POLICY agt_memory_entries_deny_all
            ON agt_memory_entries FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_memory_audit_events_deny_all'
    ) THEN
        CREATE POLICY agt_memory_audit_events_deny_all
            ON agt_memory_audit_events FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_memory_gate_settings_deny_all'
    ) THEN
        CREATE POLICY agt_memory_gate_settings_deny_all
            ON agt_memory_gate_settings FOR ALL USING (false);
    END IF;
END $$;
