-- TW-734 video consult (Justynn, 2026-10-28): the consult a shop
-- sells — homeowner video-calls the shop on a tokenized link, a real
-- tech joins live, AI triages first and takes a video message when
-- no tech joins. Shop-configured pricing (per-minute micro-tolled or
-- one flat rate); Twistor's toll runs on the M1 ledger either way.
-- PII doctrine (TW-552): the customer phone lives ONLY in the bronze
-- column here; the join token is stored as a SHA-256 hash, never raw.
-- Every table ships its org-isolation RLS policy (TW-547 contract),
-- enabled explicitly (TW-710 lesson).

CREATE TABLE IF NOT EXISTS video_consult_settings (
    org_id                   UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    enabled                  BOOLEAN NOT NULL DEFAULT FALSE,  -- shop master toggle
    pricing_mode             TEXT NOT NULL DEFAULT 'per_minute',  -- per_minute | flat
    per_minute_rate_cents    INT NOT NULL DEFAULT 200,   -- customer rate, shop-set
    flat_rate_cents          INT NOT NULL DEFAULT 4900,  -- customer rate, shop-set
    tech_join_window_seconds INT NOT NULL DEFAULT 120,   -- answering-machine window
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS video_consult_sessions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    access_token_hash   TEXT NOT NULL UNIQUE,    -- SHA-256 of the join token
    customer_name       TEXT NULL,
    customer_phone      TEXT NULL,               -- bronze only
    customer_hash       TEXT NULL,               -- SHA-256, matching without PII
    customer_id         UUID NULL REFERENCES customers(id),
    issue               TEXT NULL,
    status              TEXT NOT NULL DEFAULT 'ringing',
                        -- ringing | ai_triaging | live | message_left |
                        -- converted_to_visit | ended
    room_name           TEXT NULL,
    room_url            TEXT NULL,
    ai_disclosure_given BOOLEAN NOT NULL DEFAULT FALSE,
    disclosure_at       TIMESTAMPTZ NULL,
    recording_enabled   BOOLEAN NOT NULL DEFAULT FALSE,
    tech_user_id        TEXT NULL,
    triage_started_at   TIMESTAMPTZ NULL,
    triage_deadline_at  TIMESTAMPTZ NULL,
    live_started_at     TIMESTAMPTZ NULL,        -- billable clock start
    ended_at            TIMESTAMPTZ NULL,
    escalation_chain_id UUID NULL REFERENCES escalation_chains(id) ON DELETE SET NULL,
    job_id              UUID NULL REFERENCES jobs(id),
    appointment_id      UUID NULL REFERENCES appointments(id),
    billable_seconds    INT NULL,
    billable_minutes    INT NULL,
    charge_cents        INT NULL,                -- receivable until collection wires
    charge_status       TEXT NOT NULL DEFAULT 'not_charged',
                        -- not_charged | receivable
    metered_at          TIMESTAMPTZ NULL,        -- exactly-once metering stamp
    started_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_video_consult_sessions_org_started
    ON video_consult_sessions (org_id, started_at DESC);

CREATE TABLE IF NOT EXISTS video_consult_messages (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    session_id           UUID NOT NULL REFERENCES video_consult_sessions(id) ON DELETE CASCADE,
    recording_ref        TEXT NOT NULL,          -- storage ref of the video message
    transcript           TEXT NULL,              -- local faster-whisper output
    summary              TEXT NULL,              -- extractive summary (see service)
    transcription_status TEXT NOT NULL DEFAULT 'pending',  -- pending | complete
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_video_consult_messages_session
    ON video_consult_messages (session_id, created_at);

CREATE TABLE IF NOT EXISTS video_consult_events (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    session_id        UUID NOT NULL REFERENCES video_consult_sessions(id) ON DELETE CASCADE,
    event             TEXT NOT NULL,   -- state_change | recording_enabled | recording_disabled
    announcement_text TEXT NULL,       -- spoken text when recording switched on
    actor             TEXT NULL,       -- owner | tech | system
    detail            TEXT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_video_consult_events_session
    ON video_consult_events (session_id, created_at);

-- RLS: org isolation, same contract as ai_call_sessions (TW-547).
-- Policies are INERT unless RLS is enabled — enable explicitly.
ALTER TABLE video_consult_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_consult_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_consult_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_consult_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY "video_consult_settings_org_isolation" ON video_consult_settings
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "video_consult_sessions_org_isolation" ON video_consult_sessions
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "video_consult_messages_org_isolation" ON video_consult_messages
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "video_consult_events_org_isolation" ON video_consult_events
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
