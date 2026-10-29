-- =============================================================
-- TW-767 Twistor Agent Identity V1 (Spec 1 of TW-764, the
-- Instinct-shape build for Twistor Trades; TW-766 Agent Model
-- Standard foundation).
--
-- One agent identity per shop (org): the agent's name, its ONE
-- email address, its ONE phone number, its persona record, and
-- its coverage hours. Spec 3 assembles task prompts from the
-- persona record; Spec 6 renders the Agent Card from this record.
--
-- Fail-closed by construction: a fresh identity is status
-- 'unprovisioned' with coverage mode 'unconfigured' — the agent
-- takes no action until the shop provisions and configures it.
--
-- PII doctrine (medallion, TW-552): the agent's own email and
-- phone are the agent's identity, not customer PII. The inbound
-- queue below is a BRONZE intake: a sender's raw address, subject,
-- and body live only in agt_inbound_messages (marked bronze) and
-- are never returned by the API; matching and display use the
-- SHA-256 hash column. Queue uniqueness is per
-- (provider_message_id, org_id): one delivery of one message to
-- one org's agent queues exactly one task.
--
-- RLS deny-all (service role only), same contract as the flg_,
-- flw_, ntf_, idn_ platform tables. The backend filters org_id
-- manually on every query (service-role client bypasses RLS).
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Agent identities. Exactly one row per org (UNIQUE org_id).
-- email_address is globally unique: one address, one shop, one
-- agent — inbound routing resolves an address to exactly one org
-- or fails closed. phone_number binds to the org's existing
-- TW-284/Fonoster number path (org_phone_numbers); the service
-- layer refuses a number owned by another org.
CREATE TABLE IF NOT EXISTS agt_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    email_address TEXT,
    phone_number TEXT,
    persona JSONB NOT NULL DEFAULT '{"voice": {"voice_id": null, "style": "warm_professional"}, "tone": "warm, direct, plain-spoken", "sign_off": "", "greeting": "", "rapport": {"enabled": true, "cadence": "low", "max_checkins_per_week": 1}}',
    coverage JSONB NOT NULL DEFAULT '{"mode": "unconfigured", "timezone": "UTC", "windows": []}',
    status TEXT NOT NULL DEFAULT 'unprovisioned'
        CHECK (status IN ('unprovisioned', 'active', 'suspended')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id),
    UNIQUE (email_address)
);
CREATE INDEX IF NOT EXISTS idx_agt_identities_email ON agt_identities(email_address);
CREATE INDEX IF NOT EXISTS idx_agt_identities_phone ON agt_identities(phone_number);

-- Inbound agent mail, as queued work. BRONZE intake: from_address,
-- subject, and body_text are raw intake values, stored only here
-- and never API-returned; from_address_hash is the silver-safe
-- matching/display value. (provider_message_id, org_id) is UNIQUE
-- so a redelivered webhook can never double-queue a task.
CREATE TABLE IF NOT EXISTS agt_inbound_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    identity_id UUID NOT NULL REFERENCES agt_identities(id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'email',
    provider TEXT NOT NULL DEFAULT 'resend',
    provider_message_id TEXT NOT NULL,
    from_address TEXT,
    from_address_hash TEXT NOT NULL,
    subject TEXT,
    body_text TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'claimed', 'done', 'rejected')),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider_message_id, org_id)
);
CREATE INDEX IF NOT EXISTS idx_agt_inbound_org_status
    ON agt_inbound_messages(org_id, status, received_at DESC);

-- Audit trail. Append-only: who changed the identity record,
-- when. No updates, no deletes on it, ever. Summaries never carry
-- PII (hashes only) — the trail is silver-safe by construction.
CREATE TABLE IF NOT EXISTS agt_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN (
        'provision', 'persona_update', 'coverage_update',
        'phone_bind', 'status_change', 'inbound_enqueued'
    )),
    actor TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agt_audit_org ON agt_audit(org_id, at DESC);

-- RLS: deny-all. The service role (backend) is the only reader/writer.
ALTER TABLE agt_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_inbound_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_audit ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_identities_deny_all'
    ) THEN
        CREATE POLICY agt_identities_deny_all ON agt_identities FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_inbound_messages_deny_all'
    ) THEN
        CREATE POLICY agt_inbound_messages_deny_all ON agt_inbound_messages FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_audit_deny_all'
    ) THEN
        CREATE POLICY agt_audit_deny_all ON agt_audit FOR ALL USING (false);
    END IF;
END $$;
