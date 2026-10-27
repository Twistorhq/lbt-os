-- TW-284 AI phone automation (Path A: Twilio + OpenAI Realtime).
-- Settings + emergency contacts + AI call sessions + recording audit +
-- escalation chains/events. PII doctrine (TW-552): raw phone numbers live
-- ONLY in bronze columns here (caller_number, phone_number); every
-- API-facing list masks them. Every table ships its org-isolation RLS
-- policy (TW-547 contract), enabled explicitly (TW-710 lesson).

CREATE TABLE IF NOT EXISTS phone_automation_settings (
    org_id              UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    enabled             BOOLEAN NOT NULL DEFAULT FALSE,  -- shop master toggle
    after_hours_auto    BOOLEAN NOT NULL DEFAULT TRUE,
    timezone            TEXT NULL,               -- NULL = derive from org state
    open_days           INT[] NOT NULL DEFAULT ARRAY[1,2,3,4,5],  -- ISO Mon=1
    open_time           TEXT NOT NULL DEFAULT '07:00',
    close_time          TEXT NOT NULL DEFAULT '19:00',
    manual_active_until TIMESTAMPTZ NULL,        -- on-demand activation window
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS emergency_contacts (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone_number TEXT NOT NULL,                  -- bronze: raw, escalation needs it
    phone_hash   TEXT NOT NULL,                  -- SHA-256, matching without PII
    label        TEXT NULL,
    priority     INT NOT NULL DEFAULT 99,        -- rung in this order
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_emergency_contacts_org_priority
    ON emergency_contacts (org_id, priority);

CREATE TABLE IF NOT EXISTS ai_call_sessions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    twilio_call_sid     TEXT UNIQUE NOT NULL,
    caller_number       TEXT NOT NULL,           -- bronze only
    caller_hash         TEXT NOT NULL,
    customer_id         UUID NULL REFERENCES customers(id),
    status              TEXT NOT NULL DEFAULT 'in_progress',
    ai_disclosure_given BOOLEAN NOT NULL DEFAULT FALSE,
    disclosure_at       TIMESTAMPTZ NULL,
    recording_enabled   BOOLEAN NOT NULL DEFAULT FALSE,
    intake              JSONB NULL,
    job_id              UUID NULL REFERENCES jobs(id),
    appointment_id      UUID NULL REFERENCES appointments(id),
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_org_started
    ON ai_call_sessions (org_id, started_at DESC);

CREATE TABLE IF NOT EXISTS call_recording_events (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    session_id        UUID NOT NULL REFERENCES ai_call_sessions(id) ON DELETE CASCADE,
    event             TEXT NOT NULL,   -- recording_enabled | recording_disabled
    announcement_text TEXT NULL,       -- spoken text when recording switched on
    announced_at      TIMESTAMPTZ NULL,
    actor             TEXT NOT NULL,   -- owner | agent | system
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS escalation_chains (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    session_id      UUID NULL REFERENCES ai_call_sessions(id) ON DELETE SET NULL,
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | acknowledged | exhausted (final fallback)
    acknowledged_by UUID NULL REFERENCES emergency_contacts(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS escalation_events (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id     UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    chain_id   UUID NOT NULL REFERENCES escalation_chains(id) ON DELETE CASCADE,
    contact_id UUID NULL REFERENCES emergency_contacts(id) ON DELETE SET NULL,
    attempt_no INT NULL,
    round      INT NULL,
    channel    TEXT NOT NULL,          -- call | ack | system
    status     TEXT NOT NULL,          -- sent | acknowledged | exhausted
    detail     TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_escalation_events_chain
    ON escalation_events (chain_id, attempt_no);

-- RLS: org isolation, same contract as call_logs (TW-547). Policies are
-- INERT unless RLS is enabled — enable explicitly.
ALTER TABLE phone_automation_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE emergency_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_call_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_recording_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE escalation_chains ENABLE ROW LEVEL SECURITY;
ALTER TABLE escalation_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY "phone_automation_settings_org_isolation" ON phone_automation_settings
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "emergency_contacts_org_isolation" ON emergency_contacts
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "ai_call_sessions_org_isolation" ON ai_call_sessions
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "call_recording_events_org_isolation" ON call_recording_events
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "escalation_chains_org_isolation" ON escalation_chains
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "escalation_events_org_isolation" ON escalation_events
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
