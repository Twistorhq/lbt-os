-- Prepared by Twistor Holdings LLC
-- =============================================================
-- TW-953 (TW-948-B5): preflight, durable fire identity,
-- interruption recovery, and exactly-once reconciliation for
-- the persistent job service.
--
-- What this adds on the B1/B4 tables:
--   * agt_jobs gains the preflight surface (credential_refs,
--     action_classes, delivery_channel) and the block/review
--     markers (blocked_reason, blocked_at, blocked_alert_id,
--     blocked_alert_error, review_reason, review_fire_id).
--   * agt_job_fires gains the durable delivery handoff receipt
--     (delivery_receipt, delivery_status), the reconciliation
--     stamp (reconciled_at), and the 'interrupted' status.
--   * agt_jobs gains the 'review' status (pause-for-review
--     after an interrupted fire; never auto-resumed).
--   * agt_job_fire_steps: the durable per-step journal
--     (started / executed, toll pending / tolled / toll_failed)
--     that reconciliation reads as evidence.
--   * agt_job_tick_heartbeats: the durable tick heartbeat
--     singleton (last tick, outcome, counts, health alert
--     marker) so a dead scheduler is visible in-system.
--
-- FILES ONLY: this migration is documented and reversible and
-- is NEVER applied by this ticket. Application is gated on
-- Justynn's separate word, per the TW-949 / TW-952 precedent.
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- Run order: AFTER migration_persistence_tw949.sql and
-- migration_agent_jobs_tw952.sql.
-- =============================================================

-- ---------------------------------------------------------------
-- Job preflight surface and block/review markers.
-- ---------------------------------------------------------------
ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS credential_refs JSONB NOT NULL DEFAULT '[]';

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS action_classes JSONB NOT NULL DEFAULT '[]';

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS delivery_channel TEXT;

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS blocked_reason TEXT;

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS blocked_at TIMESTAMPTZ;

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS blocked_alert_id TEXT;

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS blocked_alert_error TEXT;

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS review_reason TEXT;

ALTER TABLE agt_jobs
    ADD COLUMN IF NOT EXISTS review_fire_id UUID
        REFERENCES agt_job_fires(id) ON DELETE SET NULL;

COMMENT ON COLUMN agt_jobs.credential_refs IS
    'TW-953: credential references preflight must resolve through the just-in-time resolver before any fire.';
COMMENT ON COLUMN agt_jobs.action_classes IS
    'TW-953: permission-ledger action classes the job prompt can reach; preflight checks every one.';
COMMENT ON COLUMN agt_jobs.blocked_reason IS
    'TW-953: masked preflight failure that blocked this job. Exactly one owner alert per block (blocked_alert_id).';

-- ---------------------------------------------------------------
-- Status vocabulary: jobs gain 'review', fires gain
-- 'interrupted'. The B1 CHECK constraints are replaced with
-- the widened lists (same values plus the B5 additions).
-- ---------------------------------------------------------------
ALTER TABLE agt_jobs DROP CONSTRAINT IF EXISTS agt_jobs_status_check;
ALTER TABLE agt_jobs ADD CONSTRAINT agt_jobs_status_check
    CHECK (status IN ('active', 'paused', 'blocked', 'archived', 'review'));

ALTER TABLE agt_job_fires DROP CONSTRAINT IF EXISTS agt_job_fires_status_check;
ALTER TABLE agt_job_fires ADD CONSTRAINT agt_job_fires_status_check
    CHECK (status IN ('pending', 'running', 'completed', 'failed',
                      'cancelled', 'blocked', 'interrupted'));

-- ---------------------------------------------------------------
-- Fire delivery handoff and reconciliation stamp.
-- ---------------------------------------------------------------
ALTER TABLE agt_job_fires
    ADD COLUMN IF NOT EXISTS delivery_receipt TEXT;

ALTER TABLE agt_job_fires
    ADD COLUMN IF NOT EXISTS delivery_status TEXT;

ALTER TABLE agt_job_fires
    ADD COLUMN IF NOT EXISTS reconciled_at TIMESTAMPTZ;

COMMENT ON COLUMN agt_job_fires.delivery_receipt IS
    'TW-953: durable delivery handoff receipt. A fire completes only when this is recorded.';

-- ---------------------------------------------------------------
-- Step journal. One row per (fire, step): the evidence
-- reconciliation classifies from. status moves started ->
-- executed; toll_status moves pending -> tolled / toll_failed
-- ('none' for steps with no toll event). A row stuck at
-- 'started' for a non-idempotent step is the unknown outcome:
-- surfaced, never auto-retried.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_job_fire_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    fire_id UUID NOT NULL REFERENCES agt_job_fires(id) ON DELETE CASCADE,
    fire_key TEXT NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'executed')),
    toll_status TEXT NOT NULL DEFAULT 'none'
        CHECK (toll_status IN ('none', 'pending', 'tolled', 'toll_failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (fire_id, step_name)
);
CREATE INDEX IF NOT EXISTS idx_agt_job_fire_steps_fire
    ON agt_job_fire_steps(fire_id);
CREATE INDEX IF NOT EXISTS idx_agt_job_fire_steps_org_agent
    ON agt_job_fire_steps(org_id, agent_identity_id, created_at DESC);

-- ---------------------------------------------------------------
-- Tick heartbeat. One singleton row (id 'agent-jobs-ticker')
-- updated by every tick, including failed scans, so scheduler
-- death is durable in-system evidence, not a caller's 500.
-- health_alerted_for keys the once-per-episode stale alert.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agt_job_tick_heartbeats (
    id TEXT PRIMARY KEY,
    last_tick_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'never',
    fired_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    health_alerted_for TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------
-- RLS: deny-all, same house pattern as B1 (service role only;
-- the backend filters org, agent, and workspace manually).
-- ---------------------------------------------------------------
ALTER TABLE agt_job_fire_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_job_tick_heartbeats ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_job_fire_steps_deny_all'
    ) THEN
        CREATE POLICY agt_job_fire_steps_deny_all
            ON agt_job_fire_steps FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE policyname = 'agt_job_tick_heartbeats_deny_all'
    ) THEN
        CREATE POLICY agt_job_tick_heartbeats_deny_all
            ON agt_job_tick_heartbeats FOR ALL USING (false);
    END IF;
END $$;

-- =============================================================
-- DOWN section (manual rollback). Nothing here runs on
-- re-apply; it is the documented reversal.
-- ALTER TABLE agt_job_fires DROP COLUMN IF EXISTS reconciled_at;
-- ALTER TABLE agt_job_fires DROP COLUMN IF EXISTS delivery_status;
-- ALTER TABLE agt_job_fires DROP COLUMN IF EXISTS delivery_receipt;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS review_fire_id;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS review_reason;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS blocked_alert_error;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS blocked_alert_id;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS blocked_at;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS blocked_reason;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS delivery_channel;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS action_classes;
-- ALTER TABLE agt_jobs DROP COLUMN IF EXISTS credential_refs;
-- (agt_job_fire_steps and agt_job_tick_heartbeats are dropped
-- by hand if a full rollback is ever ordered; their rows are
-- evidence and are not dropped by this ticket.)
-- =============================================================
