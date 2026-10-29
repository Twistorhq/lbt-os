-- =============================================================
-- TW-768 Twistor Trades agent permission ledger (TW-764 Spec 2).
--
-- The consent and permission ledger for a shop's agent: per-org
-- grants/denials per agent action class, an execution-time check
-- that fails closed, and revocation in three strengths (class,
-- channel, all) as one owner motion — halt queued work, fence
-- in-flight turns, cut tokens, purge retained working copies on a
-- stated clock, receipt in the audit feed.
--
-- This schema exists because of the failure in the TW-764
-- complaint dossier: Instinct kept summarizing a user's Gmail
-- hours after she revoked access, and deletion was a manual
-- request. Here revocation is a state change the execution gate
-- reads, retained copies carry their own purge deadline, and the
-- receipt is written by the system, never narrated by the agent.
--
-- agt_permission_events is append-only: no updates, no deletes.
-- Full change history lives there; agt_grants is current state.
--
-- RLS deny-all (service role only). The backend filters org_id
-- manually on every query (database.py discipline).
-- Idempotent: safe to re-run (IF NOT EXISTS throughout).
--
-- STUB (TW-767 / Spec 1): agent_identity_id columns reference the
-- per-org agent identity record by value. No FK yet — the
-- agent_identity table lands with TW-767's migration; the FK is
-- added there or in a follow-up once that table exists.
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Current permission state. One row per (org, action class).
-- No row = no grant = denied (fail closed at the check, not here).
-- The cap law is pinned in BOTH layers: the model layer enforces
-- it at construction AND CHECK constraints enforce it in the
-- schema, so a write path that bypasses the model (a future
-- writer, an ops repair) still cannot state a broken grant:
-- granted money rows require cap_cents, and cap_cents may exist
-- only on the money classes.
CREATE TABLE IF NOT EXISTS agt_grants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID,
    action_class TEXT NOT NULL CHECK (action_class IN (
        'email.send', 'sms.send', 'call.outbound',
        'booking.create', 'booking.reschedule',
        'payment.link_send', 'payment.charge', 'purchase'
    )),
    status TEXT NOT NULL DEFAULT 'granted' CHECK (status IN ('granted', 'denied', 'revoked')),
    scope JSONB NOT NULL DEFAULT '{}',
    cap_cents BIGINT CHECK (cap_cents IS NULL OR cap_cents >= 0),
    CONSTRAINT agt_grants_cap_only_on_money CHECK (
        cap_cents IS NULL OR action_class IN ('payment.charge', 'purchase')
    ),
    CONSTRAINT agt_grants_granted_money_requires_cap CHECK (
        status <> 'granted'
        OR action_class NOT IN ('payment.charge', 'purchase')
        OR cap_cents IS NOT NULL
    ),
    granted_by TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (org_id, action_class)
);
CREATE INDEX IF NOT EXISTS idx_agt_grants_org ON agt_grants(org_id);

-- Cap-law constraints for a table that pre-dates them (fresh
-- installs carry them inline in the CREATE TABLE above). Adding
-- one over violating rows fails loud, which is the point: the
-- broken rows surface instead of silently gaining a pass.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_grants_cap_only_on_money'
    ) THEN
        ALTER TABLE agt_grants ADD CONSTRAINT agt_grants_cap_only_on_money
            CHECK (cap_cents IS NULL OR action_class IN ('payment.charge', 'purchase'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agt_grants_granted_money_requires_cap'
    ) THEN
        ALTER TABLE agt_grants ADD CONSTRAINT agt_grants_granted_money_requires_cap
            CHECK (status <> 'granted' OR action_class NOT IN ('payment.charge', 'purchase') OR cap_cents IS NOT NULL);
    END IF;
END $$;

-- Append-only change history + audit-feed source for permission
-- events, including the system-generated revocation receipt.
CREATE TABLE IF NOT EXISTS agt_permission_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID,
    kind TEXT NOT NULL CHECK (kind IN (
        'grant', 'deny', 'update', 'revoke',
        'token_cut', 'purge_scheduled', 'purge_completed',
        'revocation_receipt'
    )),
    action_class TEXT,
    channel TEXT,
    actor TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}',
    at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agt_events_org_at ON agt_permission_events(org_id, at DESC);

-- One row per revocation motion. purge_due_at is the stated clock:
-- revoked_at + the purge delay in force at revocation time.
CREATE TABLE IF NOT EXISTS agt_revocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID,
    strength TEXT NOT NULL CHECK (strength IN ('class', 'channel', 'all')),
    target TEXT,
    revoked_by TEXT NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purge_due_at TIMESTAMPTZ NOT NULL,
    tokens_cut_at TIMESTAMPTZ,
    receipt_event_id UUID,
    purge_completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agt_revocations_org ON agt_revocations(org_id, revoked_at DESC);

-- Retained working copies registry: kind + locator only, never
-- content. Revocation schedules purge_after; the purge runner
-- stamps purged_at. A copy with purge_after set and purged_at
-- NULL past its deadline is a bug the purge test catches.
CREATE TABLE IF NOT EXISTS agt_retained_copies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    agent_identity_id UUID,
    copy_kind TEXT NOT NULL,
    locator TEXT NOT NULL,
    registered_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purge_after TIMESTAMPTZ,
    purged_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agt_copies_org_purge ON agt_retained_copies(org_id, purge_after);

-- RLS: deny-all. The service role (backend) is the only writer/reader.
ALTER TABLE agt_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_permission_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE agt_retained_copies ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_grants_deny_all'
    ) THEN
        CREATE POLICY agt_grants_deny_all ON agt_grants FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_permission_events_deny_all'
    ) THEN
        CREATE POLICY agt_permission_events_deny_all ON agt_permission_events FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_revocations_deny_all'
    ) THEN
        CREATE POLICY agt_revocations_deny_all ON agt_revocations FOR ALL USING (false);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'agt_retained_copies_deny_all'
    ) THEN
        CREATE POLICY agt_retained_copies_deny_all ON agt_retained_copies FOR ALL USING (false);
    END IF;
END $$;
