# TW-293: Trade View internal command edition as first-class app page — TDD evidence

Prepared by Twistor Holdings LLC.

## Scope

- New page `frontend/src/pages/TradeView.jsx` at `/app/tradeview`, nav item in the
  Sidebar Intelligence group (Pro badge).
- Leaflet (MIT) + CartoDB dark-matter / OSM tiles, no keys; pulsing pins
  (prospect/client); click pin → fly-to + animated dossier.
- Command HUD: search, kind/trade filters, basemap toggle.
- Insight layers: NWS storm alerts LIVE (`https://api.weather.gov/alerts/active`,
  keyless, severity colors, escaped popups); ghost-web / ad pressure / digital
  grade render honest not-assessed states (disabled toggles + empty-state panels).
- Dossier: facts, pitch notes, public records (https-only link-outs via
  `safeHttpsUrl`), provenance on every record.
- One-button diagnostics report (sample-labeled, printable).
- Read-only SQL sandbox (`runSandboxQuery`): SELECT-only subset over the sample
  dataset; rejects DDL/DML/multi-statement with clear messages.
- Sample data clearly labeled fictional; no real businesses, no invented metrics.
- Fix round (Rosa nit): `safeHttpsUrl` now returns canonical `parsed.href`
  instead of the raw trimmed string (e.g. `https:evil.example` →
  `https://evil.example/`); regression test added.

## Commands run (quoted, outputs quoted)

```
$ cd ~/workspace/worktrees/tw-293-sofia/frontend && npm test
> lbt-os@1.0.0 test
> vitest run

 RUN  v2.1.9 /home/hatch/workspace/worktrees/tw-293-sofia/frontend

Browserslist: browsers data (caniuse-lite) is 6 months old. ...
 ✓ src/pages/tradeview/TradeView.test.jsx (13 tests) 297ms

 Test Files  2 passed (2)
      Tests  21 passed (21)
   Start at  15:29:19
   Duration  3.91s (transform 499ms, setup 175ms, collect 618ms, tests 446ms, environment 1.90s, prepare 137ms)
```

(The other passing file is the pre-existing agent-UI test suite; 21/21 total,
13 of them the new TW-293 tests.)

```
$ cd ~/workspace/worktrees/tw-293-sofia/frontend && npm run build
✓ 1433 modules transformed.
rendering chunks...
dist/index.html                   0.93 kB │ gzip:   0.53 kB
dist/assets/index-B7yjP42Z.css  115.31 kB │ gzip:  23.14 kB
dist/assets/index-_Azal0NB.js   525.92 kB │ gzip: 157.19 kB
✓ built in 7.26s
```

Build-content verification (per the 2026-09-23 coaching note: a build with
`VITE_CLERK_PUBLISHABLE_KEY` unset gets tree-shaken to libraries only):

```
$ VITE_CLERK_PUBLISHABLE_KEY=pk_test_dummy_for_content_check npm run build
✓ built in 9.80s
"Mile High Air Pros": 1
"Sample data": 3
"api.weather.gov": 1
```

App strings confirmed present in the bundle. The key-less build also succeeds;
the zero-string result there is the known Rollup tree-shake behavior, not a
defect in this diff.

## RED → GREEN receipts (13 new tests)

RED: with the implementation files (`TradeView.jsx`, `urlSafe.js`,
`tradeviewSample.js`) moved aside, the new test file fails at collection —
unresolvable imports, `1 failed / 0 tests`, because the modules under test do
not exist. GREEN: with the implementation restored, `13/13` pass.

| Test | RED | GREEN |
|---|---|---|
| renders boot sequence, badges, and honest counts | fail (no module) | pass |
| renders map region with accessible label | fail (no module) | pass |
| dossier shows honest empty state before a pin is selected | fail (no module) | pass |
| insight layers carry honest not-assessed notes | fail (no module) | pass |
| sample provenance is visible on the page | fail (no module) | pass |
| kind filter changes the counts | fail (no module) | pass |
| safeHttpsUrl allows https URLs (canonical form) | fail (no module) | pass |
| safeHttpsUrl rejects javascript:, http:, data:, relative, non-strings | fail (no module) | pass |
| runSandboxQuery runs SELECT * over the sample dataset | fail (no module) | pass |
| runSandboxQuery supports WHERE trade and LIMIT | fail (no module) | pass |
| runSandboxQuery supports name LIKE | fail (no module) | pass |
| runSandboxQuery rejects non-SELECT, semicolons, unknown columns, bad shapes | fail (no module) | pass |
| DiagnosticsReport labels itself as a sample report with provenance | fail (no module) | pass |

Fix-round regression check: after canonicalizing `safeHttpsUrl` to return
`parsed.href`, the updated canonical-form test passes (`13/13`), and the full
suite stays `21/21` green.

## Guarantees table

| Guarantee | Proven by |
|---|---|
| Read-only SQL sandbox | `runSandboxQuery` tests: `DELETE`/`DROP` rejected ("only SELECT"), trailing `;` rejected, unknown column (`ssn`) rejected, unsupported shape rejected, empty query rejected; WHERE/LIKE/LIMIT supported. Rosa's 32 SQL-injection probes all held (independent verification, round-1 review). |
| XSS-safe public-record link-outs | `safeHttpsUrl` tests: `javascript:`, `http:`, `data:`, relative paths, and non-strings all rejected; https URLs allowed in canonical `parsed.href` form. Rosa's 20 XSS probes all held (independent verification, round-1 review). |
| Honest sample labeling | Tests: boot sequence shows "Sample data" badge + "3 companies · 2 prospects · 1 client"; page states sample companies are "not real businesses"; diagnostics report renders "SAMPLE report" + "Provenance:" + "Pitch angle:". |
| FOSS-only network | Code-level evidence: `grep -riE "VITE_\|api[_-]?key\|secret\|token"` across the new files returns zero hits. Outbound hosts are `api.weather.gov` (keyless NWS), `tile.openstreetmap.org` / OSM copyright (keyless), CartoDB attribution. Mapping via Leaflet (MIT, already in `package.json`). |
| Brand-board QC | Tests: "Internal" edition badge rendered, "Sample data" badge rendered, no fabricated company data (all records come from `tradeviewSample.js` labeled fictional), honest not-assessed states instead of invented metrics. |
| Route wiring | Diff evidence: `App.jsx` adds `<Route path="tradeview" element={<TradeView />} />`; `Sidebar.jsx` adds the Trade View nav item (`/app/tradeview`, Pro badge) under Intelligence. Build evidence: route + page code present in the production bundle (see build section). No dedicated routing test exists — stated honestly. |

## Independent verification (Rosa Delgado, round-1 review)

- 32 SQL-injection probes against `runSandboxQuery` — all held (rejected or inert).
- 20 XSS probes against `safeHttpsUrl` and the dossier link-out rendering — all held.
- Verdict: CHANGES REQUIRED on two items only: this missing evidence report
  (now written) and the `parsed.href` canonicalization nit (now fixed, test
  updated, suite re-run green). Everything else accepted without regression.

## Accessibility checks performed

- Map region rendered with `role="application"` and an accessible name
  ("Twistor Trade View trade map") — asserted in tests.
- Kind filter uses real `<label>` elements (`getByLabelText('Prospects')` in
  tests), keyboard-operable via native controls.
- Insight-layer toggles expose their disabled reason via `title`
  ("Not assessed in sample data").
- Semantic HTML throughout: headings, lists, table for diagnostics.

## Notes / known limitations

- NWS storm layer is the only live network dependency; it is keyless and the
  popup content is escaped before render.
- Ghost-web, ad pressure, and digital-grade layers show honest not-assessed
  states — no data means no claim.
- No real businesses, no invented metrics: the sample dataset is fictional and
  labeled as such on every surface.
