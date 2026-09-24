-- =============================================================
-- TW-201 "Build the moat from day one" — Leak-detection engine
-- Normalized service-business data model (canonical schema for all
-- 12 verticals; HVAC is the day-one pilot vertical).
--
-- New entities: technicians, service_assets, service_plans, jobs,
-- appointments, quotes. Plus additive customers columns the HVAC
-- detectors need (property_type, home_age_year).

CREATE EXTENSION IF NOT EXISTS pgcrypto;
--
-- Rules: additive only (IF NOT EXISTS), gen_random_uuid() PKs,
-- org_id scoping, RLS + updated_at triggers like the base schema.
-- Run in Supabase SQL editor. Branch: feature/tw-201-leak-engine
-- =============================================================

-- ----------------------------------------------------------
-- TECHNICIANS — org team members doing field work
-- (created before jobs; jobs references this table)
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS technicians (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    role        TEXT,            -- tech | lead_tech | installer | dispatcher | apprentice
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_technicians_org
    ON technicians(org_id);
CREATE INDEX IF NOT EXISTS idx_technicians_org_active
    ON technicians(org_id) WHERE active;

-- ----------------------------------------------------------
-- SERVICE_ASSETS — installed equipment (the equipment graveyard)
-- HVAC: furnace | air_conditioner | heat_pump | water_heater |
--   thermostat | air_handler | ductwork | panel | generator
-- Other verticals reuse: plumbing water_heater/softener/sump_pump,
-- electrical panel/generator, vet patient, dental chair, etc.
-- attributes JSONB carries vertical-specific fields (tonnage, BTU,
-- refrigerant, SEER rating, panel amps, pet species, ...) so the
-- core stays identical for all 12 verticals.
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS service_assets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    customer_id         UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    asset_type          TEXT NOT NULL,
    brand               TEXT,
    model               TEXT,
    serial              TEXT,
    install_date        DATE,
    expected_life_years INT,            -- drives replacement-age cohorts
    attributes          JSONB NOT NULL DEFAULT '{}',
    source              TEXT,           -- import | manual | integration | job
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Detector: asset age cohorts (age = now - install_date; end-of-life =
-- install_date + expected_life_years approaching). Composite on
-- (org_id, asset_type, install_date) serves both cohort scans.
CREATE INDEX IF NOT EXISTS idx_assets_org_type_install
    ON service_assets(org_id, asset_type, install_date);
CREATE INDEX IF NOT EXISTS idx_assets_org_customer
    ON service_assets(org_id, customer_id);

-- ----------------------------------------------------------
-- SERVICE_PLANS — maintenance agreements
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS service_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    customer_id     UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    plan_name       TEXT NOT NULL,
    started_at      TIMESTAMPTZ,
    renewal_at      TIMESTAMPTZ,   -- detector: expiring without renewal
    status          TEXT NOT NULL DEFAULT 'active',
                   -- active | past_due | canceled | expired
    billing_status  TEXT NOT NULL DEFAULT 'current',
                   -- current | card_failed | payment_past_due
    visits_per_year INT NOT NULL DEFAULT 2,
    visits_completed INT NOT NULL DEFAULT 0,
    annual_price    DECIMAL(12,2),
    source          TEXT,           -- import | manual | integration
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Detector: active plans missing visits (visits_completed < visits_per_year)
CREATE INDEX IF NOT EXISTS idx_plans_org_active
    ON service_plans(org_id, visits_completed) WHERE status = 'active';
-- Detector: failed billing / card failures
CREATE INDEX IF NOT EXISTS idx_plans_org_billing
    ON service_plans(org_id, billing_status) WHERE billing_status <> 'current';
-- Detector: renewals coming due without renewal activity
CREATE INDEX IF NOT EXISTS idx_plans_org_renewal
    ON service_plans(org_id, renewal_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_plans_org_customer
    ON service_plans(org_id, customer_id);

-- ----------------------------------------------------------
-- JOBS — work orders
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    customer_id   UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    asset_id      UUID REFERENCES service_assets(id) ON DELETE SET NULL,
    technician_id UUID REFERENCES technicians(id) ON DELETE SET NULL,
    job_type      TEXT NOT NULL,
                  -- install | repair | maintenance | inspection | emergency | replacement
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'scheduled',
                  -- scheduled | in_progress | completed | canceled
    scheduled_at  TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    total         DECIMAL(12,2) NOT NULL DEFAULT 0,
    source        TEXT,             -- import | manual | integration | quote
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_org_status_sched
    ON jobs(org_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_jobs_org_customer
    ON jobs(org_id, customer_id, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_org_tech
    ON jobs(org_id, technician_id) WHERE technician_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_org_asset
    ON jobs(org_id, asset_id) WHERE asset_id IS NOT NULL;

-- ----------------------------------------------------------
-- APPOINTMENTS — visits (incl. plan visits, estimates)
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS appointments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    job_id          UUID REFERENCES jobs(id) ON DELETE SET NULL,
    customer_id     UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    service_plan_id UUID REFERENCES service_plans(id) ON DELETE SET NULL,
    technician_id   UUID REFERENCES technicians(id) ON DELETE SET NULL,
    visit_type      TEXT NOT NULL DEFAULT 'job_visit',
                    -- job_visit | plan_visit | estimate | follow_up
    scheduled_at    TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    no_show         BOOLEAN NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_appts_org_customer_sched
    ON appointments(org_id, customer_id, scheduled_at DESC);
-- Detector: no-shows are pure leak
CREATE INDEX IF NOT EXISTS idx_appts_org_noshow
    ON appointments(org_id, scheduled_at DESC) WHERE no_show;
-- Plan-visit coverage: feeds visits_completed reconciliation
CREATE INDEX IF NOT EXISTS idx_appts_org_plan
    ON appointments(org_id, service_plan_id, scheduled_at DESC)
    WHERE service_plan_id IS NOT NULL;

-- ----------------------------------------------------------
-- QUOTES — trades quotes as first-class entities
-- Distinct from generic leads: a quote is priced work (line_items,
-- totals, follow-ups). A won quote links to the resulting job/sale.
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS quotes (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    customer_id       UUID REFERENCES customers(id) ON DELETE SET NULL,
    lead_id           UUID REFERENCES leads(id) ON DELETE SET NULL,
    quote_number      TEXT,
    line_items        JSONB NOT NULL DEFAULT '[]',
    -- line item shape: {description, quantity, unit_price, amount, asset_id?}
    total             DECIMAL(12,2) NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'draft',
                      -- draft | sent | follow_up | won | lost | expired
    sent_at           TIMESTAMPTZ,
    last_follow_up_at TIMESTAMPTZ,
    follow_up_count   INT NOT NULL DEFAULT 0,
    valid_until       DATE,
    won_job_id        UUID REFERENCES jobs(id) ON DELETE SET NULL,
    won_sale_id       UUID REFERENCES sales(id) ON DELETE SET NULL,
    lost_reason       TEXT,
    source            TEXT,           -- import | manual | integration
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (customer_id IS NOT NULL OR lead_id IS NOT NULL)
);

-- Detector: stale quotes — days since last follow-up on open quotes
CREATE INDEX IF NOT EXISTS idx_quotes_org_open_followup
    ON quotes(org_id, last_follow_up_at)
    WHERE status IN ('sent', 'follow_up');
CREATE INDEX IF NOT EXISTS idx_quotes_org_customer
    ON quotes(org_id, customer_id) WHERE customer_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_quotes_org_lead
    ON quotes(org_id, lead_id) WHERE lead_id IS NOT NULL;

-- ----------------------------------------------------------
-- CUSTOMERS — additive columns the HVAC detectors need
-- (no new PII: names/phones already live here)
-- ----------------------------------------------------------
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS property_type TEXT;   -- residential | commercial | multi_family
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS home_age_year INT;    -- segments replacement propensity

-- ----------------------------------------------------------
-- ROW LEVEL SECURITY (defense in depth; backend uses service role)
-- ----------------------------------------------------------
ALTER TABLE technicians   ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_plans  ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs           ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE quotes         ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['technicians','service_assets','service_plans',
                             'jobs','appointments','quotes']
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_policies
                       WHERE tablename = t AND policyname = 'org_isolation') THEN
            EXECUTE format(
                'CREATE POLICY "org_isolation" ON %I FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id())',
                t);
        END IF;
    END LOOP;
END $$;

-- ----------------------------------------------------------
-- UPDATED_AT TRIGGERS
-- ----------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_technicians_updated') THEN
        CREATE TRIGGER trg_technicians_updated
            BEFORE UPDATE ON technicians FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_service_assets_updated') THEN
        CREATE TRIGGER trg_service_assets_updated
            BEFORE UPDATE ON service_assets FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_service_plans_updated') THEN
        CREATE TRIGGER trg_service_plans_updated
            BEFORE UPDATE ON service_plans FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_jobs_updated') THEN
        CREATE TRIGGER trg_jobs_updated
            BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_appointments_updated') THEN
        CREATE TRIGGER trg_appointments_updated
            BEFORE UPDATE ON appointments FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_quotes_updated') THEN
        CREATE TRIGGER trg_quotes_updated
            BEFORE UPDATE ON quotes FOR EACH ROW EXECUTE FUNCTION fn_update_updated_at();
    END IF;
END $$;
