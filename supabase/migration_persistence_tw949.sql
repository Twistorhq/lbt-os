-- Prepared by Twistor Holdings LLC
-- =============================================================
-- TW-949 (TW-948-B1): Tori persistence foundation in lbt-os.
--
-- ARCHITECTURE RECORD (summary; full record in
-- docs/evidence/TW-949-persistence-foundation.md): lbt-os is the
-- persistence HOME for Tori. Twistor Core is a PATTERN SOURCE
-- only: the DDL shapes below port patterns from Core workforce
-- migration 001 (schedule and memory shapes, GIN full-text index)
-- and Core docsearch (full-text design over chunks). No Core code
-- is imported as a runtime by this migration or its services.
--
-- Tables landed here (all agt_ prefixed, additive only):
--   agt_sessions, agt_session_messages, agt_jobs,
--   agt_job_fires, agt_job_continuity, agt_staged_writes.
--
-- Scoping law: every table carries org_id AND agent_identity_id
-- AND workspace_id. Workspace id is the per-agent workspace key
-- (B12 builds the contained file area on this key; B1 lands the
-- column so no later migration has to retrofit scoping).
-- The backend filters all three manually on every query
-- (service-role client bypasses RLS, database.py discipline),
-- and refuses any caller-supplied workspace_id that differs
-- from the key derived from (org_id, agent_identity_id)
-- (TW-949 fix round, Rosa must-fix 1).
--
-- PII doctrine (medallion, TW-552): agt_session_messages stores
-- the raw body ONLY in body_bronze (bronze layer, never returned
-- by any retrieval projection; retrieval queries select the
-- projection columns only, and the append return is the masked
-- projection, so bronze exits only via the named audit path).
-- body_hash and agt_staged_writes.proposed_body_hash are KEYED
-- hashes (HMAC-SHA256, key in the server environment only,
-- never stored in any row; TW-949 fix round, Rex finding F4):
-- a bare SHA-256 of a short body is dictionary-reversible and
-- is not used anywhere in this schema's services. body_masked
-- and search_text are the masked retrieval projection; the
-- full-text index is built on search_text (the masked
-- projection), never on body_bronze.
--
-- Idempotency shapes (house pattern, migration_toll_ledger.sql):
-- a fire carries a durable fire_key, UNIQUE, so a retried or
-- restarted run can never create a second fire for the same
-- scheduled identity. (job_id, scheduled_for) is also UNIQUE:
-- the same job cannot fire twice for the same scheduled minute
-- (Core ScheduleStore same-minute no-double-fire contract).
-- NOTE FOR THE B4 BUILDER: the global fire_key UNIQUE matches
-- the toll-ledger idempotency precedent and stays, so B4 must
-- namespace fire keys per job (for example job id plus the
-- scheduled slot) when it mints them.
--
-- RLS: deny-all on every table (the agt_ house pattern from
-- TW-768 / TW-710: service role only). No org policy is added
-- here because no anon or authenticated client reads these
-- tables; the toll ledger's org policy exists for a different
-- access shape and is deliberately not copied.
--
-- FILES ONLY: this migration is documented and reversible and
-- is NEVER applied by this ticket. Application is gated on
-- Justynn's separate word, per the TW-926 / TW-934 precedent.
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- Rollback is the DOWN section at the bottom (manual).
-- Run order: AFTER migration_agent_identity_v1.sql (agt_identities)
-- and migration_agent_identity_tw787.sql.
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------
-- Sessions. One row per conversation or work session.
-- parent_session_id is the lineage link: a compression or
-- continuation event creates a CHILD session pointing at its
-- parent, so search and audit can walk the full chain.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    parent_session_id UUID REFERENCES agt_sessions(id) ON DELETE SET NULL,
    channel TEXT NOT NULL DEFAULT 'chat',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'compressed', 'archived', 'failed')),
    title TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agt_sessions_org_agent
    ON agt_sessions(org_id, agent_identity_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agt_sessions_parent
    ON agt_sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_agt_sessions_workspace
    ON agt_sessions(org_id, agent_identity_id, workspace_id);

-- ---------------------------------------------------------------
-- Session messages. Append-only transcript rows.
-- body_bronze is the ONLY raw column (bronze layer).
-- body_masked / search_text are the masked retrieval projection.
-- seq is per-session order; UNIQUE (session_id, seq) makes a
-- retried append of the same position fail instead of doubling.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_session_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    session_id UUID NOT NULL REFERENCES agt_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    seq INTEGER NOT NULL CHECK (seq >= 1),
    body_bronze TEXT NOT NULL DEFAULT '',
    body_hash TEXT NOT NULL,
    body_masked TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_agt_session_messages_session
    ON agt_session_messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_agt_session_messages_org_agent
    ON agt_session_messages(org_id, agent_identity_id, created_at DESC);
-- Full-text index on the MASKED projection only (Core workforce
-- 001 GIN shape; Core docsearch FTS design). Never on body_bronze.
CREATE INDEX IF NOT EXISTS idx_agt_session_messages_search
    ON agt_session_messages USING gin (to_tsvector('english', search_text));

-- ---------------------------------------------------------------
-- Jobs. Persistent scheduled work definitions (B4 runs these;
-- B1 lands the shape). prompt is self-contained; skill_keys is
-- the ordered attached-skill list. A job row carries no
-- job-management capability: no-job-creates-job is enforced in
-- the B4 execution context, not here.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    skill_keys TEXT[] NOT NULL DEFAULT '{}',
    cron_expr TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'blocked', 'archived')),
    next_fire_at TIMESTAMPTZ,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, agent_identity_id, name)
);
CREATE INDEX IF NOT EXISTS idx_agt_jobs_org_agent_status
    ON agt_jobs(org_id, agent_identity_id, status);
CREATE INDEX IF NOT EXISTS idx_agt_jobs_next_fire
    ON agt_jobs(next_fire_at) WHERE status = 'active';

-- ---------------------------------------------------------------
-- Job fires. One row per durable fire of a job.
-- fire_key is the durable fire identity (idempotency key):
-- UNIQUE, so a duplicate fire id is rejected at the database
-- layer even if a writer bypasses the service layer.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_job_fires (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    job_id UUID NOT NULL REFERENCES agt_jobs(id) ON DELETE CASCADE,
    fire_key TEXT NOT NULL UNIQUE,
    scheduled_for TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'blocked')),
    task_id TEXT,
    summary TEXT NOT NULL DEFAULT '',
    error TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, scheduled_for)
);
CREATE INDEX IF NOT EXISTS idx_agt_job_fires_job
    ON agt_job_fires(job_id, scheduled_for DESC);
CREATE INDEX IF NOT EXISTS idx_agt_job_fires_org_agent
    ON agt_job_fires(org_id, agent_identity_id, created_at DESC);

-- ---------------------------------------------------------------
-- Job continuity. Exactly one record per job: what prior runs
-- already surfaced, so the next fire dedupes against its own
-- history (dedupe is data, not memory).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_job_continuity (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    job_id UUID NOT NULL REFERENCES agt_jobs(id) ON DELETE CASCADE,
    last_fire_id UUID REFERENCES agt_job_fires(id) ON DELETE SET NULL,
    last_run_summary TEXT NOT NULL DEFAULT '',
    surfaced_item_ids JSONB NOT NULL DEFAULT '[]',
    run_count INTEGER NOT NULL DEFAULT 0 CHECK (run_count >= 0),
    last_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id)
);
CREATE INDEX IF NOT EXISTS idx_agt_job_continuity_org_agent
    ON agt_job_continuity(org_id, agent_identity_id);

-- ---------------------------------------------------------------
-- Staged writes. Proposed memory and skill writes held for the
-- approval gate (B2 and B9 own the gate logic; B1 lands the
-- staging shape). pinned_target_hash pins the target entry the
-- proposal was written against; approval refuses when the live
-- entry no longer matches the pin (enforced in B2, shaped here).
-- Bodies here are the masked projection shape only: no bronze
-- column exists on this table by design.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_staged_writes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('memory', 'skill')),
    target_key TEXT NOT NULL,
    target_ref TEXT,
    pinned_target_hash TEXT,
    proposed_body_masked TEXT NOT NULL DEFAULT '',
    proposed_body_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'applied', 'refused_stale')),
    staged_by TEXT NOT NULL DEFAULT '',
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    receipt_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agt_staged_writes_org_agent_status
    ON agt_staged_writes(org_id, agent_identity_id, status, created_at DESC);

-- ---------------------------------------------------------------
-- RLS: deny-all. The service role (backend) is the only
-- reader/writer; the backend filters org, agent, and workspace
-- manually on every query.
-- ---------------------------------------------------------------
ALTER TABLE agt_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_session_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_job_fires ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_job_continuity ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_staged_writes ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_sessions_deny_all'
    ) THEN
        CREATE POLICY agt_sessions_deny_all ON agt_sessions FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_session_messages_deny_all'
    ) THEN
        CREATE POLICY agt_session_messages_deny_all ON agt_session_messages FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_jobs_deny_all'
    ) THEN
        CREATE POLICY agt_jobs_deny_all ON agt_jobs FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_job_fires_deny_all'
    ) THEN
        CREATE POLICY agt_job_fires_deny_all ON agt_job_fires FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_job_continuity_deny_all'
    ) THEN
        CREATE POLICY agt_job_continuity_deny_all ON agt_job_continuity FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_staged_writes_deny_all'
    ) THEN
        CREATE POLICY agt_staged_writes_deny_all ON agt_staged_writes FOR ALL USING (false);
    END IF;
END $$;

-- =============================================================
-- DOWN section (manual rollback, reverse order). Nothing here
-- runs on re-apply; it is the documented reversal for the day
-- an applied migration ever needs to come back out.
-- DROP TABLE IF EXISTS agt_staged_writes;
-- DROP TABLE IF EXISTS agt_job_continuity;
-- DROP TABLE IF EXISTS agt_job_fires;
-- DROP TABLE IF EXISTS agt_jobs;
-- DROP TABLE IF EXISTS agt_session_messages;
-- DROP TABLE IF EXISTS agt_sessions;
-- =============================================================
