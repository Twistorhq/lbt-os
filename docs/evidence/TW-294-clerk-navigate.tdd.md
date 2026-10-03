# TW-294: wire React Router navigate into ClerkProvider — TDD evidence

Prepared by Twistor Holdings LLC.

## Scope

- Bug: after sign-in on the GitHub Pages deployment, Clerk fell back to
  `window.location` with the raw `/onboarding` path, dropping the `/lbt-os`
  basename and landing on the Pages 404 page. Root cause: `ClerkProvider` was
  rendered with no `navigate` prop and sat OUTSIDE the React Router, so
  Clerk's post-sign-in redirect could not go through client-side routing.
- Fix (4 files, +115/−16):
  - `frontend/src/auth/ClerkProviderWithNavigate.jsx` (new): wraps
    `ClerkProvider` and passes `navigate={(to) => navigate(to)}` from
    `useNavigate()`; keeps Clerk's URL props (`signInUrl="/sign-in"`,
    `signUpUrl="/sign-up"`, `fallbackRedirectUrl="/onboarding"`).
  - `frontend/src/main.jsx`: `BrowserRouter` (basename from `BASE_URL`,
    future flags `v7_startTransition` + `v7_relativeSplatPath` verbatim)
    now wraps `ClerkProviderWithNavigate`; the Clerk props moved here
    from the old inline `ClerkProvider`.
  - `frontend/src/App.jsx`: renders the route tree inside the Router
    (previously rendered `<BrowserRouter>` itself — moved, not duplicated).
  - `frontend/src/auth/ClerkProviderWithNavigate.test.jsx` (new): 3 tests.

## Commands run (quoted, outputs quoted)

```
$ cd ~/workspace/worktrees/tw-294-sofia/frontend && npx vitest run

 RUN  v2.1.9 /home/hatch/workspace/worktrees/tw-294-sofia/frontend

Browserslist: browsers data (caniuse-lite) is 6 months old. ...
 ✓ src/pages/tradeview/TradeView.test.jsx (13 tests) 252ms
 ✓ src/auth/ClerkProviderWithNavigate.test.jsx (3 tests) 32ms

 Test Files  3 passed (3)
      Tests  24 passed (24)
   Start at  17:27:04
   Duration  6.29s (transform 680ms, setup 326ms, collect 1.11s, tests 438ms, environment 1.90s, prepare 137ms)
```

(The third passing file is the pre-existing agent-UI test suite; 24/24 total,
3 of them the new TW-294 tests.)

Production build, exactly as the deploy workflow invokes it (base `/lbt-os/`):

```
$ cd ~/workspace/worktrees/tw-294-sofia/frontend && VITE_CLERK_PUBLISHABLE_KEY=pk_test_dummy_for_content_check npx vite build -- --base=/lbt-os/
dist/assets/index-B7yjP42Z.css    115.31 kB │ gzip:  23.14 kB
dist/assets/index-ybYp0e2_.js   1,396.69 kB │ gzip: 389.95 kB

(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rollupOptions.output.manualChunks to improve chunking: https://rollupjs.org/configuration-options/#output-manualchunks
- Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
✓ built in 9.19s
```

Build-content verification (per the 2026-09-23 coaching note: a build with
`VITE_CLERK_PUBLISHABLE_KEY` unset gets tree-shaken to libraries only, so the
key is set to a dummy value for content assertions):

```
$ bundle=dist/assets/index-ybYp0e2_.js
bundle=dist/assets/index-ybYp0e2_.js size=1397397 bytes
"Twistor Trades": 1
"tradeview": 2
```

Healthy 1.4MB bundle with app strings present — not the degenerate
tree-shaken bundle. (`dist/` is gitignored; build artifacts are not in the
diff.)

## RED → GREEN receipts (3 new tests)

RED: with the implementation file (`ClerkProviderWithNavigate.jsx`) moved
aside, the new test file fails at collection — unresolvable import, `1 failed
/ 0 tests`, because the module under test does not exist. GREEN: with the
implementation restored, `3/3` pass.

| Test | RED | GREEN |
|---|---|---|
| passes a navigate function to ClerkProvider | fail (no module) | pass |
| Clerk redirect navigates client-side (no full-page load, basename preserved) | fail (no module) | pass |
| keeps the Clerk URL props Clerk needs for auth routing | fail (no module) | pass |

## Guarantees table

| Guarantee | Proven by |
|---|---|
| Basename-preserving redirect | Test 2 renders inside `MemoryRouter basename="/lbt-os"` at `/lbt-os/sign-in`, calls the captured `navigate('/onboarding')`, and asserts the Router location becomes `/onboarding` while `window.location.pathname` is untouched — the redirect stays client-side and basename-aware instead of full-page-loading to `/onboarding`. |
| No full-page load post-sign-in | Test 2's `window.location.pathname` assertion: `navigate()` never touches `window.location` (the old Clerk fallback path did). Test 1 asserts the `navigate` prop Clerk receives is a function, so Clerk never falls back to `window.location`. |
| Router moved verbatim | Diff evidence: `main.jsx` renders `<BrowserRouter basename={basename} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>`; `App.jsx` no longer renders its own `<BrowserRouter>` — moved up, not duplicated. `grep -rn "BrowserRouter"` across `frontend/src` shows exactly one occurrence (in `main.jsx`). |
| Clerk props preserved | Test 3 asserts `publishableKey`, `signInUrl="/sign-in"`, `signUpUrl="/sign-up"`, `fallbackRedirectUrl="/onboarding"` are passed through to `ClerkProvider` unchanged. Diff evidence: the same four values the old inline `ClerkProvider` in `main.jsx` carried. |
| No route definitions changed | Diff evidence: the commit touches `main.jsx`, `App.jsx`, `ClerkProviderWithNavigate.jsx`, and its test only — no `<Route>` added, removed, or repathed. Full suite 24/24 green, including the 13 TW-293 TradeView tests, so no route regressed. |

## Independent verification (Rosa Delgado, round-1 review)

Per Rosa's round-1 review of this branch: she ran her own vitest suite
(24/24 green), her own production build, and confirmed the bug mechanism
(Clerk falling back to `window.location` without the `navigate` prop, dropping
the basename on GitHub Pages). Verdict: CHANGES REQUIRED on this missing
evidence report only — the code itself was accepted as-is.

## Accessibility checks performed

- No visual or interactive UI changed in this diff: the fix is auth-routing
  plumbing only. No new focusable elements, no copy, no motion.
- `App.jsx` route tree renders inside the same Router context as before;
  keyboard/ARIA behavior of existing routes is untouched (full suite green).

## Notes / known limitations

- `dist/` is gitignored; the production build above was run locally to match
  the deploy workflow's exact invocation (`vite build -- --base=/lbt-os/`).
- The chunk-size warning (>500 kB) is pre-existing (Leaflet bulk) and also
  appears on main; not introduced by this diff.
- React Router future-flag warnings in the test output are informational; the
  flags are already opted into in `main.jsx`.
