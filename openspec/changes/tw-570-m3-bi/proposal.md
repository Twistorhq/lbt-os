# TW-570 — M3 white-label BI (Databox wedge): proposal

## What
White-label BI dashboards for Twistor Trades: org-branded dashboards
rendered from aggregate metrics, a connector framework that turns synced
integrations into dashboard widgets, and token-gated public embeds that can
only ever serve aggregates. Productizes the NKC demonstrator muscle
(patterns, not code) inside lbt-os.

## Why
Databox charges $59–$399/mo for white-labeled client dashboards. Our wedge:
white-label BI is included in the flat tiers, connectors are the long pole,
and every embed view + connector sync meters against the M1 toll ledger.

## Scope
- Migration `supabase/migration_bi_whitelabel.sql`: `bi_dashboards`,
  `bi_embeds`, `bi_metric_snapshots` — each with org-isolation RLS per the
  TW-547 contract.
- Router `backend/app/routers/bi.py`: dashboard CRUD (auth, org-scoped,
  cross-org → 404), embed token issue/revoke, public `GET /bi/embed/{token}`
  (no JWT, aggregate-only), connector list + sync.
- Service `backend/app/services/bi_service.py`: dashboards, theming, embed
  rendering, toll emission.
- Service `backend/app/services/bi_connectors.py`: `BIConnector` protocol
  (metric_definitions + compute) with 2 connectors v1: `internal` (wraps
  existing `services/metrics.py` aggregates) and `stripe` (aggregates from
  locally-synced sales/customers rows).
- Frontend `frontend/src/pages/BI.jsx`: dashboard list/create, themed widget
  viewer, "Get embed code" iframe snippet.
- Toll rates: `bi.embed_view` $0.05, `bi.connector_sync` $0.25.

## Out of scope
- Scheduled PNG/PDF snapshots (email delivery) — later ticket.
- LLM-generated dashboards or natural-language widget builder.
- Row-level embeds — needs a new threat review, never a config flip
  (TW-547 risk 3, hard rule).
- Porting the NKC demonstrator as a shared package (TW-547 §8).

## Rosa TW-547 findings — answered here, not deferred

### is_public vs embed token (Minor 7)
One public-access mechanism, not two. Embed tokens are the ONLY way to view
a dashboard without auth. `bi_dashboards.is_public` is the master kill
switch: when false, the embed endpoint returns 404 even for a valid token.
Documented in the migration comment and the design doc.

### Aggregate-only on the public embed (Minor 8)
Enforced at the app level in the embed renderer, as a mechanism, not a
convention (Rosa round-2 Major 1): widgets reference metric keys; only keys
in the `WIDGET_METRICS` allowlist render, AND every emitted value is
validated against the metric's declared output shape before it enters the
payload. A connector returning row-shaped data fails closed with a shape
violation — never the raw value. There is no code path from an embed token
to row-level customer data: the renderer reads only org-scoped
`bi_metric_snapshots` and emits only shape-validated aggregates.

### Embed token generation (Minor 5 + round-2 Minor 6)
`secrets.token_urlsafe(32)` (CSPRNG). Only the SHA-256 hash is stored
(`bi_embeds.token_hash`, UNIQUE); the raw token is returned once at issue
and never logged (bearer tokens in URLs leak via access logs and Referer).

## Compliance notes
- PII doctrine (TW-552): connectors compute aggregates in-app (the
  `internal` connector reads row-level data exactly as `services/metrics.py`
  already does) — but only shape-validated aggregates ever leave through
  embeds or the API. No PII in embed payloads, ever; raw tokens never in
  logs.
- AGPL yellow zone: chart layer is our own; Metabase/Grafana/SigNoz are
  inspiration only, never embedded.
