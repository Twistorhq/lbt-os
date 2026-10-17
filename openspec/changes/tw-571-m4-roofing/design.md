# TW-571 — M4 roofing pro pack: design

## Migration (`supabase/migration_roofing.sql`)

```sql
-- suppliers: the bronze layer for supplier contacts (phone/email needed by
-- the PO-send flow; masked in API responses per TW-552).
CREATE TABLE IF NOT EXISTS suppliers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    branch TEXT,
    phone TEXT,
    ordering_email TEXT,
    account_number TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    supplier_id UUID NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','sent','confirmed','received','canceled')),
    line_items JSONB NOT NULL DEFAULT '[]',
    -- line item shape: {sku, description, qty, unit_cost}
    total_cost NUMERIC(12,2),
    sent_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Estimating takeoff storage. quotes.attributes does NOT exist yet
-- (Rosa TW-547 minor 3) — this migration ADDS it.
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS attributes JSONB NOT NULL DEFAULT '{}';

-- Storm campaign records.
CREATE TABLE IF NOT EXISTS storm_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    nws_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    headline TEXT,
    area_desc TEXT,
    severity TEXT,
    started_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    raw JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, nws_event_id)
);

CREATE TABLE IF NOT EXISTS storm_campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    storm_event_id UUID NOT NULL REFERENCES storm_events(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'launched'
        CHECK (status IN ('launched','completed','canceled')),
    message_body TEXT NOT NULL,
    recipient_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

RLS (appended to the same migration, per the TW-547 contract — every new
table ships a matching org-isolation policy):
```sql
ALTER TABLE suppliers ENABLE ROW LEVEL SECURITY;
CREATE POLICY "suppliers_all" ON suppliers
    FOR ALL USING (org_id = auth_org_id())
    WITH CHECK (org_id = auth_org_id());
-- identical pattern for purchase_orders, storm_events, storm_campaigns
```

## Service (`backend/app/services/roofing_service.py`)

**Takeoff math** — `Decimal`, `ROUND_HALF_UP`, exact cents:
- `material_squares = squares * (1 + waste_pct/100)`
- `material_cost = material_squares * MATERIAL_RATES[material]["material"]`
- `labor_cost = squares * MATERIAL_RATES[material]["labor"]`
- Two line items appended to `quote.line_items`:
  `{description: "Material — {material}", quantity: material_squares,
    unit_price, amount}` and `{description: "Labor — roofing", ...}`;
  `quote.total` recomputed as the sum of all line-item amounts.
- `MATERIAL_RATES` (per square, documented defaults, editable later):
  architectural_shingle {material: 145, labor: 95},
  metal_standing_seam {material: 380, labor: 160},
  tile {material: 520, labor: 220}, tpo {material: 210, labor: 120}.
- Takeoff payload stored at `quotes.attributes.takeoff`:
  `{squares, pitch, waste_pct, material, computed_at}`.
- Unknown material → 422. Non-positive squares → 422.

**Suppliers/POs** — straightforward CRUD via supabase client, org-scoped.
`send_po`: status draft→sent, sets `sent_at`, computes `total_cost` from
line_items (qty × unit_cost, exact cents), emails `ordering_email` via the
existing mail path (mocked in tests; failure leaves status draft and raises).

**Storms** — `fetch_storm_alerts(lat, lon)`:
- `GET https://api.weather.gov/alerts/active?point={lat},{lon}` with an
  identifying User-Agent (NWS policy), 10s timeout.
- Keep events whose `properties.event` matches
  hail|tornado|thunderstorm|high wind (case-insensitive).
- `zip` convenience: resolved via `https://api.zippopotam.us/us/{zip}`
  (keyless). Any fetch failure → honest 502 "weather service unreachable",
  never fabricated alerts.
- `launch_campaign(db, org_id, storm_event_id, message_body)`:
  recipients = customers of the org whose `address` contains one of the
  event's ZIPs (extracted from `area_desc`); dedupe; SMS each via the
  existing Twilio outbound helper (`_send_recovery_sms` pattern — v1 reuses
  the same sender, opt-out language appended); create lead rows
  (`source='storm_campaign'`, one per recipient); toll
  `storm.campaign_launched` + per-recipient `sms.auto_reply`.
- Recipient phones are never returned in the response (counts only).

## Router (`backend/app/routers/roofing.py`, prefix `/roofing`)

- `GET/POST /suppliers`, `GET/PATCH/DELETE /suppliers/{id}`
- `GET/POST /purchase-orders`, `GET/PATCH /purchase-orders/{id}`,
  `POST /purchase-orders/{id}/send`
- `POST /quotes/{id}/takeoff`
- `GET /storms/recent?zip=` (or `?lat=&lon=`)
- `POST /storms/campaigns`
- All org-scoped via `get_auth`; cross-org → 404 (never 403).
- Registered in `main.py` under `/api/v1`.

## Config

`TOLL_RATES` additions:
```python
"takeoff.computed":        25,  # $0.25 per takeoff
"po.sent":                 50,  # $0.50 per PO sent
"storm.campaign_launched":100,  # $1.00 per storm campaign
```

## Frontend (`frontend/src/pages/Suppliers.jsx`)

Suppliers CRUD + purchase-order list with a Send action; route added to the
app nav. Takeoff entry lives on the existing quote surface (Sales page):
squares/pitch/waste/material form → `POST /quotes/{id}/takeoff`.

## Failure modes (TW-208)

- NWS/zippopotam unreachable → 502, no partial campaign, no fabricated data.
- Twilio unconfigured → campaign recorded as launched with 0 messages sent,
  recipients queued honestly in the response (no silent drops).
- PO send email failure → status stays draft, error surfaces.
