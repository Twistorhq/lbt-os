-- =============================================================
-- TW-620 Twistor Notify V1: notification orchestration tables.
--
-- Trust domain note: ntf_messages.recipient stores the address/number
-- because delivery tracking and provider retries are functionally
-- impossible without it — the same justification as idn_users.email in
-- the TW-619 migration. RLS deny-all (service role only); recipients
-- are never logged (sha256 prefix hashes only) and never returned by
-- any API except to the owning org's admins.
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Org provider bindings. config holds NON-SECRET tuning only (from_addr,
-- priority hints); credentials live in the environment, never here.
CREATE TABLE IF NOT EXISTS ntf_providers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'sms', 'push')),
    provider_name TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    cost_cents_per_unit INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_health_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, channel, provider_name)
);
CREATE INDEX IF NOT EXISTS idx_ntf_providers_org
    ON ntf_providers(org_id);

-- Templates. One name per org; channel-specific bodies.
CREATE TABLE IF NOT EXISTS ntf_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'sms', 'push')),
    subject_template TEXT,
    body_template TEXT NOT NULL DEFAULT '',
    variables JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, name)
);
CREATE INDEX IF NOT EXISTS idx_ntf_templates_org
    ON ntf_templates(org_id);

-- Delivery ledger: one row per orchestrated message.
CREATE TABLE IF NOT EXISTS ntf_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    template_name TEXT,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'sms', 'push')),
    workflow TEXT,
    priority TEXT NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('normal', 'system')),
    recipient TEXT NOT NULL,
    recipient_hash TEXT NOT NULL,
    subject TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'sent', 'delivered', 'failed')),
    provider_used TEXT,
    cost_preview_cents INTEGER NOT NULL DEFAULT 0,
    cost_actual_cents INTEGER NOT NULL DEFAULT 0,
    toll_event_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ntf_messages_org
    ON ntf_messages(org_id);
CREATE INDEX IF NOT EXISTS idx_ntf_messages_workflow
    ON ntf_messages(org_id, workflow);

-- Attempt trail: every provider tried, in order. The debugger reads this.
CREATE TABLE IF NOT EXISTS ntf_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES ntf_messages(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
    error TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS idx_ntf_attempts_message
    ON ntf_attempts(message_id);

-- RLS deny-all: service role only, matching the identity migration.
ALTER TABLE ntf_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE ntf_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE ntf_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE ntf_attempts ENABLE ROW LEVEL SECURITY;
