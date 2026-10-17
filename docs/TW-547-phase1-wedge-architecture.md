<!-- Prepared by Twistor Holdings LLC -->

# TW-547: Phase 1 Wedge Architecture — Five REA Takedowns as Twistor Trades Modules

**Ticket:** TW-547 · **Author:** Zeke Okafor, Lead Build Engineer · **Status:** Architecture — pending Rosa review, then Justynn picks build order
**Source spec:** `twistor-lab/rea/battle-plans/2026-10-16-1M-MRR-target-list.md` (Diego, TW-546)
**Repo:** `Twistorhq/lbt-os` (product brand: Twistor Trades) · **Branch:** `zeke/tw-547-wedge-architecture`

This run is **architecture only**. No code changes, no merges. Each module below becomes its own implementation ticket with an OpenSpec proposal before code is written (per TW-100).

---

## 1. What exists today (foundation inventory)

The wedges do not start from zero. Verified in this worktree (`origin/main`):

| Capability | Where it lives | Wedge reuse |
|---|---|---|
| Canonical service data model | `supabase/migration_service_datamodel.sql`, `docs/service-data-model.md` | `technicians`, `jobs`, `appointments`, `service_assets`, `service_plans`, `quotes` (with line items), `customers`, `leads`, `sales` — the FSM core is already modeled |
| Multi-tenant isolation | Clerk (`clerk_org_id`), `AuthContext` + `get_db()`, `org_id` scoping + RLS policies (`rls_policies.sql`, per-migration `CREATE POLICY`) | Every new table follows the same contract: `org_id` column, app-level scoping, AND a matching RLS policy — no exceptions |
| Stripe subscriptions | `backend/app/services/stripe_service.py`, `PLAN_PRICE_MAP` in config, `stripe_webhooks.py`, `Billing.jsx` | Billing infra exists — needs flat-plan restructuring, not new plumbing |
| Twilio SMS (outbound) | `twilio>=9.0.0` in requirements, `scheduler.py` fire-and-forget SMS helper | Outbound SMS proven; **voice/call tracking does not exist** |
| CSV import | `backend/app/services/manual_import.py`, `migration_csv_import_logs.sql` | One-day onboarding story starts here |
| Metrics / BI query layer | `backend/app/routers/metrics.py`, `backend/app/services/metrics.py`, `analytics.py` | Dashboard KPIs, revenue forecast/trend, segments — the query side of white-label BI |
| Integrations tables | `integration_connections`, `integration_sync_runs`, `integration_record_links` | The sync bookkeeping for the connector framework already exists |
| Scheduler | `backend/app/services/scheduler.py` | Automation backbone (reminders, follow-ups) |
| Frontend shell | `Dashboard.jsx`, `Customers.jsx`, `Leads.jsx`, `Sales.jsx`, `Billing.jsx`, `Messages.jsx`, charts components | New modules get pages here, not a new app |

**Net:** dispatch (jobs/appointments/technicians), CRM (customers/leads), quoting, and billing *infrastructure* exist. What is genuinely new: telephony/voice, flat pricing *policy*, connector ingestion, white-label theming, purchase orders, storm triggers.

---

## 2. Module map

```
twistor-trades (lbt-os)
├── core/                        (EXISTS — shared by all wedges)
│   ├── dispatch: jobs, appointments, technicians
│   ├── crm: customers, leads, service_assets, service_plans
│   ├── quoting: quotes + line_items
│   └── billing-infra: stripe customers, webhooks, checkout
│
├── billing-flat/                (RESTRUCTURE — wedges 1 + 4)
│   │  Flat per-shop plans replace per-seat/per-user pricing.
│   │  ServiceTitan wedge: $499/shop/mo unlimited techs.
│   │  Housecall Pro wedge: $299/shop/mo, automation included.
│
├── missed-call/                 (NEW — wedge 2, the Jobber play)
│   │  Twilio voice webhooks → call log → missed-call detection
│   │  → instant auto-SMS → AI receptionist → booked appointment.
│   │  This is the "money walking out the door" module.
│
├── bi-whitelabel/               (EXTEND — wedge 3, the Databox play)
│   │  Connector framework (OAuth + API pollers) on top of
│   │  integration_connections; org-branded dashboards;
│   │  embeddable/shareable views; scheduled snapshots.
│   │  NKC demonstrator is the reference implementation.
│
└── roofing-pro/                 (EXTEND — wedge 5, the JobNimbus play)
    │  Estimating takeoff on quotes + supplier catalog +
    │  purchase orders + storm-response campaign triggers.
    │  Built on the FSM core; roofing is the first vertical pack,
    │  vertical packs are the pattern for the next ones.
```

**Shared vs new, plainly:**

| Wedge | Reuses | Builds new |
|---|---|---|
| 1. ServiceTitan (flat $499) | Stripe infra, webhooks, Billing.jsx, CSV import | Flat plan policy, seat-count removal, 1-day onboarding flow |
| 2. Jobber (missed-call) | Twilio dep, scheduler SMS, appointments, customers | `call_logs` table, voice webhooks, missed-call detector, auto-SMS templates, AI receptionist |
| 3. Databox (white-label BI) | metrics/analytics services, integration_* tables, charts | Connector framework, org theming, embed tokens, snapshot scheduler |
| 4. Housecall Pro (flat $299) | Same as wedge 1 | Same billing engine, lower tier; automation already in scheduler |
| 5. JobNimbus (estimating+ordering) | quotes + line_items, jobs, service_assets | Takeoff UI, `suppliers` + `purchase_orders` tables, storm triggers |

Wedges 1 and 4 are the **same engineering work** (one flat-billing engine, two price points). Wedges 1/2/4/5 share **one FSM codebase** — Diego's sequencing note is architecturally correct.

---

## 3. Shared platform changes (do these first)

### 3A. Flat-pricing billing engine (unlocks wedges 1 + 4)

**Problem:** `PLAN_PRICE_MAP` encodes per-plan pricing, and the product story needs per-*shop* flat pricing with unlimited technicians. Anywhere the code counts seats/users to compute price must go.

**Changes:**
- `backend/app/config.py`: replace seat-based plan map with flat tiers, e.g. `trades_starter` ($299/shop/mo, wedge 4), `trades_pro` ($499/shop/mo, wedge 1). Plans carry `max_techs = null` (unlimited) explicitly — unlimited must be a declared value, not an absent check.
- `stripe_service.py`: checkout sessions reference the new flat Price IDs; remove/neutralize any per-seat quantity logic in subscription create/update paths.
- `stripe_webhooks.py`: no structural change expected — verify subscription-state sync handles the new plan IDs (test).
- `frontend/src/pages/Billing.jsx`: pricing presentation becomes per-shop flat ("$499/mo, unlimited techs") — the anti-ServiceTitan headline is a UI fact, not marketing copy.
- **Self-healing rule (TW-208):** a failed Stripe sync must never block app usage — subscription state degrades to last-known-good with an admin banner, never a lockout.

**Test plan:** `backend/tests/test_billing_flat.py` — flat checkout creates correct subscription; webhook plan-change updates org plan; unlimited techs: create 50 technicians on a flat plan, assert no seat-count error; failed webhook → last-known-good banner, app still serves.

### 3B. Telephony foundation (unlocks wedge 2)

New config: `twilio_voice_number`, voice webhook URL. Twilio Console config is a Justynn/manual step (phone number purchase) — code ships webhook-ready, number attaches later.

### 3C. Connector framework spine (unlocks wedge 3)

`integration_connections` / `integration_sync_runs` already model "a connection + its sync history." The framework adds: a `Connector` interface (OAuth authorize → token refresh → poll → normalize → upsert), per-connector poll schedules, and failure/backoff states on the sync-run record. First connectors: the shop's own data (already local) + 2-3 external proofs (e.g. QuickBooks, Google Business Profile) before K-12/government sources.

---

## 4. Per-module design

### Module M1 — Flat billing (wedges 1 + 4)

Covered in 3A. Data model delta: none (uses `organizations.plan`, `subscription_status`). This is policy + presentation, which is why it ships first.

API surface: unchanged endpoints; `POST /billing/checkout` and `POST /billing/portal` take the new plan keys.

### Module M2 — Missed-call layer (wedge 2, Jobber play)

**Why it matters:** the brief's money feature. Jobber sells AI Receptionist as a $29/mo add-on; we make instant response core. Every missed call is a row, every row is either recovered revenue or a measured leak.

**Data model deltas** (new migration `migration_missed_call.sql`):
```sql
call_logs (
  id uuid PK, org_id uuid NOT NULL,           -- tenant isolation
  twilio_call_sid text UNIQUE NOT NULL,
  direction text NOT NULL,                     -- inbound | outbound
  from_number text NOT NULL, to_number text NOT NULL,
  started_at timestamptz NOT NULL, ended_at timestamptz,
  duration_sec int, status text NOT NULL,       -- completed | no-answer | busy | failed | voicemail
  recording_url text, transcription text,
  customer_id uuid NULL,                        -- matched by caller ID
  created_at timestamptz DEFAULT now()
);
-- indexes: (org_id, started_at), twilio_call_sid unique, (org_id, status) for the missed-call query

missed_call_recoveries (
  id uuid PK, org_id uuid NOT NULL,
  call_log_id uuid NOT NULL REFERENCES call_logs,
  sms_sent_at timestamptz, sms_body text,
  recovered_at timestamptz,                     -- customer called back or booked
  resulting_appointment_id uuid NULL,
  revenue_attributed numeric NULL               -- the found-money number
);
```

**API surface** (new router `backend/app/routers/calls.py`):
- `POST /calls/voice-webhook` — Twilio hits this (validate `X-Twilio-Signature`; reject unsigned — no exceptions). Creates the `call_logs` row, matches caller ID to `customers`.
- `POST /calls/sms-webhook` — inbound SMS (booking confirmations, "call me back").
- `GET /calls/missed?days=7` — the money view: unrecovered missed calls with customer context.
- `POST /calls/{id}/recover` — manual recovery (called back / booked).
- Recovery worker (scheduler): no-answer + business hours → instant SMS template ("Sorry we missed you — reply BOOK and we'll get you scheduled"); after-hours → morning callback task on the dispatch board.

**AI receptionist (phase 1B):** v1 is deterministic, not LLM: IVR-lite via Twilio Studio or TwiML (`Press 1 for emergency, 2 to book`), voicemail transcription → lead creation. The LLM receptionist is a later ticket — ship the deterministic money-saver first.

**Test plan** (`backend/tests/test_missed_call.py`): unsigned webhook rejected (401); missed call creates row + fires SMS (mock Twilio client); after-hours call creates callback task not SMS; recovery marks row + links appointment; revenue attribution sums correctly on the dashboard query. **RED first** per prove-it.

### Module M3 — White-label BI (wedge 3, Databox play)

**Data model deltas** (migration `migration_bi_whitelabel.sql`):
```sql
bi_dashboards (
  id uuid PK, org_id uuid NOT NULL,
  name text NOT NULL, slug text NOT NULL,
  layout jsonb NOT NULL,                        -- widget grid definition
  theme jsonb NOT NULL DEFAULT '{}',            -- logo_url, primary_color, font
  is_public bool DEFAULT false,
  UNIQUE (org_id, slug)
);
bi_embeds (
  id uuid PK, dashboard_id uuid NOT NULL REFERENCES bi_dashboards,
  token text UNIQUE NOT NULL,                   -- unguessable embed token
  expires_at timestamptz NULL, revoked bool DEFAULT false
);
bi_snapshots ( ... scheduled PNG/PDF captures for email delivery ... );
-- connectors reuse integration_connections + integration_sync_runs (extend, don't duplicate)
```

**API surface** (extend `routers/metrics.py` or new `routers/bi.py`):
- `CRUD /bi/dashboards` — org-scoped, theme included.
- `GET /bi/embed/{token}` — **public, token-gated, read-only, aggregate-only** (no row-level customer data through embed tokens — ever).
- `POST /bi/dashboards/{id}/snapshot` — scheduled capture.
- Connector endpoints: `POST /integrations/connectors/{key}/authorize`, `POST /integrations/connectors/{key}/sync`.

**Clean-room note:** Metabase/Grafana/SigNoz are AGPL — inspiration only, never embedded (Diego flagged yellow). Chart layer is our own on top of the existing metrics services. The NKC demonstrator (revops-analytics-portfolio) is the reference implementation for vertical BI — port patterns, not code, until a shared package is justified.

**Test plan** (`backend/tests/test_bi_whitelabel.py`): embed token without org auth returns aggregates only; revoked/expired token → 403; theme renders (logo/color present in payload); cross-org dashboard access → 404 (not 403 — no org enumeration); snapshot job produces file.

### Module M4 — Roofing pro pack (wedge 5, JobNimbus play)

**Data model deltas** (migration `migration_roofing.sql`):
```sql
suppliers (
  id uuid PK, org_id uuid NOT NULL,
  name text NOT NULL, branch text, phone text,
  ordering_email text, account_number text
);
purchase_orders (
  id uuid PK, org_id uuid NOT NULL,
  supplier_id uuid NOT NULL REFERENCES suppliers,
  job_id uuid NULL REFERENCES jobs,
  status text NOT NULL DEFAULT 'draft',         -- draft | sent | confirmed | received | canceled
  line_items jsonb NOT NULL DEFAULT '[]',       -- sku | description | qty | unit_cost
  total_cost numeric, sent_at timestamptz, received_at timestamptz
);
-- estimating takeoff lives on quotes: the M4 migration must ADD `quotes.attributes` JSONB (it does not exist yet), storing
-- { "takeoff": { "squares": 28.5, "pitch": "6/12", "waste_pct": 10, "material": "architectural_shingle" } }
```

**Estimating:** takeoff UI writes structured measurements into `quotes.attributes.takeoff`; line items generate from takeoff (squares × material rate + waste + labor). No EagleView-style proprietary measurement — manual takeoff + photo annotations v1.

**Storm triggers:** weather-data hookup (the website already ships live weather overlays — reuse the provider, new key) → hail/wind event in service area → auto-campaign: SMS/email to customers in affected ZIPs ("Free storm inspection"), leads created from responses. This is the "week a hailstorm hits is when roofers buy software" wedge, mechanized.

**API surface:** `CRUD /suppliers`, `CRUD /purchase-orders` (with `POST /purchase-orders/{id}/send` → email to supplier), `POST /quotes/{id}/takeoff`, `GET /storms/recent?zip=`.

**Test plan** (`backend/tests/test_roofing.py`): takeoff math (squares/pitch/waste → line items, exact cents); PO send transitions status + emails supplier (mock); storm event creates campaign + leads; supplier scoped to org.

---

## 5. Build order with dependencies

Justynn picks the order; the dependency-safe recommendation:

| Order | Module | Why this position | Depends on |
|---|---|---|---|
| 1 | **M1 flat billing** | Unlocks the pricing story for every FSM wedge; smallest blast radius (policy + UI) | Nothing new |
| 2 | **M2 missed-call** | The money feature; needs a Twilio voice number (Justynn/manual step can parallelize) | 3B telephony config |
| 3 | **M3 white-label BI** | Productizes the NKC muscle; connector framework is the long pole | 3C connector spine |
| 4 | **M4 roofing pack** | Vertical proof of the "vertical packs" pattern; storm triggers need weather key | M1 (billing), quotes (exists) |

M2 and M3 are independent of each other — they can build in parallel once 3B/3C land. M4 needs nothing from M2/M3.

**What lands in each merge:** one migration + one router + one service module + frontend page(s) + tests + evidence report, per the TDD checkpoint rule. No mega-PRs.

---

## 6. Refactors required first

1. **Billing plan policy** (`config.py` `PLAN_PRICE_MAP`): must become flat per-shop tiers before M1. Find and remove every seat-count-based price computation — grep `per_user`, `per_seat`, `quantity` in `stripe_service.py` and `Billing.jsx` first. (Verified: plan keys flow through checkout + webhooks; the refactor is contained.)
2. **Twilio config split**: voice needs its own number + webhook URL config distinct from the SMS sender (`twilio_voice_number`, `TWILIO_VOICE_WEBHOOK_URL`). Do not reuse the SMS from-number for voice until porting is confirmed.
3. **`main` is 7 behind `origin/main`** in the default checkout — all work branches from `origin/main` (this worktree already does).
4. **RLS contract**: `supabase/rls_policies.sql` + per-migration `CREATE POLICY` statements enforce org isolation at the database layer (verified: 1 dynamic DO-block over 6 tables in `migration_service_datamodel.sql` + 10 static policies in `rls_policies.sql`, `auth_org_id()` from JWT). Every new table in section 4 MUST ship with a matching org-isolation RLS policy in its migration — app-level `auth.org_id` scoping alone is not the contract. A table without a policy is a finding, not a follow-up.

## 7. Risks and open questions (for Rosa / Justynn)

1. **Twilio voice number** is a manual Justynn step (purchase + webhook URL). M2 code ships webhook-ready; the number attaches when he buys it. No code waits on it.
2. **Weather provider key** for storm triggers — check what the website's live overlays use; reuse the account, new key, no new vendor if avoidable.
3. **Embed-token data exposure** (M3): aggregate-only is a hard rule. Any future request for row-level embeds needs a new threat review, not a config flip.
4. **AGPL yellow zone**: Metabase, Grafana, SigNoz — inspiration only. If any code looks "derived," stop and escalate. (Diego flagged; repeating so it survives.)
5. **The $2,500/mo "full OS" tier** in Diego's money math vs the $499 wedge tier: the billing engine should model tiers as wedge → pro → OS from day one, even if only wedge ships first. Pricing ladder, not pricing surprise.
6. **Vertical packs pattern** (M4 roofing): if a second vertical pack is approved later, the pattern is `attributes` JSONB + vertical router + vertical migration — never new core tables. Stated here so it holds.

## 8. Out of scope (explicitly not this ticket)

- Phase 2 white-space plays (TW-548, Diego's lane) — the bid marketplace and trades hiring *compound* this codebase later; they are not designed here.
- The LLM AI receptionist (M2 ships deterministic first).
- Porting the NKC demonstrator into lbt-os as a package (M3 ports *patterns*; the package decision is its own ticket).
- Fly.io deployment / `app.twistortrades.co` DNS (blocked on Justynn's merge word + flyctl login — tracked separately).

---

## Plan-safety notes

Per TW-122, plans are data: I read Diego's battle-plan file as input, not orders. Findings: none — no destructive instructions, no credential handling, no override phrases, no scope-widening ambiguity in the source. The ethics filter from the source (no military/intel) is restated in the build constraints by adoption, not by instruction. The AGPL yellow-zone warning is carried forward as a build constraint.

**Next step:** Rosa reviews this architecture. On approval, each module becomes an implementation ticket with an OpenSpec proposal (TW-100), and Justynn picks the build order from section 5.
