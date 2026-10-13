# TDD Evidence: TW-446 public overlay modules

**Prepared by Twistor Holdings LLC**

## 1. Source
- Ticket TW-446 (Justynn, 2026-10-09): real public-data overlays for the Trade View map — Denver permits + NWS weather, no API keys, framework-agnostic modules.
- Surge reassignment TW-470 (Justynn, 2026-10-12): Dre took the lead from Zeke.
- Branch: `dre/tw-446-public-overlays` (lbt-os repo).
- **Ground-truth correction (verified live 2026-10-12 before coding):** the ticket
  assumed Denver permits on Socrata. Denver retired its Socrata catalog (~2025)
  and moved to ArcGIS Hub. Built against the live FeatureServers
  (`ODC_DEV_RESIDENTIALCONSTPERMIT_P/316`, `ODC_DEV_COMMERCIALCONSTPERMIT_P/317`);
  newest rows dated 2026-10-09 at time of verification. NWS `api.weather.gov`
  verified live (points → stations → observations, alerts endpoint).

## 2. Task report
- **Overlay modules** (`frontend/src/overlays/` — http, permits, nws, index,
  README): written test-first.
  RED command: `npx vitest run src/overlays/` → collection failure
  (`Cannot find module './http.js'`, modules did not exist).
  GREEN after implementing: `27 passed (27)`.
  Guarantees: exact-host allowlist rejects lookalikes (`evilapi.weather.gov`,
  `api.weather.gov.evil.com`) and non-HTTPS; lat/lon boundary rejects NaN /
  Infinity / out-of-range / non-numbers before URL interpolation; 10s
  AbortController timeout (covers body read, TW-473); 2MB response cap;
  final URL re-validated after redirects (TW-472); upstream `stationId`
  encoded (TW-474); TTL cache with lazy eviction, failures never cached;
  `safeFetch` returns `{ok, data|error}` so one dead source never kills the map;
  permits without valid geometry (city-geocoded 0,0) dropped; malformed NWS
  features skipped per-item.
- **TradeView.jsx wiring:** "Building permits (live)" + "Current conditions
  (live)" toggles, Leaflet layer groups, popups with source attribution +
  "as of" timestamps, labeled fallbacks. Existing NWS alerts fetch migrated
  onto `nws.fetchAlerts` (normalized → GeoJSON at the boundary so
  `buildOpportunities` and the renderers are untouched).
  Verified via `npx vite build` (succeeds) + full suite below.
- **Full suite:** `npx vitest run` → `14 passed (14) / 112 passed (112)` at
  commit e32f4e3; `27 passed (27)` overlay tests after Rex hardening
  (commit 43550e4).

## 3. Bugs found while proving (fixed, tests still green)
1. Test regex asserted `point=39.74` — the fixture coordinate is 39.73**9**2;
   assertion bug, not code bug. Fixed the regex.
2. Test asserted `URLSearchParams` encodes spaces as `%20` — it emits `+`.
   Fixed the regex to `where=DATE_ISSUED+%3E%3D+`.
3. `fetchPermitsNear` test stub returned the same features for both ArcGIS
   layers → duplicate pins. Stub now differentiates by layer URL.
4. jsdom + fake-timers AbortController emits an unhandled rejection — timeout
   test rewritten with real timers (50ms).
5. `new Response()` mocks have empty `.url` — TW-472 re-check falls back to
   the validated request URL; redirect tests set `url` via defineProperty.
6. While fixing Rosa's nits: a `finally { clearTimeout }` placed right after
   `fetch()` would have killed the timer before `res.text()` (re-breaking
   TW-473). Restructured so the timer spans the body read on every path.

## 4. Reviews
- Rex (security, external-fetch surface): **APPROVE** — 3 low/info findings
  (TW-472/473/474), all fixed with regression tests, tickets closed.
- Rosa (diff review): round 1 REQUEST_CHANGES — 1 blocker (this report) +
  4 nits (unused `stormFetchedAt` state, `esc()` 4x duplication, TTL cache
  eviction, timer cleanup on redirect-throw path). All addressed in the
  follow-up commit; re-review requested on the new tip.

## 5. Provenance contract (for the UI consumers)
Every module result carries `source: { name, url }` and `fetchedAt` (ISO).
UI must label these numbers **real public data** with the "as of" timestamp.
`fetchPermitsNear` derivations are labeled with center + radius. No modeled
numbers are produced by these modules — anything derived downstream must
carry its own uncertainty label per Justynn's rule.
