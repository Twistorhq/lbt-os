-- TW-884 outbound sales agent (epic TW-883, slice 1: test mode only).
-- Legal posture per Angela's TW-885 gate memo: cold AI-voice dials go
-- only to verified business landlines; wireless/residential numbers
-- need a prior-express-written-consent row in outbound_consents;
-- suppression is one ledger (DNC requests, revocations from any
-- channel, registry hits) checked at dial time.
--
-- PII / medallion doctrine (TW-547): raw phone numbers and full
-- transcripts are BRONZE ONLY, hashed at ingestion (phone_hash),
-- masked everywhere else (phone_masked); nothing raw is reachable
-- from silver, gold, or any customer-facing surface (HR-19).

CREATE TABLE IF NOT EXISTS outbound_agent_settings (
    org_id              UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    enabled             BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_at          TIMESTAMPTZ NULL,
    persona_name        TEXT NULL,            -- TW-886 persona slot
    timezone            TEXT NOT NULL DEFAULT 'America/Denver',
    window_start        TEXT NOT NULL DEFAULT '08:00',
    window_end          TEXT NOT NULL DEFAULT '17:00',
    lunch_start         TEXT NULL DEFAULT '12:00',
    lunch_end           TEXT NULL DEFAULT '13:00',
    call_days           JSONB NOT NULL DEFAULT '[0,1,2,3,4]',
    daily_cap           INTEGER NOT NULL DEFAULT 25,
    demo_duration_min   INTEGER NOT NULL DEFAULT 20,
    demo_buffer_min     INTEGER NOT NULL DEFAULT 10,
    demo_window_start   TEXT NOT NULL DEFAULT '09:00',
    demo_window_end     TEXT NOT NULL DEFAULT '16:00',
    calendar_id         TEXT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbound_prospects (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    company             TEXT NULL,
    owner_name          TEXT NULL,
    city                TEXT NULL,
    state               TEXT NULL,
    timezone            TEXT NULL,
    phone               TEXT NOT NULL,        -- bronze only
    phone_hash          TEXT NOT NULL,
    phone_masked        TEXT NOT NULL,
    website             TEXT NULL,
    rank                INTEGER NULL,
    status              TEXT NOT NULL DEFAULT 'new',  -- new|in_cadence|engaged|converted|resting|dnc
    lane                TEXT NOT NULL DEFAULT 'agent', -- agent|consent_capture|human
    touch_count         INTEGER NOT NULL DEFAULT 0,
    cadence_start       DATE NULL,
    next_touch_at       TIMESTAMPTZ NULL,
    last_disposition    TEXT NULL,
    scrub_status        TEXT NOT NULL DEFAULT 'unscrubbed', -- unscrubbed|clear|registry_hit
    scrub_batch_id      TEXT NULL,
    line_type           TEXT NULL,            -- last carrier lookup (HR-05)
    no_record           BOOLEAN NOT NULL DEFAULT FALSE,     -- HR-17 objection flag
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, phone_hash)
);

CREATE TABLE IF NOT EXISTS outbound_call_attempts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    prospect_id         UUID NOT NULL REFERENCES outbound_prospects(id) ON DELETE CASCADE,
    session_id          UUID NULL REFERENCES ai_call_sessions(id) ON DELETE SET NULL,
    attempt_no          INTEGER NOT NULL DEFAULT 1,
    touch_day           INTEGER NULL,         -- cadence day: 0|3|7|12
    dialed              BOOLEAN NOT NULL DEFAULT TRUE,
    disposition         TEXT NULL,            -- demo_set|audit_booked|callback|not_interested|dnc|voicemail|no_answer|gatekeeper|reached|aborted|blocked_*
    transcript          JSONB NULL,           -- bronze only (HR-19)
    audit_answers       JSONB NOT NULL DEFAULT '{}',
    demo_event_id       TEXT NULL,
    recording_enabled   BOOLEAN NOT NULL DEFAULT FALSE,
    line_type           TEXT NULL,            -- lookup result per attempt (HR-05)
    carrier             TEXT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbound_suppression (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone               TEXT NOT NULL,        -- bronze only
    phone_hash          TEXT NOT NULL,
    phone_masked        TEXT NOT NULL,
    source              TEXT NOT NULL,        -- dnc_request|revocation|registry_hit|web_optout
    reason              TEXT NULL,            -- the words they used
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, phone_hash)
);

CREATE TABLE IF NOT EXISTS outbound_consents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone               TEXT NOT NULL,        -- bronze only
    phone_hash          TEXT NOT NULL,
    seller_name         TEXT NOT NULL DEFAULT 'Twistor',
    scope_ai_voice      BOOLEAN NOT NULL DEFAULT FALSE,  -- consent covers AI/automated voice (HR-07)
    source              TEXT NULL,            -- web_form|inbound_call|prior_call
    evidence_ref        TEXT NULL,
    granted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at          TIMESTAMPTZ NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbound_scrub_runs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    batch_id            TEXT NOT NULL,
    registry_version    TEXT NOT NULL,        -- list version scrubbed against (HR-11)
    numbers_checked     INTEGER NOT NULL DEFAULT 0,
    hits                INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbound_purge_log (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    asset_class         TEXT NOT NULL,        -- transcripts|audio (HR-20)
    purged_count        INTEGER NOT NULL DEFAULT 0,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbound_attempts_org_created
    ON outbound_call_attempts (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outbound_prospects_org_status
    ON outbound_prospects (org_id, status);

-- RLS: org isolation, same contract as the TW-284 phone tables.
-- Policies are INERT unless RLS is enabled — enable explicitly.
ALTER TABLE outbound_agent_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbound_prospects ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbound_call_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbound_suppression ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbound_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbound_scrub_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbound_purge_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "outbound_agent_settings_org_isolation" ON outbound_agent_settings
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "outbound_prospects_org_isolation" ON outbound_prospects
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "outbound_call_attempts_org_isolation" ON outbound_call_attempts
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "outbound_suppression_org_isolation" ON outbound_suppression
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "outbound_consents_org_isolation" ON outbound_consents
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "outbound_scrub_runs_org_isolation" ON outbound_scrub_runs
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "outbound_purge_log_org_isolation" ON outbound_purge_log
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
