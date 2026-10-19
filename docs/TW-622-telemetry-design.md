# TW-622 Twistor Telemetry — Design

**Prepared by Twistor Holdings LLC**

Product analytics, clean-room from the PostHog mechanism (MIT core). No
PostHog code. TW-209's first-party analytics mandate made real.

## The wedge (TW-630)

PostHog's #1 anger: event-volume metering that explodes with success —
$20k–$71k/year surprise bills, per-product meter cliffs, flag evaluations
metered too. Telemetry's answer:

1. **Capture is NOT per-event tolled.** The flat subscription covers capture
   up to a generous monthly tier. Tolling per event would recreate the exact
   meter this product exists to kill. (Deliberate, documented.)
2. **User-settable cap with graceful degradation.** Over the monthly tier,
   ingestion samples deterministically (recorded rate) instead of dropping
   or surprise-billing. Sampling, not silence. Warn at 80%.
3. **No-code by default.** Saved insights (trends, funnels, retention,
   breakdowns) are plain-language definitions — the client side gets zero
   code, per the dual-lens standard.

## PII doctrine (TW-552)

- `distinct_id` is HMAC-SHA256-hashed at ingestion, keyed per org. Raw
  identifiers never touch the events table.
- Event properties are recursively scrubbed at ingestion: values under
  PII-shaped keys (email, phone, name, address, ssn, plus org-configured
  extras) are HMAC-hashed. Gold (query results) carries aggregates only.
- V2 hardening: dedicated per-org salt rotation. V1 uses org_id-keyed HMAC
  (deterministic, documented).

## Modules (`backend/app/telemetry/`)

| Module | Role |
|---|---|
| `models.py` | Pydantic: events, batches, queries, funnel/retention definitions, insights. Fail-loud validation. |
| `scrub.py` | Pure PII scrubbing at ingestion (recursive). |
| `query.py` | Pure query engine: trends, funnels, retention, breakdowns over event rows. No DB. |
| `budgets.py` | Sampling-degradation budget contract (pure decision + service glue). |
| `service.py` | Supabase CRUD: ingest, bounded event fetch, insights, budgets. |
| `migration.py` | PostHog import: events/funnels map; replay, heatmaps, experiments reported unmapped (out of V1 scope, honestly). |
| `datafeed.py` | Cohort-level aggregates (k>=5 gate) feeding the benchmark niches. |
| `router.py` | HTTP API: capture, queries, insights admin, PostHog import, datafeed export. |

## Tables (`supabase/migration_telemetry_v1.sql`)

- `tlm_events` — org_id, event_name, distinct_id_hash, properties JSONB
  (scrubbed), occurred_at, ingested_at, sampled, sample_rate.
- `tlm_insights` — saved trend/funnel/retention/breakdown definitions,
  versioned, attributed.
- `tlm_budgets` — org monthly cap, used, period_start, warn_sent.
- RLS deny-all; service role only. Idempotent migration.
- `tlm_bump_budget` RPC: atomic monthly-counter bump (single statement;
  no lost-update race). The backend falls back to a read-modify-write
  upsert when the function is not deployed yet.

## API

- `POST /telemetry/capture` — batch events (1200/min). Scrub → budget
  decision → insert → 202. No per-event toll.
- `POST /telemetry/trends|/funnels|/retention|/breakdown` — queries (120/min).
- `POST /telemetry/admin/insights` (+GET/PATCH/DELETE) — saved insights.
- `POST /telemetry/admin/import/posthog` — migration import (30/min).
- `POST /telemetry/datafeed/export` — benchmark aggregates (30/min).

All endpoints org-scoped from the Identity JWT. PII is scrubbed at
ingestion (TW-552). V1 limitation (documented): the scrubber is a
key-name heuristic — free-text values under non-hint keys are stored
as-is; orgs declare PII-shaped custom keys via extra_pii_keys.

All query responses carry a `truncated` flag when the bounded event
fetch hits its cap — honest uncertainty, never silently incomplete
counts.

## Customer zero

Dez's outreach machine: sends, opens, replies, lead-stage transitions flow
through `POST /telemetry/capture` from day one. Funnel: sent → opened →
replied → qualified → pilot.

## Out of scope (V1)

Session replay, heatmaps, experiments/A-B, anomaly detection. When models
land, they ship with the 9-point governance spec.

## Quality bar

"Would someone choose this over PostHog?" — on predictability, no-code
usability, and honest migration, not feature count.
