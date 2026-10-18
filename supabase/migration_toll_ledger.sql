-- =============================================================
-- TW-550: Micro-toll ledger (Twistor staple, TW-549)
-- Every metered action in the system emits a toll record here.
-- M1 ships the ledger; M2 (missed-call) / M3 (BI) emit into it.
-- Run once against your Supabase project.
-- =============================================================

CREATE TABLE IF NOT EXISTS toll_events (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           UUID        NOT NULL REFERENCES organizations(id),
    event_type       TEXT        NOT NULL,   -- e.g. 'missed_call.detected'
    quantity         NUMERIC     NOT NULL DEFAULT 1,
    unit_rate_cents  INTEGER     NOT NULL,   -- from TOLL_RATES, or emitter-supplied
    amount_cents     INTEGER     NOT NULL,   -- quantity * unit_rate_cents, or emitter amount
    idempotency_key  TEXT        NOT NULL UNIQUE,  -- no double-toll, ever
    source_type      TEXT,       -- emitter's table, e.g. 'call_log'
    source_id        TEXT,       -- emitter's row id
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_toll_events_org_occurred
    ON toll_events (org_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_toll_events_org_type
    ON toll_events (org_id, event_type);

-- RLS: org isolation, same contract as rls_policies.sql (TW-547 §6.4).
-- No cross-org reads: a shop sees only its own tolls.
ALTER TABLE toll_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY "toll_events_org_isolation" ON toll_events
    FOR ALL USING (org_id = auth_org_id())
    WITH CHECK (org_id = auth_org_id());
