-- TW-571: M4 roofing pro pack (JobNimbus wedge) data model.
--
-- New tables: suppliers, purchase_orders, storm_events, storm_campaigns.
-- Estimating takeoff lives on quotes.attributes — this migration ADDS the
-- column (it does not exist yet; Rosa TW-547 minor 3).
-- Every new table ships a matching org-isolation RLS policy (TW-547 contract).
-- PII note (TW-552): supplier phone/ordering_email are bronze-layer by
-- necessity (the PO-send flow must reach the supplier); masked in responses.

-- ---------------------------------------------------------------------------
-- Estimating takeoff storage on quotes
-- ---------------------------------------------------------------------------
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS attributes JSONB NOT NULL DEFAULT '{}';

-- ---------------------------------------------------------------------------
-- Suppliers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    branch         TEXT,
    phone          TEXT,
    ordering_email TEXT,
    account_number TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Purchase orders
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS purchase_orders (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    supplier_id UUID NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    job_id      UUID REFERENCES jobs(id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'sent', 'confirmed', 'received', 'canceled')),
    line_items  JSONB NOT NULL DEFAULT '[]',
                -- line item shape: {sku, description, qty, unit_cost}
    total_cost  NUMERIC(12,2),
    sent_at     TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Storm events + campaigns
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS storm_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    nws_event_id TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    headline     TEXT,
    area_desc    TEXT,
    severity     TEXT,
    started_at   TIMESTAMPTZ,
    ends_at      TIMESTAMPTZ,
    raw          JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, nws_event_id)
);

CREATE TABLE IF NOT EXISTS storm_campaigns (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    storm_event_id UUID NOT NULL REFERENCES storm_events(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'launched'
                   CHECK (status IN ('launched', 'completed', 'canceled')),
    message_body   TEXT NOT NULL,
    recipient_count INT NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- RLS: org isolation on all four new tables (TW-547 contract)
-- ---------------------------------------------------------------------------
ALTER TABLE suppliers ENABLE ROW LEVEL SECURITY;
CREATE POLICY "suppliers_all" ON suppliers
    FOR ALL USING (org_id = auth_org_id())
    WITH CHECK (org_id = auth_org_id());

ALTER TABLE purchase_orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY "purchase_orders_all" ON purchase_orders
    FOR ALL USING (org_id = auth_org_id())
    WITH CHECK (org_id = auth_org_id());

ALTER TABLE storm_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "storm_events_all" ON storm_events
    FOR ALL USING (org_id = auth_org_id())
    WITH CHECK (org_id = auth_org_id());

ALTER TABLE storm_campaigns ENABLE ROW LEVEL SECURITY;
CREATE POLICY "storm_campaigns_all" ON storm_campaigns
    FOR ALL USING (org_id = auth_org_id())
    WITH CHECK (org_id = auth_org_id());
