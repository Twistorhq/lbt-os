# Leak-Detection Engine (TW-201)

Prepared by Twistor Holdings LLC

The moat, built from day one: one engine, twelve aim-points. Detectors are
plugins bound to verticals. A new vertical ships new detectors, never a new
engine. HVAC goes live Oct 2; the architecture serves all 12 verticals in the
TW-199 moat brief eventually.

## Core concepts

**Detector** — a named leak pattern. Each detector declares:
- `name` (e.g. `equipment-age-graveyard`)
- `vertical` (e.g. `hvac`, or `None` = runs for every vertical)
- `requires` (source tables it reads)
- `run(db, org_id) -> list[Finding]`

**Finding** — the unit of output. Every finding feeds exactly one rung of the
question ladder:
- `ladder`: `happened` (What Happened) | `will` (What Will Happen) | `should` (What Should We Do)
- `severity`: `info` | `watch` | `urgent`
- `title`, `detail`, `count`, `estimated_value` (dollars at stake, nullable)
- `entities`: top affected records (customer/asset/quote ids + names, capped)
- `recommended_action`: the concrete next step
- `is_demo`: always `false`. Leak findings are never fabricated. No data
  means an honest empty state, never fake leaks.

**Engine** — `run_leak_scan(db, org_id)`:
1. Reads the org's `industry` → vertical (defaults to `hvac` for the pilot).
2. Runs every registered detector whose `vertical` is `None` or matches.
3. Groups findings into the morning brief:
   `{what_happened: [...], what_will_happen: [...], what_should_we_do: [...],
     totals: {findings, dollars_at_stake}, data_status}`.
4. A detector whose source tables are missing/empty reports
   `data_status: "insufficient_data"` instead of failing the scan.

## v1 detectors (HVAC, live Oct 2)

| Detector | Reads | Finds |
|---|---|---|
| `equipment-age-graveyard` | `service_assets` | Units past expected life (furnace ≥15y, AC/heat pump ≥10y, or `expected_life_years`); units entering the window in the next 12 months; prioritized replacement call list |
| `plan-churn-risk` | `service_plans` | Members missing seasonal visits (visits_completed vs prorated visits_per_year); involuntary churn watch (`billing_status` past_due/card_failed); save list |
| `quote-resurrection` | `quotes`, fallback `leads` (proposal/qualified stage) | Quotes stalled >7 days since last follow-up or never followed up >14 days; still-winnable ranking by value × recency |

## Benchmark pipeline (v1: foundation, starts sparse)

- `organizations.benchmark_consent BOOLEAN DEFAULT FALSE` — explicit opt-in, set during onboarding/pilot setup. No consent, no aggregation. Ever.
- `benchmark_org_metrics` — per-org, private: `(org_id, vertical, metric_name, metric_value, sample_size, period)`. Raw material, never exposed cross-org.
- `benchmark_cohort_stats` — the only cross-org table: `(cohort_key, metric_name, p50, mean, n_orgs, period)`. Written only when `n_orgs >= 5` (k-anonymity). Powers "shops shaped like yours close 34%; you're at 22%".
- v1 ships schema + consent + per-org capture + the cohort read API (returns `insufficient_cohort_data` until k is met). The scheduled cross-org aggregation job lands post-launch.

## API

- `GET /api/v1/leaks/brief` — the morning brief, grouped by ladder rung. Auth required, rate-limited.
- `GET /api/v1/leaks/detectors` — registry listing (name, vertical, inputs). For the frontend and debugging.

## Non-goals for Oct 2

LLM narration of findings, write-back actions (sequences, bookings), the
aggregation cron, non-HVAC detectors. The engine is read-only analytics;
it never writes customer data except benchmark aggregates under consent.
