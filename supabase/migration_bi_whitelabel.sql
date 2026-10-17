-- TW-570 M3 white-label BI (Databox wedge).
-- Org-branded dashboards, token-gated public embeds (aggregate-only),
-- cached connector metric snapshots.
-- Every new table ships its org-isolation RLS policy (TW-547 contract).
--
-- is_public semantics (Rosa TW-547 Minor 7): embed tokens are the SOLE
-- public-access mechanism. bi_dashboards.is_public is the master kill switch:
-- when false, GET /bi/embed/{token} returns 404 even for a valid token.

CREATE TABLE IF NOT EXISTS bi_dashboards (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id     UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL,
    layout     JSONB NOT NULL DEFAULT '{"widgets": []}',
    -- layout: {"widgets": [{"id": "w1", "metric": "revenue_30d",
    --                       "title": "Revenue (30d)", "chart": "kpi",
    --                       "connector": "internal"}]}
    theme      JSONB NOT NULL DEFAULT '{}',
    -- theme keys (validated in app): logo_url, primary_color,
    -- secondary_color, font_family, background_color
    is_public  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, slug)
);

CREATE TABLE IF NOT EXISTS bi_embeds (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    -- Denormalized from the dashboard at issue time so the RLS policy keeps
    -- the uniform org-isolation form (no subquery).
    dashboard_id UUID NOT NULL REFERENCES bi_dashboards(id) ON DELETE CASCADE,
    token_hash   TEXT UNIQUE NOT NULL,
    -- SHA-256 hex of the bearer token (secrets.token_urlsafe(32)).
    -- The raw token is returned ONCE at issue and never stored or logged.
    expires_at   TIMESTAMPTZ NULL,
    revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bi_metric_snapshots (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    connector_key TEXT NOT NULL,
    metric_key    TEXT NOT NULL,
    value         JSONB NOT NULL,
    -- Aggregate payload ONLY. The embed renderer validates every value
    -- against the metric's declared output shape before serving.
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, connector_key, metric_key)
);

CREATE INDEX IF NOT EXISTS idx_bi_dashboards_org
    ON bi_dashboards(org_id);
CREATE INDEX IF NOT EXISTS idx_bi_embeds_dashboard
    ON bi_embeds(dashboard_id);
CREATE INDEX IF NOT EXISTS idx_bi_embeds_org
    ON bi_embeds(org_id);
CREATE INDEX IF NOT EXISTS idx_bi_embeds_token_hash
    ON bi_embeds(token_hash);
CREATE INDEX IF NOT EXISTS idx_bi_metric_snapshots_org
    ON bi_metric_snapshots(org_id, connector_key);

DROP TRIGGER IF EXISTS trg_bi_dashboards_updated ON bi_dashboards;
CREATE TRIGGER trg_bi_dashboards_updated
    BEFORE UPDATE ON bi_dashboards FOR EACH ROW
    EXECUTE FUNCTION fn_update_updated_at();

-- ---------------------------------------------------------------------------
-- RLS: org isolation on every new table (TW-547 section 6 contract).
-- NOTE: the public embed endpoint carries no JWT, so it uses a service-role
-- client; tenant isolation there is enforced in application code
-- (token -> embed -> dashboard -> org_id chain, never from the request).
-- ---------------------------------------------------------------------------
CREATE POLICY "bi_dashboards_org_isolation" ON bi_dashboards
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "bi_embeds_org_isolation" ON bi_embeds
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "bi_metric_snapshots_org_isolation" ON bi_metric_snapshots
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
