# TW-301 — Trade View real backend API — TDD evidence

Prepared by Twistor Holdings LLC.

## 1. Source

Ticket TW-301 (assignee: Zeke Okafor): expose the Trade View as a live
lbt-os HTTP API — pins, dossiers, diagnostics, layers served from real
pipeline output (customers, leads, leak-engine brief) instead of the
fictional sample dataset. Consent-gated: auth required, org-scoped,
cross-org ids degrade to 404. Signed-out visitors keep the
clearly-labeled sample experience (TW-295).

Branch: `feature/tw-301-tradeview-api` (lbt-os worktree). No twistor-core
changes were needed: lbt-os has no twistor-core dependency and the only
shared piece (keyless geocoding) is stdlib-only, so it was vendored with
attribution instead of adding a package dependency.

## 2. Task report

**Geocoder (vendored keyless: Photon → Nominatim, stdlib-only).**
RED: `ModuleNotFoundError: No module named 'httpx'` then
`ModuleNotFoundError: No module named 'jose'` — the lane's first runs
exposed that this shell lacked backend test deps; installed httpx +
`pip install -r requirements.txt` (environment only, no repo change).
Then RED proper: `ImportError: cannot import name 'tradeview' from
'app.routers'` on the new test module.
GREEN: `backend/app/services/geocode.py` (vendored from
twistor-core/src/twistor_core/territory_intel/geocode.py, TW-277, with
vendored-from attribution) + `GeocodeCache` (in-memory TTL; misses cached
shorter so bad addresses can recover).
Command: `python3 -m pytest tests/test_tradeview_api.py -q`
→ `16 passed` (4 geocode incl. cache-hit-avoids-network and
empty-address-never-touches-network).

**Trade View router (`GET /api/v1/tradeview/...`).**
RED: same collection ImportError above.
GREEN: `backend/app/routers/tradeview.py` — `/pins` (customers+leads,
geocoded, `source: "live"`, honest `unlocated_count`),
`/dossiers/{kind}/{id}` (facts + related leak findings + provenance),
`/diagnostics/{kind}/{id}` (server-generated snapshot + findings +
recommended next step), `/layers` (pins/storm/leak_map/actions with
live/coming status). Auth required on all four; cross-org ids 404;
per-row try/except so one bad row never kills the endpoint (TW-204);
geocode failure degrades to unlocated (never 500s).
Registered in `backend/app/main.py` as `/api/v1`.
Command: `python3 -m pytest tests/test_tradeview_api.py -q` → `16 passed`.

**Frontend live wiring.**
`frontend/src/lib/api.js`: new `tradeviewApi` (pins/dossier/diagnostics/
layers). `TradeView.jsx`: when signed in (`!isSampleMode()`), fetches
live pins; any failure falls back to the sample dataset — the map never
renders empty. Live dossier (facts + leak findings), live diagnostics
report, kind normalization (customer|lead → client|prospect, idempotent),
SQL sandbox generalized to `runSandboxQuery(sql, dataset)` (default keeps
old callers green). "Live data" badge replaces "Sample data" badge when
live; HUD note and unlocated-count note are honest.
Command: `npx vitest run src/pages/tradeview/` → `18 passed`
(13 pre-existing + 5 new live-mode tests). Full frontend suite:
`59 passed` across 9 files.

**Test-infra fix (test-only, zero production code).**
`backend/tests/conftest.py`: the TW-178 obviously-dummy env pattern at
conftest scope. This healed 6 test modules that could not even be
collected in a bare shell (test_analytics_events, test_demo_audit_limit,
test_integrations_platform, test_pilot_blockers, test_security_fixes,
test_stripe_checkout). Full backend suite went from 88 collectible to
`154 passed`.
Command: `python3 -m pytest tests/ -q` → `154 passed, 8 subtests passed`.

**Lint.** `ruff check app tests` → `All checks passed!` (CI's exact
invocation: `ruff check app tests` per .github/workflows/ci.yml).
`ruff format` applied to the four new/changed backend files.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Empty/blank addresses never touch the network | tests/test_tradeview_api.py::GeocodeTest::test_empty_address_returns_none_without_network | unit | PASS | `_get` mocked to raise; returned None |
| 2 | Cache serves the second lookup with 1 network call | ...::test_cache_hit_avoids_network | unit | PASS | `m.call_count == 1` |
| 3 | Pins come from real customers+leads with coordinates | ...::PinsEndpointTest::test_pins_come_from_real_data | integration | PASS | TestClient 200, `source: "live"`, lat/lng asserted |
| 4 | Address-less entities are counted, never pinned or dropped silently | ...::test_unlocated_entities_counted_honestly, test_geocode_failure_degrades_to_unlocated | integration | PASS | `unlocated_count` asserted; `pins == []` on total geocode failure |
| 5 | Endpoints require auth | ...::test_requires_auth | integration | PASS | 401/403 without token |
| 6 | Cross-org entity ids 404 (never leak) | ...::test_cross_org_entity_is_404 | integration | PASS | 404 |
| 7 | Dossier carries facts + related leak findings + provenance | ...::DossierEndpointTest | integration | PASS | 200, fields asserted |
| 8 | Diagnostics generated from real data | ...::DiagnosticsEndpointTest | integration | PASS | snapshot/findings/next-step asserted |
| 9 | Layers endpoint shape (pins/storm/leak_map/actions) | ...::LayersEndpointTest | integration | PASS | ids + live flags asserted |
| 10 | Signed-in map shows Live badge + live pins; backend failure falls back to sample | TradeViewLive.test.jsx | unit | PASS | `waitFor` Live badge; fallback asserts Sample badge + sample counts |
| 11 | Full backend suite green incl. healed modules | `python3 -m pytest tests/ -q` | CI | PASS | `154 passed, 8 subtests passed` |
| 12 | Full frontend suite green | `npx vitest run` | CI | PASS | `59 passed`, 9 files |
| 13 | Lint clean under CI's exact invocation | `ruff check app tests` | CI | PASS | `All checks passed!` |

## 4. Coverage and known gaps

- Geocoding hits free third-party APIs (Photon, Nominatim) at request
  time with a process-local TTL cache. No persistent cache yet: a backend
  restart re-warms. Follow-up if pin loads grow: persist lat/lng on the
  customer/lead row at import time.
- Dossier's `_findings_for_entity` runs a full leak scan per request.
  Fine at pilot scale; revisit with per-org scan caching if it shows up
  in latency.
- The 6 healed test modules now collect and pass locally; they were
  previously uncollectible in a bare shell (pre-existing env gap, not a
  code gap). CI (with real env vars) was unaffected.
- No twistor-core changes: the tradeview gateway/copilot stay library
  code; lbt-os consumes real pipeline output through its own leak engine
  and models. If dossier enrichment later needs the gateway's public-
  records adapters, that is a separate ticketed change.
- Node-side: `leaflet` was a declared dependency missing from
  node_modules (pre-existing); installed `--no-save` to run the suite.
  package.json/package-lock untouched.

## 5. Before/after proof (ADD MEANS USE)

BEFORE: Trade View pins came only from fictional `SAMPLE_COMPANIES`
hardcoded in the frontend bundle — no backend involvement.
AFTER (live, TestClient against the new router with a stubbed org):
`GET /api/v1/tradeview/pins` → 200 →
`[{"id": "c-1", "kind": "customer", "name": "Acme Heating",
"address": "123 Colfax Ave, Denver, CO", "located": true,
"lat": 39.74, "lng": -104.99, "geocode_source": "photon"}]`;
`GET /api/v1/tradeview/layers` → `['pins', 'storm', 'leak_map', 'actions']`.
The signed-in Trade View now renders the org's real customers and leads
as pins; signed-out visitors keep the TW-295 sample experience.

## 6. Plan-safety notes

No untrusted plan content was acted on. No destructive or credential-
handling instructions appeared in any plan or ticket text. No new network
calls beyond the two keyless geocoders the brief requires (Photon,
Nominatim) and the org's own API. No secrets, tokens, or PII in the diff.
The vendored geocoder carries its twistor-core source attribution.
