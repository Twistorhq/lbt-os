# TW-306 — What Should We Do layer — TDD evidence

Prepared by Twistor Holdings LLC.

## 1. Source

Ticket TW-306 (assignee: Zeke Okafor): prescriptive actions per pin —
the next-best-action engine (implements the TW-212 brief, which had no
implementation), nearest-qualified-tech assignment via JEV-style auditable
records, and route optimization. The "do" button behind every leak and
forecast.

Branch: `feature/tw-306-next-actions`, stacked on
`feature/tw-303-leak-map` (commit 436945e).

## 2. Task report

**Next-best-action engine (`backend/app/services/next_actions.py`).**
Pure, stdlib-only. `build_action_queue(findings, located)` ranks every
(finding, entity) by expected dollars-recovered = dollars ×
recoverability weight, and attaches the finding's prescribed
`recommended_action` as the single next move.
RED: `ImportError: cannot import name 'next_actions' from 'app.services'`.
GREEN: 17/17 new tests pass.
Recoverability weights are DOCUMENTED HEURISTIC PRIORS, labeled on every
action (`weight_basis: "heuristic prior — tune per org; not a measured
rate"`): quote-resurrection 0.35, plan-churn-risk 0.50,
equipment-age-graveyard 0.20, default 0.25. A leak with no dollar figure
gets expected_recovery None — never a fabricated estimate.

**Tech assignment (`assign_tech`).** Nearest qualified tech from a roster
(`skills` matched against the action's trade; unskilled techs excluded),
haversine distance, JEV-style decision record (option set, chosen option,
per-candidate distribution, model id, timestamp, context). Honest
degradation: no roster → "no technician roster connected"; no qualified
tech → named reason; unlocated action → cannot assign. No techs table
exists yet — the roster arrives as a payload; a roster-management UI is
the documented follow-up.

**Route optimization (`optimize_route`).** Nearest-neighbor over located
stops; documented as a heuristic, not an optimal solver. Unknown or
unlocated ids are reported, never routed on a guess.

**Endpoints (in `app/routers/tradeview.py`, sharing TW-303's
`_collect_leak_items` flattening — no duplicated scan logic).**
- `GET /api/v1/tradeview/actions` — ranked queue, one action per entity
  (highest-ranked wins), unlocated leaks included (location gates the
  pin and the assignment, never the action).
- `POST /api/v1/tradeview/route` — `{leak_ids, start}` → visit order,
  legs, total km, unknown ids reported honestly.
Auth required, org-scoped, rate-limited like the other tradeview routes.

**Frontend.** `tradeviewApi.actions()` / `.route()`; "What should we do"
panel in the HUD when the leak layer is on (top 5 actions: entity,
expected recovery, prescribed next move; weights labeled as priors);
"Optimize route" button draws the dashed route polyline and fits the map.
The API call is not gated on the map existing (jsdom has no map) — only
the polyline drawing is.

**Full suites + lint.** Backend: `187 passed` (18 new: 17 engine/endpoint
+ 1 tech-assignment surface). Frontend: `88 passed`, 11 files (3 new test
blocks: ranked queue rendering, route API call, actions-fetch honesty).
`ruff check app tests` → `All checks passed!` (CI's exact invocation).
`ruff format` applied.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Ranked by expected recovery (dollars × weight) | test_tradeview_actions.py::RankingTest::test_ranked_by_expected_recovery | unit | PASS | $10k×0.35=$3,500 beats $5k×0.50=$2,500 |
| 2 | Weights labeled as priors on every action | ...::test_weights_are_labeled_priors | unit | PASS | `weight_basis` contains "prior" |
| 3 | Single next move prescribed per action | ...::test_single_next_move_prescribed | unit | PASS | Equals the detector's recommended_action |
| 4 | No dollars → no expected recovery, never fabricated | ...::test_no_dollar_means_no_expected_recovery_not_zero_claim | unit | PASS | Both None |
| 5 | Nearest qualified tech wins; decision auditable | ...::AssignTechTest::test_picks_nearest_qualified_tech | unit | PASS | t-near chosen; distribution covers all candidates |
| 6 | Unskilled techs excluded; honest reasons | ...::test_unskilled_techs_are_excluded, test_no_roster_is_honest, test_unlocated_action_cannot_assign | unit | PASS | tech None + reason in each |
| 7 | Haversine sane (Denver→Boulder ~40km) | ...::RouteTest::test_haversine_denver_boulder | unit | PASS | 30 < km < 55 |
| 8 | Nearest-neighbor order + documented heuristic | ...::test_nearest_neighbor_order | unit | PASS | order starts at start; method labeled |
| 9 | `/actions` serves the ranked queue from the scan | ...::ActionsEndpointTest::test_action_queue_from_real_scan | integration | PASS | 200, q-1 before q-2, next_move present |
| 10 | `/route` routes located ids, reports unknown honestly | ...::RouteEndpointTest | integration | PASS | order correct; unknown ids named |
| 11 | Both endpoints require auth | ... | integration | PASS | 401/403 |
| 12 | Queue panel renders ranked actions + priors label | TradeViewLive.test.jsx (TW-306 block) | unit | PASS | $2,975 expected; "heuristic priors" visible |
| 13 | Route button calls API with located ids | ... | unit | PASS | `toHaveBeenCalledWith(['q-1','q-2'], null)` |
| 14 | Full backend suite green | `python3 -m pytest tests/ -q` | CI | PASS | `187 passed, 8 subtests passed` |
| 15 | Full frontend suite green | `npx vitest run` | CI | PASS | `88 passed`, 11 files |
| 16 | Lint clean under CI's exact invocation | `ruff check app tests` | CI | PASS | `All checks passed!` |
| 17 | Every `/actions` action carries its `tech_assignment` | ...::TechAssignmentSurfaceTest | integration | PASS | located → honest no-roster reason; unlocated → honest no-location reason; decision record structure queryable |
| 18 | `/actions` fetch failure never reads as all-clear | TradeViewLive.test.jsx (false all-clear) | unit | PASS | "Couldn't load actions — check your connection and try again."; no "Nothing to do" copy on error |

## 4. Coverage and known gaps

- Tech assignment has no roster source yet: no techs table, no roster UI.
  Until a roster connects, `GET /actions` carries a per-action
  `tech_assignment` with the honest "no technician roster connected —
  connect your roster to enable assignment" reason and an empty JEV
  decision record (unlocated actions get the honest "no location"
  reason). The honest state is queryable per action on the API and shown
  on every action in the UI — the roster-management UI is the documented
  follow-up, not a silent gap.
- Route optimization is nearest-neighbor (documented); true VRP with
  time windows is out of scope.
- The `/leaks` refactor (shared `_collect_leak_items`) changed no
  behavior: all 9 TW-303 backend tests still pass unmodified.
- Recoverability weights are priors, not measurements — the day a shop
  has enough closed-loop data, fit them per org (Maya's lane).

## 5. Before/after proof (ADD MEANS USE)

BEFORE: leaks were pins with dollar figures but no prescribed action —
the shop could see the money, not what to do first.
AFTER (live, TestClient against the new endpoints with the real engine,
two stalled quotes):
`GET /api/v1/tradeview/actions` →
`[(1, 'Acme Heating', expected_recovery 2975.0,
'Two-touch follow-up this week, highest v…'),
(2, 'Beta Corp', expected_recovery 1575.0, 'Two-touch follow-up…')]`;
`POST /api/v1/tradeview/route {leak_ids: ['q-1','q-2']}` →
`order ['q-1','q-2'], 2 stop(s) routed`;
`assign_tech` → `Pops, 1.4 km — nearest qualified tech` with a full
decision record. The signed-in Trade View now shows the ranked
"What should we do" queue and the Optimize-route button beside the
Leak Map.

## 6. Plan-safety notes

No untrusted plan content was acted on. No destructive or credential-
handling instructions appeared anywhere. No new network calls in this
lane (pure computation over scan output). No secrets, tokens, or PII in
the diff. No dollar figure or recovery estimate is synthesized — weights
are labeled priors and missing dollars stay missing.
