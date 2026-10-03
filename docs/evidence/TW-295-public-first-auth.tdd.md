<!-- Prepared by Twistor Holdings LLC -->

# TDD Evidence Report — TW-295: Public-first auth (browse the app, sign in on click)

## 1. Source

- **Ticket:** TW-295 (reporter: Justynn, 2026-10-03) — "I want sign in to be after you see the app."
- **Branch:** `feature/tw-295-public-first-auth` off `origin/main` @ `0e391ef` (TW-294 merge).
  Worktree `~/workspace/worktrees/tw-295-sofia`. Nothing merged; main untouched.
- **Scope:**
  1. Remove `RequireAuth` as the hard gate on `/app/*` — unauthenticated visitors browse the app shell on clearly-labeled fictional sample data.
  2. Auth triggers only on explicit click (nav Sign in, Get started CTAs, gated actions). Gated-action click while signed out → `signup_intent_click` + bounce to `/sign-in`, then post-auth return (first-timers → `/onboarding`, as today).
  3. Funnel instrumentation through the existing TW-209 visitor-events pipeline (`lib/analytics.js` `trackVisitorEvent` → `POST /api/v1/visitor-events`): `app_view`, `signup_intent_click`, `signup_completed`. No parallel pipeline.
  4. Keep the TW-294 Clerk `navigate` fix working (all auth navigation client-side under `/lbt-os`).

## 2. Design

- **Sample-data layer** (`lib/sampleMode.js` + `lib/sampleData.js`): an axios request interceptor, installed once on both axios instances in `lib/api.js`, active only when `setSampleMode(true)` (Clerk `isLoaded && !isSignedIn`, synced by `SampleModeSync` in App.jsx).
  - GETs: first-match against `SAMPLE_GETS` (`[exactPath | RegExp, payload]`) → resolve 200 with the fictional payload. Unmapped GETs pass through untouched.
  - Writes (POST/PATCH/PUT/DELETE): rejected with `code: SAMPLE_WRITE_BLOCKED`; a registered handler fires (app routes to `/sign-in` with post-auth return); `signup_intent_click{element: 'gated_write'}` tracked. Funnel events use raw `fetch` (`lib/analytics.js`), so they are never blocked.
  - Mode flips invalidate the react-query cache and clear the auth token, so sample rows never leak into signed-in sessions.
- **Fictional data contract:** every record uses 555 phones / `.example` emails; payloads carry `is_demo: true` (the backend's existing convention — RevenueIntelligence's `DemoBanner` and BriefScene's `is_demo` badge light up automatically).
- **Gated actions** (`hooks/useGatedAction.js`): wraps real-data actions; signed-out click → intent event + `sessionStorage[lbt_post_auth_return]` + `navigate('/sign-in')`. Wired into Connections "Connect {provider}". All other writes are caught by the interceptor.
- **Post-auth landing** (`pages/AuthCallback.jsx`, route `/auth-callback`, now the Clerk `fallbackRedirectUrl`): new users (Clerk `createdAt` within 5 min) → `/onboarding` + `signup_completed`; returning users → stored return path; no stored path → `/onboarding` (today's behavior preserved). Idempotency ref guard against double-navigation (StrictMode-safe).
- **Chrome:** `SampleBanner` in `Layout` for visitors (role=region, keyboard-reachable CTA, visible focus, no motion); `Sidebar` shows a Sign in CTA when signed out (tracked), `UserButton` when signed in.
- **FOSS:** zero new dependencies (react, react-router, @clerk/clerk-react, @tanstack/react-query only).

## 3. RED → GREEN receipts

RED (implementation files moved aside, tests run):
```
Test Files  5 failed (5)
     Tests  no tests
```
All 5 new suites fail at import — the modules under test do not exist.

GREEN (files restored):
```
Test Files  8 passed (8)
     Tests  54 passed (54)
```
24 pre-existing + 30 new. Full command: `npx vitest run` in `frontend/`.

Production build (exactly as the deploy workflow invokes it):
```
VITE_CLERK_PUBLISHABLE_KEY=pk_test_dummy npx vite build -- --base=/lbt-os/
✓ built in 10.65s
dist/assets/index-BeQLs1zM.js  1,411.44 kB   (healthy — not the degenerate tree-shake)
```
Bundle contains "Twistor Trades" ×1, "Sample data" ×4, "tradeview" ×2, "auth-callback" ×1. (One real defect caught by the build: a malformed `<Route>` JSX tag from the App.jsx edit — fixed, rebuild green.)

## 4. Guarantees table

| Guarantee | Proof |
|---|---|
| Unauthenticated GETs serve sample data | `sampleMode.test.js`: mapped GET → 200 + `SAMPLE_LEADS`; regex-mapped messages GET → 200 |
| Unmapped GETs pass through untouched | `sampleMode.test.js`: `/admin/stats` reaches the real adapter (no short-circuit) |
| Writes blocked while signed out | `sampleMode.test.js`: POST/DELETE → reject with `code: SAMPLE_WRITE_BLOCKED` |
| Blocked write bounces to sign-in | `sampleMode.test.js`: registered handler called once per blocked write |
| Blocked write tracks funnel intent | `sampleMode.test.js`: `signup_intent_click{element:'gated_write'}` |
| Sample mode off → zero interference | `sampleMode.test.js`: pass-through + handler not called |
| Every browse-page GET covered | `sampleData.test.js`: 25 required paths all match the sample map |
| Sample data is fictional | `sampleData.test.js`: all phones `(555)`, all emails `@example.com`; lists non-empty with ids |
| `is_demo` convention honored | `sampleData.test.js`: org, workspace-status, metrics, RI, brief, audit, strategy payloads carry `is_demo: true` |
| `app_view` once per session | `funnel.test.js`: 3 calls → 1 `trackVisitorEvent('app_view', {mode:'sample'})` |
| `signup_intent_click` / `signup_completed` shapes | `funnel.test.js`: exact event names + payload keys |
| Gated click (signed out) → intent + return + /sign-in | `useGatedAction.test.jsx`: tracking called, `sessionStorage` set, path → `/sign-in`, action not run |
| Gated click (signed in) → action runs | `useGatedAction.test.jsx`: action ran once, no redirect, no intent event |
| New user → /onboarding + signup_completed | `AuthCallback.test.jsx`: stored returnTo consumed, event fired with it, lands `/onboarding` |
| Returning user → back to gated page | `AuthCallback.test.jsx`: lands `/app/tradeview`, no signup event |
| Plain sign-in (no returnTo) → /onboarding | `resolvePostAuthDestination` pure tests (today's behavior preserved) |
| Unauthenticated /auth-callback → /sign-in | `AuthCallback.test.jsx` |
| No double-navigation (StrictMode-safe) | `doneRef` idempotency guard in `AuthCallback`; routing tests use real `<Routes>` so the component unmounts on navigation, as in the app |
| TW-294 navigate fix intact | `ClerkProviderWithNavigate.test.jsx` 3/3 still green; SignIn/SignUp keep `routing="hash"` + Clerk props; only `fallbackRedirectUrl` changed to `/auth-callback` |
| No secrets / FOSS-only | New files contain no keys/tokens; outbound hosts unchanged (api.weather.gov keyless, tile CDNs) |

## 5. Verification of each required behavior

1. **Public browse works unauthenticated:** interceptor serves all 25 browse-page GETs with fictional payloads; `RequireAuth` removed from `/app`; `SampleBanner` labels the mode; existing `is_demo` banners (RevenueIntel, BriefScene) activate.
2. **Auth only on click:** nav Sign in / Get started / sidebar CTA / sample banner CTA / `useGatedAction` (Connections) / any write attempt (interceptor) are the only auth triggers. Verified by the gated-action tests and the write-block tests.
3. **Funnel events fire:** `app_view` (once/session, `/app/*`, sample mode), `signup_intent_click` (element + page on every CTA and blocked write), `signup_completed` (new users only, in AuthCallback) — all through `trackVisitorEvent` → existing `/api/v1/visitor-events` sink. No new pipeline.
4. **Accessibility:** CTAs are native links/buttons (keyboard-reachable); visible `focus-visible` outlines on SampleBanner and Sidebar CTAs; `SampleBanner` is a labeled `region`; `prefers-reduced-motion` untouched (no new motion added).

## 6. Caveats for Rosa

- Sample payloads are static (not generated per-session); dates are fixed in late Sep 2026 — clearly demo, never real.
- Strategy `ask` / competitor search / AI audit run are POSTs → gated to sign-in by design (they need real data + AI backends).
- `/admin` and `/onboarding` remain behind `RequireAuth` (internal tooling, post-signup flow) — only `/app/*` was opened.
- `visitorEventsApi.capture` (axios POST) is Admin-only; the public funnel uses `trackVisitorEvent` (raw fetch) and is unaffected by the write block.
