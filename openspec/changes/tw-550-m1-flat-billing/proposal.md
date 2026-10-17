<!-- Prepared by Twistor Holdings LLC -->

# TW-550: M1 flat billing engine — proposal

- Ticket: TW-550 · Reporter: Justynn · Owner: Zeke Okafor (Lead Build Engineer)
- Branch: `zeke/tw-550-m1-flat-billing` · Base: `zeke/tw-547-wedge-architecture` @ `38d8196`
- Architecture: `docs/TW-547-phase1-wedge-architecture.md` §3A + §4/M1 (Rosa APPROVED; minors 3–4 corrected on branch)
- Related: TW-549 (Twistor staple: lower-priced entry tiers + micro-tolling)

## Problem

The billing engine sells the old product: `basic`/`pro`/`premium` plans with
per-plan feature gates and seat-count language in the UI ("Up to 5 team
members"). The Phase 1 wedges need per-*shop* flat pricing with explicitly
unlimited technicians — the anti-ServiceTitan headline ($499/mo, unlimited
techs) and the Housecall Pro undercut ($299/mo, unlimited techs). There is
also no metering spine: micro-tolls (TW-549) need a ledger that metered
events can write to, and M2/M3 will emit those events.

## Scope (this ticket)

1. Restructure `PLAN_PRICE_MAP` to flat trades tiers (`trades_starter`
   $299/shop/mo, `trades_pro` $499/shop/mo), unlimited techs as a declared
   value. Remove seat-count-based pricing presentation.
2. Grandfather legacy `basic`/`pro`/`premium`: no new checkouts, but webhook
   sync keeps accepting any plan string from subscription metadata (it already
   does — no validation against the map).
3. 14-day trial on checkout (Justynn's land motion, zero credit risk).
4. Toll ledger: `toll_events` table + RLS org-isolation policy +
   `record_toll_event()` service (idempotent) + `TOLL_RATES` for the M2 event
   vocabulary. M2/M3 emit; M1 ships the ledger.
5. Self-healing: a failed subscription sync must never block the checkout
   verification response (last-known-good stays in DB; webhook retries later).

## Out of scope

- M2/M3/M4 modules. Emitting toll events (call/SMS handlers) is their work.
- Changing Stripe Price IDs in production (Justynn/manual step).
- LLM anything. Deterministic policy code only.

## Acceptance

- `POST /billing/checkout?plan=trades_starter|trades_pro` creates a
  quantity-1 subscription checkout with 14-day trial; legacy plan keys 400.
- Webhook `customer.subscription.updated` with a new plan ID syncs
  `organizations.plan`; unknown/legacy plan strings pass through unchanged.
- `record_toll_event` is idempotent on `idempotency_key`; amounts derive from
  `TOLL_RATES`; cross-org reads impossible (RLS).
- `verify_checkout_session` returns session data even when the immediate
  subscription sync throws (logged, webhook covers it later).
- `Billing.jsx` shows exactly the two flat tiers with per-shop, unlimited-tech
  copy; no seat-count language remains.
- Full backend suite green; `ruff` clean; evidence report written.
