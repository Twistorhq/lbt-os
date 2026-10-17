# TW-571 — M4 roofing pro pack (JobNimbus wedge): proposal

## What
Estimating takeoff on quotes (exact-cent math), supplier + purchase-order
management, and storm-triggered customer campaigns — the vertical-pack proof
for Twistor Trades. Roofing first; the pattern (`attributes` JSONB + vertical
router + vertical migration) repeats for future verticals without new core
tables.

## Why
JobNimbus owns roofing by being vertical-specific. The wedge: takeoff math
that turns measurements into line items in one tap, supplier ordering without
leaving the job record, and the "week a hailstorm hits is when roofers buy
software" trigger — mechanized via keyless weather.gov alerts.

## Scope
- Migration `supabase/migration_roofing.sql`: `suppliers`, `purchase_orders`
  tables (RLS org isolation); ADD `quotes.attributes` JSONB (it does not exist
  — Rosa TW-547 minor 3, the "gains a key" language was wrong).
- `POST /quotes/{id}/takeoff`: {squares, pitch, waste_pct, material} →
  Decimal-exact line items (material + labor), stored on
  `quotes.attributes.takeoff`; quote `line_items`/`total` updated.
- CRUD `/suppliers`, CRUD `/purchase-orders`,
  `POST /purchase-orders/{id}/send` (status → sent, emails supplier via
  existing mail path; mocked in tests).
- `GET /storms/recent?zip=` (or `lat`/`lon`): keyless NWS alerts, filtered to
  roofing-relevant events (hail, tornado, severe thunderstorm wind).
- `POST /storms/campaigns`: storm event → customers in affected ZIPs →
  campaign record + SMS via existing Twilio outbound helper + leads
  (`source='storm_campaign'`). Deterministic rules first.
- Every metered event emits to the M1 `toll_events` ledger (idempotent):
  `takeoff.computed` $0.25, `po.sent` $0.50, `storm.campaign_launched` $1.00;
  per-recipient SMS reuses `sms.auto_reply` $0.05.
- Frontend: Suppliers page (CRUD + PO list + send action); takeoff entry on
  the quote surface.

## Out of scope
- EagleView-style proprietary aerial measurement (manual takeoff v1).
- LLM anything; marketing blasts to non-customers (explicit opt-in required —
  future ticket).
- Twilio voice number purchase (Justynn/manual; unrelated to M4).

## Compliance (answered here, not deferred)

### PII doctrine (TW-552)
Supplier `phone`/`ordering_email` are bronze-layer by necessity: the PO-send
flow must email the supplier. They live on the `suppliers` row (the bronze
for supplier contacts); API responses mask them (email local-part masked,
phone last-4 only). No supplier PII in application logs. Customer phones used
for storm SMS are masked in campaign responses; message bodies are never
stored.

### TCPA consent model (Rosa TW-547 Major 2)
v1 storm campaigns target **existing customers only** — the shop's own
customer list, an existing business relationship. The message is an
informational service offer ("free storm inspection after last night's hail"),
not telemarketing, and every message carries opt-out language (STOP honored
via the M2 inbound-SMS path). Marketing blasts to prospects/non-customers
require explicit prior express written consent and are OUT OF SCOPE for v1.
10DLC registration rides the shop's existing M2 number registration.
