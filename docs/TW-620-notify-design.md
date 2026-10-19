# TW-620 Twistor Notify — Design

Prepared by Twistor Holdings LLC

**Status:** design → build. Clean-room from the Novu *mechanism* (MIT,
verified in the TW-617 brief). No Novu code. TW-630 complaint wedge drives
every product decision below.

## 1. What it is

One API for email, SMS, and push. Providers are dumb pipes behind a
channel-provider abstraction:

- **Templates** — one template, channel-specific bodies, `{{variable}}`
  substitution, per-org.
- **Least-cost routing** — every provider declares its per-unit cost; the
  router tries cheapest-first per channel.
- **Failover** — a provider error moves to the next provider, automatically.
  No single provider can hold an org hostage.
- **Cost preview** — `preview_cost()` returns the exact per-send cost BEFORE
  anything goes out. The TW-630 wedge: opaque per-message pricing is users'
  #1 anger at notification infra. Every send shows its cost first.
- **Delivery ledger** — every message and every attempt recorded. Delivery
  analytics is the CORE product (per-workflow, per-tenant, per-channel
  rates), not a beta tab — Novu's one consistent product complaint.
- **Silent-failure debugger** — a message's full attempt trail is one query
  away: what was tried, in what order, what failed, what it cost.

## 2. Module layout — `backend/app/notify/`

| File | Owns |
|---|---|
| `providers.py` | `Provider` interface + adapters: `ResendEmailProvider`, `TwilioSmsProvider`, `NullProvider` (dev/test), `PushProviderStub` |
| `routing.py` | Least-cost ordering + failover walk over an org's enabled providers |
| `service.py` | `send()` orchestration: template render → cost preview → route → attempts → ledger → toll |
| `templates.py` | Template CRUD + `{{var}}` rendering (missing-var fails loud) |
| `analytics.py` | Delivery rates per workflow / tenant / channel; silent-failure trail |
| `legacy.py` | Migration shim: drop-in wrappers over `services/email.py` (Resend) and `services/call_service.py` (Twilio) so call sites switch one import at a time |
| `router.py` | FastAPI: `/notify/...` endpoints, slowapi rate limits, org scoping |

## 3. Data model — `supabase/migration_notify_v1.sql`

- `ntf_providers` — org_id, channel (`email|sms|push`), provider_name,
  config JSONB (keys by reference, never values), cost_cents_per_unit,
  priority, is_enabled, last_health_at. One row per org/channel/provider.
- `ntf_templates` — org_id, name (unique per org), channel, subject_template,
  body_template, variables JSONB, is_active.
- `ntf_messages` — org_id, template_id, channel, workflow (free-text tag,
  e.g. `rate_limit_warning`, `outreach_touch_1`), recipient (see PII note),
  recipient_hash, status (`queued|sent|delivered|failed`), provider_used,
  cost_preview_cents, cost_actual_cents, toll_event_id, timestamps.
- `ntf_attempts` — message_id, provider, attempt_no, status, error (truncated),
  latency_ms, created_at.

RLS deny-all on all four tables (service role only), matching the identity
migration. Idempotent migration (`IF NOT EXISTS` throughout).

### PII note (trust domain)

`ntf_messages.recipient` stores the address/number because delivery tracking
and provider retries are functionally impossible without it — the same
justification as `idn_users.email` in the TW-619 migration. RLS deny-all;
never logged (logs carry `sha256:…` prefix hashes only, same as
`services/email.py::_recipient_tag`); never returned by any API except to
the owning org's admins; excluded from analytics aggregates.

## 4. Cost and tolls

- Provider cost is declared per unit and stamped on every message
  (`cost_preview_cents` before send, `cost_actual_cents` after).
- Orchestration toll: new `TOLL_RATES["notify.message_sent"] = 1`
  ($0.01 per orchestrated message), emitted with an idempotency key
  `notify:{message_id}` — no double-toll on retries.
- Provider spend is the org's own pipe bill; Notify never marks it up.
  The ledger is the receipt: "every dollar shows its source."

## 5. The 80/100 notification contract (TW-608)

TW-608's warning emails run through Notify. Contract:
- `service.send(..., priority="system")` bypasses user preference checks
  but never bypasses the cost preview or the ledger.
- A `rate_limit_warning` template ships seeded per org; the 80% warn path
  calls `send()` with `workflow="rate_limit_warning"`.
- The 100% shutoff path emits NO toll and sends NO message — Notify is never
  asked to send on a blocked call.

## 6. Outreach machine — customer zero

Dez's sequences call `notify.service.send()` from day one:
- `workflow="outreach_touch_N"` tags every message for analytics.
- Cost preview feeds the campaign budget view: projected send cost is known
  before the batch runs.

## 7. Migration path (drop-in)

- `notify/legacy.py::send_email(...)` keeps the exact signature of
  `services/email.py::_send` call sites; internally routes through Notify
  with provider `resend` pinned (no behavior change on day one).
- `notify/legacy.py::send_sms(...)` wraps the Twilio path in
  `services/call_service.py` the same way.
- Call sites migrate one import at a time; the old services keep working.

## 8. Non-goals (V1)

- Push delivery beyond the stub adapter interface (no push provider wired).
- In-app notification center / framework SDKs (the wedge says ship these
  framework-native and tiny — a V2 with its own design).
- User preference center UI (API supports it; Sofia's surface comes later).

## 9. Test plan

`backend/tests/test_notify_*.py`: provider interface conformance (each
adapter against the contract), least-cost ordering, failover walk
(primary down → secondary sends, ledger shows both attempts),
cost-preview-before-send, template missing-var failure, toll emission
idempotency, legacy shim parity, RLS migration idempotency (SQL parses,
IF NOT EXISTS throughout), analytics aggregation math.

## 10. Quality bar

"Would someone choose this over Novu?" — decided on: cost shown before
every send, analytics a PM can read, self-host in one command (compose
file ships with the migration), no provider lock-in by construction.
