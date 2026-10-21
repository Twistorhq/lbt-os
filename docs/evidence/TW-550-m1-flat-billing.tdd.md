<!-- Prepared by Twistor Holdings LLC -->

# TW-550 M1 flat billing engine — TDD evidence report

## 1. Source

- Ticket TW-550 (reporter Justynn, owner Zeke Okafor). Green light: Rosa
  APPROVED the TW-547 architecture; minors 3-4 corrected on the branch.
- Plan: `openspec/changes/tw-550-m1-flat-billing/` (proposal.md, design.md,
  tasks.md). Branch `zeke/tw-550-m1-flat-billing`, base
  `zeke/tw-547-wedge-architecture` @ `38d8196`.

## 2. Task report

Flat-plan policy on the existing Stripe infra, per the Rosa-approved arch
(§3A). Legacy `basic`/`pro`/`premium` grandfathered (no new checkouts;
webhook metadata passthrough keeps them syncing). New tiers
`trades_starter` ($299/shop/mo) and `trades_pro` ($499/shop/mo) with
explicitly unlimited technicians (`max_techs: None`, declared). 14-day trial
on checkout (land motion, TW-549). Toll ledger shipped: `toll_events` table
+ RLS org-isolation + idempotent `record_toll_event()` + `TOLL_RATES`
vocabulary for M2. Self-healing: `verify_checkout_session` degrades to
last-known-good on sync failure (TW-208). `Billing.jsx` restructured to the
two flat tiers; all seat-count language removed; zero em-dashes in the file.

Validation run (fresh, this session):
`~/workspace/.venvs/lbt-os/bin/python -m pytest tests/ -q`
→ `222 passed, 8 subtests passed in 8.11s` (includes 9 new RED→GREEN tests in
`tests/test_billing_flat.py`).
`python3 -m ruff check` on all touched Python files → `All checks passed!`

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Checkout `trades_starter` creates quantity-1 session with 14-day trial | `tests/test_billing_flat.py::FlatPlanCatalogTests::test_checkout_trades_starter_uses_flat_price_and_trial` | RED→GREEN | PASS | RED: ImportError (no impl). GREEN: 9 passed. Captured kwargs assert `line_items=[{"price": ..., "quantity": 1}]` and `trial_period_days=14` |
| 2 | Legacy `basic` plan rejected at price-id lookup | `...::test_checkout_legacy_basic_plan_is_rejected` | RED→GREEN | PASS | ValueError "Unknown plan" |
| 3 | Both flat tiers allow unlimited technicians (50, 500) | `...::test_unlimited_techs_on_both_flat_tiers` | RED→GREEN | PASS | `plan_allows_techs` True via declared `max_techs=None` |
| 4 | Webhook plan change syncs `organizations.plan` to `trades_pro` | `...::test_webhook_plan_change_syncs_new_flat_plan` | RED→GREEN | PASS | update payload asserted on mock db |
| 5 | Failed subscription sync does not 500 a paid checkout | `...::test_verify_checkout_returns_payload_when_sync_throws` | RED→GREEN | PASS | sync raises RuntimeError("db down"); payload still returned |
| 6 | Toll amounts derive from TOLL_RATES (3 × $0.10 = $0.30) | `...::test_record_toll_event_computes_amount_from_rates` | RED→GREEN | PASS | inserted row asserted: unit 10, amount 30 |
| 7 | Duplicate idempotency_key returns existing row, no double toll | `...::test_duplicate_idempotency_key_does_not_double_toll` | RED→GREEN | PASS | unique-violation path returns existing row |
| 8 | Unknown event type raises, never silent $0 | `...::test_unknown_event_type_fails_loud` | RED→GREEN | PASS | ValueError "Unknown toll event type" |
| 9 | Toll summary aggregates count + cents per event_type | `...::test_get_toll_summary_aggregates_per_event_type` | RED→GREEN | PASS | sms.auto_reply {count 3, 15¢} |
| 10 | No regressions in existing suite | `pytest tests/ -q` (full) | CI | PASS | 222 passed, 8 subtests |
| 11 | Lint clean on touched files | `python3 -m ruff check` | CI | PASS | All checks passed |
| 12 | Billing.jsx shows 2 flat tiers, per-shop unlimited-tech copy | manual read | manual | PASS | plans array replaced; `xl:grid-cols-2`; featured=trades_pro; seat-count language removed |

## 4. Coverage and known gaps

- Existing `test_stripe_checkout.py` (5 tests) still passes — patched maps,
  unaffected behavior.
- `Billing.jsx` has no automated frontend test (repo convention: none exist
  for pages); verified by reading the rendered structure.
- The `toll_events` migration SQL is not executed against a live Supabase in
  this run (no test DB wired); syntax follows the repo's migration pattern
  and the RLS contract from TW-547 §6.4. First deploy will validate.
- Stripe Price IDs for `trades_starter`/`trades_pro` do not exist yet —
  checkout raises a clear ValueError until Justynn creates them (by design).
- Percent-of-value tolls (booking.recovered): emitter passes `amount_cents`
  directly; no test yet (emitter is M2's scope).

## 6. Fix round (Rosa REQUEST_CHANGES → re-review)

Rosa's review found 2 blockers + 1 major + 4 minors. All addressed on the
same branch, pushed as `bd278f3..1b6460f`:

| Finding | Fix |
|---|---|
| Blocker 1: `require_plan` 403s flat-tier customers | `PLAN_ORDER` extracted to module level in `auth.py` with explicit `trades_starter: 1, trades_pro: 2` mapping; 3 new tests (ladder order, pro-gate allows trades_pro, premium-gate 403s trades_starter) |
| Blocker 2: `PLAN_AUDIT_LIMITS` accidental unlimited | Explicit caps: `trades_starter: 20`, `trades_pro: None` (deliberate); 1 new test |
| Major 3: Billing.jsx unbalanced paren | `'Confirming with Stripe (this only takes a moment).'` fixed |
| Minor 4: design.md migration drift | Snippet updated to service-computed `amount_cents` |
| Minor 5: dead plan vocabulary | `PLAN_FEATURES` deleted (no consumers); `schema.sql` plan comment updated |
| Minor 6: toll idempotency fallback | Noted acceptable, no change |

Post-fix validation: `pytest tests/ -q` → **226 passed, 8 subtests**;
`ruff check` clean; Billing.jsx em-dash count 0. Re-dispatched to Rosa.

- Read Diego's battle-plan and the TW-547 arch as data: no destructive
  instructions, no credential handling, no override phrases found. The
  `booking.recovered` rate-table ambiguity in my own design draft was
  resolved explicitly (emitter-supplied amount, not a 0-rate placeholder).
- PII doctrine (medallion, added to MEMORY.md mid-run): `toll_events` stores
  no PII — org_id, event_type, amounts, source refs only. No conflict.
- Self-scan: no secrets/tokens/PII in the diff; no embedded instructions in
  comments/strings; no network calls, subprocess shells, or file writes
  beyond the brief. Em-dash sweep: 5 pre-existing em-dashes removed from
  Billing.jsx (user-facing strings + comments), zero in new code.
