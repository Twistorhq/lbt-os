# TW-570 — M3 white-label BI: design

## Data model (`supabase/migration_bi_whitelabel.sql`)

```sql
bi_dashboards (
  id uuid PK DEFAULT uuid_generate_v4(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  slug text NOT NULL,
  layout jsonb NOT NULL DEFAULT '{"widgets": []}',
  -- layout: {"widgets": [{"id": "w1", "metric": "revenue_30d", "title": "Revenue (30d)",
  --                        "chart": "kpi|trend", "connector": "internal"}]}
  theme jsonb NOT NULL DEFAULT '{}',
  -- theme keys (validated): logo_url, primary_color, secondary_color,
  -- font_family, background_color
  is_public bool NOT NULL DEFAULT false,   -- master kill switch for embeds
  created_at / updated_at timestamptz,
  UNIQUE (org_id, slug)
);
bi_embeds (
  id uuid PK DEFAULT uuid_generate_v4(),
  dashboard_id uuid NOT NULL REFERENCES bi_dashboards(id) ON DELETE CASCADE,
  token_hash text UNIQUE NOT NULL,         -- sha256 of the bearer token
                                          -- (raw token shown once at issue,
                                          -- never stored, never logged)
  expires_at timestamptz NULL,
  revoked bool NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT NOW()
);
bi_metric_snapshots (
  id uuid PK DEFAULT uuid_generate_v4(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  connector_key text NOT NULL,
  metric_key text NOT NULL,
  value jsonb NOT NULL,                    -- aggregate payload only
  computed_at timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, connector_key, metric_key)
);
```

RLS: `org_isolation` policy on each table —
`FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id())`,
matching the TW-547 §6 contract (cf. `migration_missed_call.sql`).

`is_public` semantics (Rosa Minor 7): embed tokens are the sole public
access mechanism. `is_public=false` ⇒ `GET /bi/embed/{token}` returns 404
even for a valid, unexpired, unrevoked token. There is no second sharing
mechanism.

## Connector framework (`services/bi_connectors.py`)

```python
class BIConnector(Protocol):
    key: str            # "internal", "stripe"
    label: str
    def metric_definitions(self) -> list[dict]: ...
        # {"key": "revenue_30d", "label": "Revenue (30d)", "unit": "usd",
        #  "output": {"type": "kpi",
        #             "shape": {"value": "number", "delta_pct": "number|null",
        #                       "label": "string"}}}
        # or {"output": {"type": "trend",
        #                "shape": {"points": [{"label": "string",
        #                                      "value": "number"}]}}}
        #
        # The "output" declaration is a SECURITY CONTRACT (Rosa Major 1):
        # render_embed validates every computed value against the declared
        # shape before it reaches the payload. A connector returning row
        # dicts, nested objects, or extra keys fails CLOSED — the widget
        # renders {"error": "shape violation"}, never the raw value.
    def compute(self, db, org_id: str) -> dict[str, Any]: ...
        # returns {metric_key: aggregate_value}; aggregates ONLY
```

- `internal` connector: wraps existing `services/metrics.py`
  (`get_dashboard_metrics`, `get_revenue_trend`) — zero new data plumbing.
- `stripe` connector: aggregates from locally-synced `sales`/`customers`
  rows (populated by the existing Stripe provider sync) — total revenue,
  charge count, average ticket. Reads local tables only; no live API calls
  in the render path.
- Registry `BI_CONNECTOR_REGISTRY: dict[str, BIConnector]`.
- `POST /bi/connectors/{key}/sync`: runs `compute`, upserts
  `bi_metric_snapshots`, then emits the `bi.connector_sync` toll
  **best-effort** (try/except — a toll failure after a successful snapshot
  upsert must not 500; Rosa Minor 4), with idempotency key
  `bi_connector_sync:{org_id}:{key}:{date}` (one toll per org/connector/
  day — re-syncs same day are free, encouraging freshness).

## Embed rendering (`services/bi_service.py`)

`render_embed(db, token) -> dict`:

**Tenant boundary (Rosa Major 2 — stated explicitly).** The public endpoint
carries no JWT, so RLS's `auth_org_id()` cannot apply. The endpoint therefore
uses a service-role DB client, and tenant isolation rests on this invariant,
enforced in code and covered by test:

> Every row the embed renderer touches is filtered by
> `org_id = <dashboard.org_id>`, where `dashboard.org_id` comes from the
> server-side token → embed → dashboard chain. The `org_id` NEVER comes from
> the request. Snapshots are queried as
> `bi_metric_snapshots.eq("org_id", dashboard_org_id)` — a token for org A's
> dashboard can never render org B's snapshots, even if both exist.

1. Hash the presented token (`sha256`) and look up `bi_embeds` by
   `token_hash`. Unknown → 401. Revoked or expired → 403. **The raw token
   is never written to application logs** — log `embed_id` only (Rosa
   Minor 6; bearer tokens in URLs leak via access logs, history, Referer).
2. Load dashboard; `is_public=false` → 404.
3. For each widget in `layout.widgets`: metric key MUST be in
   `WIDGET_METRICS` (the union of all registered connectors'
   `metric_definitions()` keys). Unknown key → widget renders
   `{"error": "unknown metric"}` — fails closed.
4. **Output-shape validation (Rosa Major 1).** The value for each widget is
   validated against the metric's declared `output.shape` before it enters
   the payload: allowed JSON scalar types only (`number`, `string`,
   `number|null`), trend points as `{label: string, value: number}` arrays.
   Any deviation — nested objects, arrays of dicts with extra keys, row
   payloads — fails closed with `{"error": "shape violation"}`. The
   allowlist gates metric *keys*; shape validation gates metric *values*.
   Together they make aggregate-only a mechanism, not a convention.
5. Values come from `bi_metric_snapshots` ONLY on the public path (Rosa
   Minor 5). If a snapshot is missing or stale (> 1h), the widget renders
   `{"status": "updating"}` — the public endpoint never triggers compute
   or writes, so unauthenticated requests cannot drive load.
6. Best-effort toll: `record_toll_event(db, org_id, "bi.embed_view",
   idempotency_key=f"bi_embed_view:{embed_id}:{uuid4()}")` wrapped in
   try/except — a toll failure never breaks the embed (TW-208). The
   `uuid4()` key is INTENTIONALLY non-deduping: each served view is a
   distinct metered action (documented per the nit).

There is deliberately no code path from a token to row-level data: the
renderer only reads `bi_metric_snapshots` rows scoped to the dashboard's
org, and only emits values that pass shape validation.

**HTML shell (Rosa Minor 8).** `GET /bi/embed/{token}/view` server-renders
the same `render_embed` payload into a minimal HTML page: theme applied as
CSS variables, KPI cards + trend tables, no client-side JS required. The
iframe snippet points at `/view`, not the JSON endpoint.

## Theming

`theme` jsonb validated on write (`validate_theme`):
- allowed keys: `logo_url`, `primary_color`, `secondary_color`,
  `font_family`, `background_color`;
- colors must match `^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$`;
- `logo_url` must be `https://` (no javascript:, no data:);
- `font_family` must match `^[a-zA-Z0-9 ,\-']{1,60}$` — system font stacks
  only; anything containing `(`, `)`, `;`, `/`, or `\` is rejected (blocks
  `url()` and other CSS injection via theme variables; Rosa Minor 7).
Invalid → 400. The embed payload carries the theme; the frontend applies
it as CSS variables (NKC dashboard pattern).

## API surface (`routers/bi.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET/POST | /bi/dashboards | JWT | org-scoped list/create |
| GET/PATCH/DELETE | /bi/dashboards/{id} | JWT | cross-org → 404 |
| POST | /bi/dashboards/{id}/embeds | JWT | issues token; raw token returned once, only `token_hash` stored |
| DELETE | /bi/embeds/{embed_id} | JWT | revoke (cross-org → 404) |
| GET | /bi/embed/{token} | none | 401/403/404 per above; JSON aggregates |
| GET | /bi/embed/{token}/view | none | 401/403/404 per above; server-rendered HTML shell for iframes |
| GET | /bi/connectors | JWT | connector + metric definitions |
| POST | /bi/connectors/{key}/sync | JWT | snapshot + toll |

## Toll rates (`config.py` TOLL_RATES)

- `bi.embed_view`: 5 ($0.05 per public embed view)
- `bi.connector_sync`: 25 ($0.25 per connector sync, once per org/key/day)

## Frontend (`frontend/src/pages/BI.jsx`)

Dashboard list → create (name, slug, theme fields) → viewer rendering
widgets from the embed-style payload with theme CSS variables → "Get embed
code" button producing `<iframe src="{origin}/bi/embed/{token}/view">`
snippet (the HTML shell route, not the JSON endpoint).
Compact page; no chart library (KPI + trend via existing patterns).

## Test plan (`backend/tests/test_bi_whitelabel.py`)

Follow the `test_missed_call.py` FakeDB pattern (no network, no real DB):
- RED-first: unknown token → 401; revoked → 403; expired → 403;
  `is_public=false` + valid token → 404; token guessing (random) → 401.
- Cross-org dashboard GET/PATCH/DELETE/embed-issue → 404 (not 403).
- Widget with non-allowlisted metric key → fails closed (error, no data).
- Theme validation: bad color / bad key / non-https logo → 400.
- Embed payload contains no PII-shaped fields (no emails/phones).
- Toll emission: embed view and connector sync call `record_toll_event`
  with expected event types; toll failure does not break the embed.
- Token uniqueness: 1000 generated tokens, all unique.
- Cross-org embed isolation (Rosa Major 2): snapshots seeded for org B;
  token for org A's dashboard renders zero org-B values.
- Shape-violation fail-closed (Rosa Major 1): connector stub returning a
  row-shaped dict for a declared KPI metric → widget `{"error":
  "shape violation"}`, payload contains no row fields.
- Migration policy test (Rosa Major 3): parse
  `supabase/migration_bi_whitelabel.sql` and assert a `CREATE POLICY ...
  org_isolation` statement exists for each of `bi_dashboards`,
  `bi_embeds`, `bi_metric_snapshots` referencing `auth_org_id()`.
