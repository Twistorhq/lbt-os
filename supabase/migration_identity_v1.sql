-- =============================================================
-- TW-619 Twistor Identity V1: auth/identity tables.
--
-- Trust domain note: identity is a first-party auth domain, separate
-- from the analytics medallion. Emails are functionally required
-- (magic links, verification, SSO provisioning) and are stored here,
-- never exposed via API except to the owning user, never logged.
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";

-- Users. password_hash NULL for magic-link / OAuth / SSO-only accounts.
CREATE TABLE IF NOT EXISTS idn_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT NOT NULL UNIQUE,
    password_hash TEXT,
    email_verified_at TIMESTAMPTZ,
    clerk_user_id TEXT UNIQUE,
    last_sign_in_at TIMESTAMPTZ,
    is_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_idn_users_clerk ON idn_users(clerk_user_id)
    WHERE clerk_user_id IS NOT NULL;

-- Org memberships. org_id references the existing organizations table.
CREATE TABLE IF NOT EXISTS idn_org_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member'
        CHECK (role IN ('owner', 'admin', 'member')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, org_id)
);
CREATE INDEX IF NOT EXISTS idx_idn_memberships_user
    ON idn_org_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_idn_memberships_org
    ON idn_org_memberships(org_id);

-- Sessions / refresh tokens. Only the sha256 is stored, never the raw.
-- family_id groups rotations; reuse of a rotated token revokes the family.
CREATE TABLE IF NOT EXISTS idn_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    family_id UUID NOT NULL DEFAULT gen_random_uuid(),
    refresh_token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    rotated_from UUID REFERENCES idn_sessions(id) ON DELETE SET NULL,
    ip INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_idn_sessions_user
    ON idn_sessions(user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_idn_sessions_family ON idn_sessions(family_id);

-- Magic links. Single use, short expiry. Only the hash is stored.
CREATE TABLE IF NOT EXISTS idn_magic_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL DEFAULT 'login' CHECK (purpose IN ('login', 'verify')),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- OAuth linked accounts.
CREATE TABLE IF NOT EXISTS idn_oauth_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES idn_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_user_id)
);

-- SSO configs per org. settings_json is app-layer encrypted (Fernet).
CREATE TABLE IF NOT EXISTS idn_sso_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
    protocol TEXT NOT NULL CHECK (protocol IN ('saml', 'oidc')),
    domains TEXT[] NOT NULL DEFAULT '{}',
    settings_json TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- updated_at maintenance
CREATE OR REPLACE FUNCTION idn_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_idn_users_touch ON idn_users;
CREATE TRIGGER trg_idn_users_touch
    BEFORE UPDATE ON idn_users
    FOR EACH ROW EXECUTE FUNCTION idn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_idn_sso_touch ON idn_sso_configs;
CREATE TRIGGER trg_idn_sso_touch
    BEFORE UPDATE ON idn_sso_configs
    FOR EACH ROW EXECUTE FUNCTION idn_touch_updated_at();

-- RLS: deny-all by default; the service role bypasses (server-side only).
ALTER TABLE idn_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE idn_org_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE idn_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE idn_magic_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE idn_oauth_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE idn_sso_configs ENABLE ROW LEVEL SECURITY;
