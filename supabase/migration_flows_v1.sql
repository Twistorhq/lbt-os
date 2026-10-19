-- =============================================================
-- TW-625 Twistor Flows V1: workflow automation tables.
--
-- Trust domain note: flw_run_steps.output may contain rendered step
-- outputs. Recipient addresses are stored as sha256 prefix hashes only
-- (see executor._hash_recipient) — never raw, per the PII doctrine
-- (TW-552). RLS deny-all (service role only).
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Workflow definitions. definition is the validated JSON from
-- flows.models.validate_definition. status: active | paused.
CREATE TABLE IF NOT EXISTS flw_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    definition JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused')),
    paused_reason TEXT,
    last_warning TEXT,
    alert_to TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_flw_workflows_org
    ON flw_workflows(org_id);

-- Run ledger. One row per trigger; the receipt for the run.
CREATE TABLE IF NOT EXISTS flw_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workflow_id UUID NOT NULL REFERENCES flw_workflows(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'suspended', 'completed',
                          'halted', 'errored', 'blocked')),
    steps_executed INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    resume_at TIMESTAMPTZ,
    resume_step_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_flw_runs_workflow
    ON flw_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_flw_runs_resume
    ON flw_runs(status, resume_at) WHERE status = 'suspended';

-- Per-step ledger. output holds step results; recipient addresses are
-- hashed at write time (executor), never raw.
CREATE TABLE IF NOT EXISTS flw_run_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES flw_runs(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('ok', 'error', 'halted', 'circuit_broken')),
    output JSONB NOT NULL DEFAULT '{}',
    error TEXT,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_flw_run_steps_run
    ON flw_run_steps(run_id);

-- Daily budget counters. One row per workflow per UTC day; the 80/100
-- throttle reads this. warned_80 ensures one warning per day.
CREATE TABLE IF NOT EXISTS flw_budget_counters (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workflow_id UUID NOT NULL REFERENCES flw_workflows(id) ON DELETE CASCADE,
    day DATE NOT NULL,
    runs INTEGER NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    cost_cents INTEGER NOT NULL DEFAULT 0,
    warned_80 BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (org_id, workflow_id, day)
);

ALTER TABLE flw_workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE flw_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE flw_run_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE flw_budget_counters ENABLE ROW LEVEL SECURITY;
-- No policies: deny-all. Service role only, matching identity/notify.
