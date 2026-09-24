<!-- Prepared by Twistor Holdings LLC -->

# Service Business Data Model (Canonical)

**Migration:** `supabase/migration_service_datamodel.sql` · **Ticket:** TW-201 · **Branch:** `feature/tw-201-leak-engine`

One normalized schema every Twistor vertical maps into. HVAC is the day-one pilot (go-live Oct 2); the same six tables absorb all 12 service verticals without schema changes — vertical differences live in `asset_type`, `job_type`, `visit_type`, `plan_name`, and the free-form `attributes` JSONB, never in new columns.

Existing tables (untouched, still the foundation): `organizations`, `leads` (+ `lead_stage_history`), `customers`, `sales`, `expenses`, `audit_reports`, `integration_connections`, `integration_sync_runs`, `integration_record_links`.

## Entity map

| Canonical table | What it is | Vertical examples |
|---|---|---|
| `technicians` | Org team members doing field work | HVAC techs, plumbers, electricians, stylists, hygienists, detailers |
| `service_assets` | Installed equipment / assets at a customer site | HVAC: furnace, AC, heat pump, water heater, panel, thermostat, generator. Plumbing: water heater, softener, sump pump. Electrical: panel, generator, EV charger. Vet: patient (attributes: species, breed, weight). Dental: chair, x-ray unit. Landscaping: mower fleet, irrigation system. Cleaning: supply inventory |
| `service_plans` | Recurring maintenance agreements | HVAC maintenance club, plumbing protection plan, dental hygiene plan, salon membership, gym membership, lawn-care seasonal plan |
| `jobs` | Work orders | Install, repair, maintenance, inspection, emergency. Vet: procedure. Dental: procedure/appointment treatment. Salon: booking. Restaurant: catering order |
| `appointments` | Visits (incl. plan visits, estimates, no-shows) | Plan maintenance visits, job visits, free estimates, follow-up callbacks |
| `quotes` | Priced proposals with line items and follow-up state | Trades quotes, **dental treatment plans**, vet estimates, renovation bids, catering proposals |
| `customers` (+`property_type`, `home_age_year`) | The account / site / household | Vet: the pet owner (patients live in `service_assets`); Dental: the patient; Property mgmt: the building |

### Mapping notes for the 12 verticals

- **Dental:** "treatment plan" → `quotes` (line_items = procedures, status tracks acceptance). Patient → `customers`. Equipment → `service_assets` only for office equipment, not per-patient.
- **Vet:** pet owner → `customers`; each pet → one `service_assets` row (asset_type = `pet`, attributes = species/breed/weight/dob). Procedures → `jobs` with `asset_id` = the pet.
- **Salon/spa, gym, cleaning:** memberships → `service_plans` (visits_per_year can exceed monthly cadence; `visits_completed` tracks redemption).
- **Real estate / property management:** each managed building → `customers` (property_type = `commercial`/`multi_family`); big equipment per building → `service_assets`.
- **Restaurants, gig workers:** same tables apply (plans may be unused; quotes cover catering/event bids).

### Deliberately NOT modeled

- **Line items on jobs/invoices:** live in `sales` (already exists) and `quotes.line_items`. Jobs carry only the `total`.
- **Payments/invoices:** `sales` (existing) — leak detectors read revenue from `sales.payment_status`.
- **Technician credentials/licenses:** org HR concern, out of scope for leak detection.
- **New PII:** none added. Names, phones, emails, addresses already live on `customers`; this migration adds no sensitive columns.

## Detector query patterns (Oct 2 HVAC set)

Zeke's detectors read these shapes; every one is index-backed:

1. **Replacement-age cohorts:** `SELECT ... FROM service_assets WHERE org_id = ? AND asset_type IN (...) AND install_date <= NOW() - (expected_life_years - 2) * INTERVAL '1 year'` → `idx_assets_org_type_install`.
2. **Plans missing visits:** `SELECT ... FROM service_plans WHERE org_id = ? AND status = 'active' AND visits_completed < visits_per_year` → `idx_plans_org_active`.
3. **Card-failure billing:** `... WHERE org_id = ? AND billing_status = 'card_failed'` → `idx_plans_org_billing`.
4. **Stale quotes:** `SELECT *, NOW() - COALESCE(last_follow_up_at, sent_at) AS days_stale FROM quotes WHERE org_id = ? AND status IN ('sent','follow_up') ORDER BY last_follow_up_at` → `idx_quotes_org_open_followup`.
5. **Plan visits not reconciled:** join `appointments` (plan_visit, completed) against `service_plans` per period → `idx_appts_org_plan`.
6. **No-show rate:** `... FROM appointments WHERE org_id = ? AND no_show AND scheduled_at > ...` → `idx_appts_org_noshow`.

Age of assets with NULL `install_date` are excluded from cohort queries — the import contract requires `install_date` for detector coverage, so pilot shops should supply it for equipment rows.

## Import contract (CSV / manual import)

Pilot shops import via CSV. **One CSV per entity.** Required columns are marked; everything else is optional. `org_id` is assigned at import time (never supplied by the shop). Dates accept ISO-8601 (`YYYY-MM-DD`).

### customers (existing) — new columns
- `name` (required) · `phone` · `email` · `address` · `tags` · `property_type` (`residential` | `commercial` | `multi_family`) · `home_age_year` (integer)

### technicians
- `name` (required) · `role` (`tech` | `lead_tech` | `installer` | `dispatcher` | `apprentice`) · `active` (true/false, default true)

### service_assets
- `customer` (required — match by customer name or phone; resolves to `customer_id`) · `asset_type` (required, e.g. `furnace`, `air_conditioner`) · `install_date` (required for detector coverage) · `expected_life_years` · `brand` · `model` · `serial` · `source` (default `import`) · `notes`
- Vertical-specific fields (tonnage, BTU, refrigerant, panel amps, pet species…) go in `attributes` — supply as extra columns prefixed `attr_`, e.g. `attr_tonnage=3.5`.

### service_plans
- `customer` (required) · `plan_name` (required) · `status` (`active` | `past_due` | `canceled` | `expired`, default `active`) · `billing_status` (`current` | `card_failed` | `payment_past_due`, default `current`) · `visits_per_year` (default 2) · `visits_completed` (default 0) · `annual_price` · `started_at` · `renewal_at` · `source` (default `import`)

### jobs
- `customer` (required) · `job_type` (required: `install` | `repair` | `maintenance` | `inspection` | `emergency` | `replacement`) · `status` (`scheduled` | `in_progress` | `completed` | `canceled`, default `scheduled`) · `scheduled_at` · `completed_at` · `total` · `technician` (match by name) · `asset` (match by asset_type + install_date for the customer) · `source` (default `import`) · `description`

### appointments
- `customer` (required) · `scheduled_at` (required) · `visit_type` (`job_visit` | `plan_visit` | `estimate` | `follow_up`, default `job_visit`) · `completed_at` · `no_show` (true/false, default false) · `technician` · `job` / `service_plan` (match keys; plan visits need the plan reference to reconcile `visits_completed`)

### quotes
- At least one of `customer` or `lead` (required) · `status` (`draft` | `sent` | `follow_up` | `won` | `lost` | `expired`, default `draft`) · `total` · `sent_at` · `last_follow_up_at` · `follow_up_count` (default 0) · `valid_until` · `quote_number` · `source` (default `import`) · `notes`
- `line_items`: supply as extra columns prefixed `line_`, e.g. `line_1=Condenser replacement|1|2400`. The importer parses `description|quantity|unit_price`.

### Import ordering
`technicians`, `customers` → `service_assets`, `service_plans` → `jobs`, `quotes` → `appointments`. Rows referencing customers that don't match get flagged, not auto-created (shops review the unmatched list).
