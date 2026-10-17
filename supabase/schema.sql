-- =============================================================
-- LBT OS — Supabase Schema
-- Multi-tenant: every table is scoped to an org_id
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ----------------------------------------------------------
-- ORGANIZATIONS  (synced from Clerk orgs via webhook)
-- ----------------------------------------------------------
CREATE TABLE organizations (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    clerk_org_id          TEXT UNIQUE NOT NULL,
    name                  TEXT NOT NULL,
    industry              TEXT,          -- hvac | plumbing | electrician | landscaping | cleaning_service | gig_worker | salon_spa | restaurant | gym | real_estate
    plan                  TEXT NOT NULL DEFAULT 'basic',  -- basic | pro | premium | trades_starter | trades_pro
    stripe_customer_id    TEXT,
    stripe_subscription_id TEXT,
    subscription_status   TEXT NOT NULL DEFAULT 'inactive', -- active | inactive | past_due | canceled
    city                  TEXT NOT NULL DEFAULT 'Denver',
    state                 TEXT NOT NULL DEFAULT 'CO',
    onboarding_complete   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- LEADS
-- ----------------------------------------------------------
CREATE TABLE leads (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id           UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    email            TEXT,
    phone            TEXT,
    source           TEXT,   -- google | referral | social | yelp | cold_call | walk_in | other
    status           TEXT NOT NULL DEFAULT 'new',
                             -- new | contacted | qualified | proposal | won | lost
    service_interest TEXT,
    estimated_value  DECIMAL(12,2),
    notes            TEXT,
    assigned_to      TEXT,   -- Clerk user_id of staff member
    follow_up_at     TIMESTAMPTZ,
    contacted_at     TIMESTAMPTZ,
    converted_at     TIMESTAMPTZ,  -- set when status → won
    lost_reason      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- CUSTOMERS  (created when a lead is won OR added directly)
-- ----------------------------------------------------------
CREATE TABLE customers (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id           UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    lead_id          UUID REFERENCES leads(id) ON DELETE SET NULL,
    name             TEXT NOT NULL,
    email            TEXT,
    phone            TEXT,
    address          TEXT,
    tags             TEXT[] DEFAULT '{}',   -- loyal | vip | at_risk | inactive
    lifetime_value   DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_orders     INT NOT NULL DEFAULT 0,
    last_purchase_at TIMESTAMPTZ,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- SALES / REVENUE
-- ----------------------------------------------------------
CREATE TABLE sales (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    customer_id    UUID REFERENCES customers(id) ON DELETE SET NULL,
    lead_id        UUID REFERENCES leads(id) ON DELETE SET NULL,
    service        TEXT NOT NULL,
    amount         DECIMAL(12,2) NOT NULL,
    cost           DECIMAL(12,2) NOT NULL DEFAULT 0,
    profit         DECIMAL(12,2) GENERATED ALWAYS AS (amount - cost) STORED,
    payment_method TEXT,   -- cash | card | check | bank_transfer | financing
    payment_status TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | refunded
    source         TEXT,   -- lead source attribution for ROI tracking
    invoice_number TEXT,
    notes          TEXT,
    sold_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- EXPENSES
-- ----------------------------------------------------------
CREATE TABLE expenses (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    category          TEXT NOT NULL,
                      -- payroll | materials | marketing | rent | utilities | equipment | insurance | misc
    description       TEXT NOT NULL,
    amount            DECIMAL(12,2) NOT NULL,
    vendor            TEXT,
    receipt_url       TEXT,
    is_recurring      BOOLEAN NOT NULL DEFAULT FALSE,
    recurrence_period TEXT,   -- weekly | monthly | quarterly | annual
    expense_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- AI AUDIT REPORTS
-- ----------------------------------------------------------
CREATE TABLE audit_reports (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id           UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_start     DATE NOT NULL,
    period_end       DATE NOT NULL,
    insights         JSONB NOT NULL DEFAULT '[]',
    recommendations  JSONB NOT NULL DEFAULT '[]',
    raw_metrics      JSONB NOT NULL DEFAULT '{}',
    health_score     INT CHECK (health_score BETWEEN 0 AND 100),
    model_used       TEXT NOT NULL DEFAULT 'gpt-4o-mini',
    generated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- INTEGRATIONS
-- Stores encrypted provider credentials and sync metadata
-- ----------------------------------------------------------
CREATE TABLE integration_connections (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id                UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    provider              TEXT NOT NULL,
    label                 TEXT,
    status                TEXT NOT NULL DEFAULT 'connected',
    credentials_encrypted TEXT NOT NULL,
    config                JSONB NOT NULL DEFAULT '{}',
    external_account_id   TEXT,
    external_account_name TEXT,
    last_synced_at        TIMESTAMPTZ,
    last_sync_status      TEXT,
    last_sync_error       TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE integration_sync_runs (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    connection_id  UUID NOT NULL REFERENCES integration_connections(id) ON DELETE CASCADE,
    provider       TEXT NOT NULL,
    trigger_source TEXT NOT NULL DEFAULT 'manual',
    status         TEXT NOT NULL DEFAULT 'pending',
    stats          JSONB NOT NULL DEFAULT '{}',
    error          TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ
);

CREATE TABLE integration_record_links (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES integration_connections(id) ON DELETE CASCADE,
    provider      TEXT NOT NULL,
    object_type   TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    local_table   TEXT NOT NULL,
    local_id      UUID NOT NULL,
    fingerprint   TEXT,
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(connection_id, object_type, external_id)
);

-- ----------------------------------------------------------
-- INDEXES
-- ----------------------------------------------------------
CREATE INDEX idx_leads_org_status      ON leads(org_id, status);
CREATE INDEX idx_leads_org_created     ON leads(org_id, created_at DESC);
CREATE INDEX idx_leads_follow_up       ON leads(org_id, follow_up_at) WHERE follow_up_at IS NOT NULL;
CREATE INDEX idx_customers_org         ON customers(org_id);
CREATE INDEX idx_customers_org_created ON customers(org_id, created_at DESC);
CREATE INDEX idx_sales_org_sold        ON sales(org_id, sold_at DESC);
CREATE INDEX idx_sales_org_source      ON sales(org_id, source);
CREATE INDEX idx_expenses_org_date     ON expenses(org_id, expense_date DESC);
CREATE INDEX idx_expenses_org_category ON expenses(org_id, category);
CREATE INDEX idx_audit_org_generated   ON audit_reports(org_id, generated_at DESC);
CREATE INDEX idx_integration_connections_org ON integration_connections(org_id, provider);
CREATE INDEX idx_integration_sync_runs_org ON integration_sync_runs(org_id, started_at DESC);
CREATE INDEX idx_integration_record_links_lookup ON integration_record_links(connection_id, object_type, external_id);

-- ----------------------------------------------------------
-- UPDATED_AT TRIGGER
-- ----------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_orgs_updated      BEFORE UPDATE ON organizations FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_leads_updated     BEFORE UPDATE ON leads         FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_customers_updated BEFORE UPDATE ON customers     FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_sales_updated     BEFORE UPDATE ON sales         FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_expenses_updated  BEFORE UPDATE ON expenses      FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_integration_connections_updated BEFORE UPDATE ON integration_connections FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
CREATE TRIGGER trg_integration_record_links_updated BEFORE UPDATE ON integration_record_links FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();

-- ----------------------------------------------------------
-- CUSTOMER LIFETIME VALUE TRIGGER
-- Automatically updates customer LTV and order count on sale insert/update
-- ----------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sync_customer_ltv()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.customer_id IS NOT NULL THEN
        UPDATE customers
        SET
            lifetime_value   = (SELECT COALESCE(SUM(amount), 0) FROM sales WHERE customer_id = NEW.customer_id AND payment_status = 'paid'),
            total_orders     = (SELECT COUNT(*) FROM sales WHERE customer_id = NEW.customer_id),
            last_purchase_at = (SELECT MAX(sold_at) FROM sales WHERE customer_id = NEW.customer_id)
        WHERE id = NEW.customer_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_sales_sync_ltv
    AFTER INSERT OR UPDATE ON sales
    FOR EACH ROW EXECUTE FUNCTION fn_sync_customer_ltv();
