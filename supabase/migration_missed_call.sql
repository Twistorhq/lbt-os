-- TW-556 M2 missed-call layer (Jobber wedge).
-- Bronze-layer telephony tables. PII doctrine (TW-552): raw numbers live ONLY
-- here; silver/gold layers and API responses use masked values.
-- Every new table ships its org-isolation RLS policy (TW-547 contract).

CREATE TABLE IF NOT EXISTS org_phone_numbers (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone_number TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'voice',  -- voice | sms
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (phone_number)
);

CREATE TABLE IF NOT EXISTS call_logs (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id           UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    twilio_call_sid  TEXT UNIQUE NOT NULL,
    direction        TEXT NOT NULL,   -- inbound | outbound
    from_number      TEXT NOT NULL,   -- bronze: raw, needed for SMS-back
    from_number_hash TEXT NOT NULL,   -- SHA-256, for matching without PII
    to_number        TEXT NOT NULL,   -- the shop's Twilio number (bronze)
    started_at       TIMESTAMPTZ NOT NULL,
    ended_at         TIMESTAMPTZ,
    duration_sec     INT,
    status           TEXT NOT NULL,   -- completed | no-answer | busy | failed | voicemail
    recording_url    TEXT,            -- bronze only, never API-returned in v1
    transcription    TEXT,            -- bronze only, never API-returned in v1
    customer_id      UUID NULL REFERENCES customers(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_call_logs_org_started
    ON call_logs (org_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_logs_org_status
    ON call_logs (org_id, status);

CREATE TABLE IF NOT EXISTS missed_call_recoveries (
    id                       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    call_log_id              UUID NOT NULL REFERENCES call_logs(id) ON DELETE CASCADE,
    sms_sent_at              TIMESTAMPTZ,
    sms_body                 TEXT,
    sms_error                TEXT,   -- dead-letter reason; NULL = sent or not attempted
    recovered_at             TIMESTAMPTZ,
    resulting_appointment_id UUID NULL,
    revenue_attributed       NUMERIC(12,2) NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_missed_recoveries_org
    ON missed_call_recoveries (org_id, recovered_at);

-- RLS: org isolation, same contract as toll_events (TW-547)
CREATE POLICY "org_phone_numbers_org_isolation" ON org_phone_numbers
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "call_logs_org_isolation" ON call_logs
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "missed_call_recoveries_org_isolation" ON missed_call_recoveries
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
