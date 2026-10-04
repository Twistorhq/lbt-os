# TW-303 — Leak Map (money on the table) — TDD evidence

Prepared by Twistor Holdings LLC.

## 1. Source

Ticket TW-303 (assignee: Zeke Okafor): render every missed follow-up,
stale quote, and at-risk customer on the Trade View map as a glowing leak
WITH a dollar figure. Builds on the leak-engine API (TW-202) and the
TW-301 real-backend router. Internal edition gets the full layer; the
client-safe `LeakExecutiveSummary` (no SQL, no code) is the piece the
client edition consumes.

Branch: `feature/tw-303-leak-map`, stacked on `feature/tw-301-tradeview-api`
(commit 7ba7233).

## 2. Task report

**Lead address migration + models.**
Leads had no address field, so stalled quotes (the money) could never be
pinned. Added `supabase/migration_lead_address.sql`
(`ALTER TABLE leads ADD COLUMN IF NOT EXISTS address TEXT`) and the
`address` field to `LeadCreate`/`LeadUpdate`/`LeadOut` (nullable — old
rows unaffected; `.get("address")` degrades gracefully when the column
is absent).

**Leaks endpoint (`GET /api/v1/tradeview/leaks`).**
RED: `404` / `KeyError: 'leaks'` on the new test module.
GREEN: runs the real leak-engine scan, resolves each finding's entities
to addresses across customers/leads/quotes (id lookup, then customer-name
fallback), geocodes via the TW-301 cached geocoder, and returns one pin
per entity: detector, title, severity, per-entity dollars, days_idle,
recommended_action, coordinates. Rules enforced in code:
- dedupe: one pin per entity, highest-dollar wins;
- unlocatable entities are counted in `totals.unlocated`, never pinned
  on a guess and never dropped silently;
- dollar figures come ONLY from detectors (`total`/`estimated_value`);
  `_dollars()` coerces garbage to None — a missing figure is honest, a
  fabricated one is not;
- scan failure degrades to an honest empty headline, never a 500 of lies;
- per-row try/except (TW-204): one bad entity never kills the endpoint.
Headline: `"$8,500 left on the table"` (or the honest
`"No leaks detected — your follow-up game is tight."`).
Command: `python3 -m pytest tests/test_tradeview_leaks.py -q` → `9 passed`
(8 endpoint incl. a full end-to-end with the REAL engine: a 20-day-old
unfollowed $8,500 quote becomes a pinned leak; plus the Lead model test).

**Frontend Leak Map layer.**
`tradeviewApi.leaks()`; layer toggle in the Insight-layers HUD (disabled
in sample mode with an explanatory title); glowing leak pins
(`.tv-leak-pin`, severity-colored, dollar figure on the pin, `LEAK` when
no figure); click popup with title, dollars, recommended action;
headline banner (`$X left on the table`) floating over the map;
`LeakExecutiveSummary` in the HUD — plain-language totals + top 5 leaks,
no SQL, no code (client-safe).
Command: `npx vitest run src/pages/tradeview/` → `22 passed`
(13 pre-existing + 9 new incl. toggle-fetches-banner, sample-mode
disabled, summary-has-no-SQL).

**Full suites + lint.** Backend: `154 → 163 passed`
(9 new). Frontend: `59 → 67 passed`, 9 files (4 new test files; 22 in
the tradeview dir) — the original draft said "63 passed"; Rosa's
independent run measured 67/9 and that is the corrected figure.
Post-rebase onto main (TW-301 + TW-304/309 merged): frontend full suite
`85 passed`, 11 files. `ruff check app tests` → `All checks passed!`
(CI's exact invocation).

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Leak pins come from the real leak-engine scan | test_tradeview_leaks.py::LeaksIntegrationTest::test_real_engine_stalled_quotes_become_pins | integration | PASS | Real `run_leak_scan`; 20-day-old $8,500 quote → pin with $8,500 |
| 2 | Headline carries the real dollar total | ...::LeaksEndpointTest::test_headline_totals | integration | PASS | `"$13,000 left on the table"` asserted |
| 3 | Unlocatable entities counted, never pinned/dropped | ...::test_unlocated_entities_counted_not_dropped | integration | PASS | `unlocated >= 1`, pin absent |
| 4 | One pin per entity, highest dollar wins | ...::test_dedupe_keeps_highest_dollar_pin | integration | PASS | 1 pin, $8,500 not $2,000 |
| 5 | No dollar figure is ever fabricated | ...::test_no_dollar_figure_is_never_fabricated | integration | PASS | `dollars is None` asserted |
| 6 | Empty scan is honest | ...::test_empty_scan_is_honest | integration | PASS | `leaks == []`, truthful headline |
| 7 | Endpoint requires auth | ...::test_requires_auth | integration | PASS | 401/403 |
| 8 | Lead models accept address; missing stays None | ...::LeadAddressModelTest | unit | PASS | Create/Update accept; absent → None |
| 9 | Layer toggle fetches leaks, banner + summary render | TradeViewLive.test.jsx (TW-303 block) | unit | PASS | `waitFor` banner; summary assertions |
| 10 | Layer disabled in sample mode | ... | unit | PASS | `toBeDisabled()` asserted |
| 11 | Executive summary has no SQL/code | ... | unit | PASS | `not.toMatch(/SELECT/i)` |
| 12 | Full backend suite green | `python3 -m pytest tests/ -q` | CI | PASS | `163 passed, 8 subtests passed` |
| 13 | Full frontend suite green | `npx vitest run` | CI | PASS | `67 passed`, 9 files (85/11 post-rebase) |
| 14 | Lint clean under CI's exact invocation | `ruff check app tests` | CI | PASS | `All checks passed!` |

## 4. Coverage and known gaps

- Pins need addresses: customers have them; leads/quotes get them from
  the new column or the customer-name fallback. Entities that still
  cannot be located appear in `totals.unlocated` and in the summary note
  ("N need an address to pin") — the shop's data gap, stated plainly.
- Findings without per-entity dollar figures pin without a figure
  (`LEAK` badge) — the detector didn't provide one, and we don't invent
  one. The headline totals only sum real estimates.
- Geocoding is per-request with the TW-301 process cache; the persist-
  lat/lng follow-up from TW-301 applies here too.
- The client edition page does not exist in this repo yet; the
  `LeakExecutiveSummary` component is built to be dropped into it
  unchanged when it does.

## 5. Before/after proof (ADD MEANS USE)

BEFORE: the Trade View's insight layers were disabled checkboxes
("Not assessed in sample data"); leak findings lived only as text in the
morning brief — no map presence, no dollar figure on screen.
AFTER (live, TestClient against the new endpoint with the real engine):
`GET /api/v1/tradeview/leaks` → 200 →
headline `"$8,500 left on the table"`,
`totals: {"findings": 1, "dollars_at_stake": 8500.0, "located": 1,
"unlocated": 0, "partial": false}`,
pin `{"id": "quote-resurrection:q-1", "entity_name": "Acme Heating",
"dollars": 8500.0, "lat": 39.74, "lng": -104.99, ...}`.
Signed-in Trade View: toggling "Leak Map — money on the table" renders
glowing dollar pins, the headline banner, and the executive summary.

## 6. Plan-safety notes

No untrusted plan content was acted on. No destructive or credential-
handling instructions appeared anywhere. No new network calls beyond the
TW-301 keyless geocoders. No secrets, tokens, or PII in the diff. No
dollar figure is synthesized anywhere in the lane — `_dollars()` maps
every hostile value to None.
