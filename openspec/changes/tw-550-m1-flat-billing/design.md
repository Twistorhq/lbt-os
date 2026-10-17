<!-- Prepared by Twistor Holdings LLC -->

# TW-550: M1 flat billing engine — design

## Plan catalog (`backend/app/config.py`)

```python
PLAN_PRICE_MAP = {
    "trades_starter": settings.stripe_price_trades_starter,  # $299/shop/mo
    "trades_pro":     settings.stripe_price_trades_pro,      # $499/shop/mo
}
PLAN_LIMITS = {
    "trades_starter": {"max_techs": None},  # None = unlimited, declared
    "trades_pro":     {"max_techs": None},
}
TRIAL_PERIOD_DAYS = 14
TOLL_RATES = {  # unit_rate_cents per event_type; M2 vocabulary, ledger-owned
    "missed_call.detected": 10,    # $0.10
    "sms.auto_reply":         5,   # $0.05
    "call.ai_handled":       50,   # $0.50
}
# Percent-of-value events (booking.recovered at 10% of job value): NOT in the
# map — the emitter computes the amount and passes amount_cents directly.
```

New settings: `stripe_price_trades_starter`, `stripe_price_trades_pro`
(required, `price_` prefix). Production validation in `model_post_init`
switches to the new keys. Legacy `STRIPE_PRICE_BASIC/PRO/PREMIUM` settings
stay (harmless) but are no longer referenced by the map.

`_ALLOWED_CHECKOUT_PLANS = {"trades_starter", "trades_pro"}` in
`stripe_webhooks.py`.

## Checkout (`stripe_service.create_checkout_session`)

- `line_items=[{"price": price_id, "quantity": 1}]` (unchanged — already flat).
- Add `subscription_data={"trial_period_days": TRIAL_PERIOD_DAYS, ...}`.
- Unlimited techs: no code counts seats today (verified by grep); the change
  is making unlimited *explicit* via `PLAN_LIMITS` + a `plan_allows_techs()`
  helper returning True for None. UI copy drops "Up to N team members".

## Webhook sync

`sync_subscription` unchanged structurally: plan comes from subscription
metadata, defaults `"basic"`, no map validation — so legacy and future plan
strings pass through. New test: plan change `basic` → `trades_pro` updates
`organizations.plan`.

`customer.subscription.deleted` currently resets `plan` to `"basic"`. Keep
(grandfathered baseline), do not invent a new default.

## Self-healing (`verify_checkout_session`)

Wrap the immediate `sync_subscription` call in try/except: on failure, log
`checkout_sync_deferred` and return the session payload anyway. The Stripe
webhook (`checkout.session.completed`) performs the authoritative sync with
retries. Rationale (TW-208): a transient DB blip must not turn a paid
checkout into a 500 for the buyer.

## Toll ledger

Migration `supabase/migration_toll_ledger.sql`:

```sql
create table toll_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id),
  event_type text not null,
  quantity numeric not null default 1,
  unit_rate_cents integer not null,
  amount_cents integer generated always as ((quantity * unit_rate_cents)::integer) stored,
  idempotency_key text not null unique,
  source_type text,            -- e.g. 'call_log', 'sms_log'
  source_id text,              -- emitter's row id
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
-- RLS: org isolation, mirroring rls_policies.sql contract
alter table toll_events enable row level security;
create policy toll_events_org_isolation on toll_events
  using (org_id = auth_org_id()) with check (org_id = auth_org_id());
```

Service `backend/app/services/toll_service.py`:

- `record_toll_event(db, org_id, event_type, quantity=1, idempotency_key, source_type=None, source_id=None)`:
  looks up `TOLL_RATES[event_type]` (ValueError on unknown — fail loud, not
  silent $0), inserts; on unique-violation of `idempotency_key` returns the
  existing row (no double-toll).
- `get_toll_summary(db, org_id, since)` → `{event_type: {count, amount_cents}}`
  for the dashboard spend view.

No router yet: M2/M3 call the service directly; the spend-view endpoint is
their ticket's scope. (Keeps this PR to one migration + service + config.)

## Frontend (`Billing.jsx`)

`plans` becomes the two flat tiers:

- Trades Starter — $299/mo — "One shop. Unlimited techs. Everything to run the day." (Housecall Pro undercut)
- Trades Pro — $499/mo — "One shop. Unlimited techs. The full money-finding stack." (ServiceTitan undercut)

Featured card: `trades_pro`. All seat-count language removed. Upgrade flow,
success banner, and portal link unchanged.

## Test plan

`backend/tests/test_billing_flat.py` (new, RED first):

| # | Behavior | Type |
|---|---|---|
| 1 | checkout `trades_starter` → session created, quantity 1, trial 14d | unit (mock stripe) |
| 2 | checkout `basic` (legacy) → ValueError unknown plan | unit |
| 3 | webhook plan change syncs `organizations.plan` to `trades_pro` | unit (mock db) |
| 4 | `plan_allows_techs` returns True (unlimited) for both tiers | unit |
| 5 | `verify_checkout_session` returns payload when sync throws | unit |
| 6 | `record_toll_event` computes amount from TOLL_RATES | unit (mock db) |
| 7 | duplicate `idempotency_key` → no second row | unit (mock db) |
| 8 | unknown event_type → ValueError | unit |
| 9 | `get_toll_summary` aggregates per event_type | unit (mock db) |

Existing `test_stripe_checkout.py` keeps passing (uses patched maps).

## Risks

- Stripe Price IDs for the new tiers don't exist yet — checkout 400s with a
  clear message until Justynn creates them (by design; `_configured_price_id`
  already fails loud).
- Frontend plan-key mismatch: `Billing.jsx` and `_ALLOWED_CHECKOUT_PLANS`
  must change together — same PR, verified by reading both.
