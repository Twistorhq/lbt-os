# TW-606 evidence report — dynamic volume pricing curve for the science paywall

**Prepared by Twistor Holdings LLC**

Ticket: TW-606 · Branch: `dre/tw-606-dynamic-pricing` · Author: Dre Coleman
Reviewer: Rosa Delgado (REQUEST_CHANGES, 2026-10-18 — fixes in progress) · Date: 2026-10-18

## Source

Ticket TW-606 (reporter: Justynn, 2026-10-18): replace the flat $6.00/$8.50/$22.50
Excel tiers in the shared science paywall with a dynamic volume curve —
price(d) = 0.01 + 59.98*(d/100000)^1.5, capped $59.99 — with equivalent API
micro-tolls and curve-priced rate-limit tiers. Two refinements folded in:
(1) asset-value floors — each dataset's floor set by its value tier at launch
(standard $0.01, premium higher); (2) customer tiers gated behind a
signup/purchase question — student $5.99 entry, researcher $10.99, other $10.99 —
then identical per-request tolling at the dataset's live curve price.
Subscription gate stays required on top of per-download fees.

## RED / GREEN history

- **GREEN (this round)**: `backend/app/data_layer/science/paywall.py` extended;
  carbon/subseasonal/crop_climate routers rewired; tests rewritten/added.
  `pytest tests/test_science_paywall.py tests/test_carbon.py
  tests/test_subseasonal.py tests/test_turbulence.py -q` → **127 passed**.
- Two test-fake gaps surfaced and were fixed in the test doubles (not by
  weakening assertions): the paywall fake's query builder lacked
  `.order()`/`.limit()`, and its `.select()` didn't accept the
  `count="exact", head=True` kwargs the new server-side count uses.
- **Full suite**: 367 passed; 20 failed + 7 collection errors are
  pre-existing on the clean tree (verified via `git stash` — missing
  `sniffio` sandbox dep; modules: bi_whitelabel, missed_call, pilot_blockers,
  tradeview/analytics/billing/security collection).
- **Lint**: `ruff check` clean on all 7 touched files; `ruff format --check`
  clean on paywall.py (3 router files carry pre-existing repo-wide format
  drift, untouched).

## Behavior (verified by tests + review)

- Curve: 1¢ at 0 downloads → 5999¢ at 100,000 → hard cap beyond; negative
  counts clamp to floor; exponent (1.5), cap ($59.99), and scale (100k) are
  keyword parameters. `ValueError` on nonsense config (non-positive scale,
  negative exponent).
- Asset-value floors: standard $0.01 / premium $9.99 / flagship $29.99 via
  `price_floor_cents` on `sci_dataset_versions`; unknown tiers degrade to
  the standard default.
- Customer tiers: `CUSTOMER_TIERS` config (student 599¢ / researcher 1099¢ /
  other 1099¢); `organizations.customer_tier` defaults to 'other'; unknown
  values degrade to default. Subscription gate unchanged.
- API tolls: per-row rate = live curve price / total rows; a full-dataset
  API pull converges to the curve price. Server-side `count="exact"` for the
  download ledger (no row fetch on the hot path); resolved version row is
  threaded through (no redundant re-lookup). Legacy flat query toll fallback
  when no version row resolves.
- Rate-limit tiers: standard/pro/scale at 1x/5x/20x the live curve price.
  **Pricing schedule defined only — billing + enforcement NOT wired**
  (marked in code; follow-up ticket needed before presenting as live).
- TW-208: every new DB lookup is try/except with graceful defaults; all
  emit paths swallow and continue. Degradation direction is undercharge
  (falls to floor on lookup failure).

## Open design point (needs Justynn's call — Rosa Blocker 1)

The charged Excel download fee is currently the tiered entry fee
($5.99/$10.99), while API tolls price off the curve. At d=0 a full API pull
costs ~1¢ vs a $10.99 download (~1100x gap); at d=100k it's 5999¢ vs $10.99
(5.5x the other way). Options: (a) download fee = curve price with the
tiered entry fee charged separately, or (b) keep as-is and correct every
neutrality claim. Code comments flag this explicitly; docstrings now
describe actual behavior either way.

## Migration

`supabase/migration_sci_paywall_tw606.sql` — idempotent
`ADD COLUMN IF NOT EXISTS` for `sci_dataset_versions.price_floor_cents`,
`organizations.customer_tier`, `organizations.rate_limit_tier`; `NOT NULL
DEFAULT` safe for existing rows. Cumulative download counts are derived from
the toll ledger at runtime (not in the migration). **Not run against any
live DB.**
