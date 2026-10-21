# Trade View Lite / Pro — iteration 1

Prepared by Twistor Holdings LLC. Checked 2026-10-11 UTC.

## What this iteration delivers

- Public `/tradeview/lite` route: free, interactive fictional property-maintenance workflow; three trades, selectable pins and list, layer toggle, per-request progress, reset. No persistence, outreach, charges or dispatch.
- Same React component exported as `docs/previews/tradeview-lite-v1.html`. Its CSP blocks network access. It needs no Clerk, map, billing or Google key. The illustrated map is not actual geographic imagery.
- Pro tab loads the existing organization-scoped Trade View pins endpoint. It shows subscription/auth failure, connection failure, empty records and unlocated counts explicitly; never replaces connected records with samples. Existing geographic Trade View is linked only after successful access.
- Every `/api/v1/tradeview/*` endpoint now requires server-resolved active subscription status, a Stripe subscription reference, and a recognized existing plan. This intentionally removes previously free signed-in access to these APIs. No new SKU, price or checkout is introduced. Existing Stripe sync maps trials to active: access is **not evidence of a settled payment**.
- Authentication and connected APIs stay in LBT-OS. No Core migration or duplicate billing application.

Native app still needs its existing Clerk configuration. Offline preview does not. The Pro data path is implemented and tested with fixtures; customer integration, provider sandbox and production verification remain separate gates. No production deployment is performed.

## Reuse and source evidence

Inspected LBT-OS main `cf65da3e85f1d920e72acb6af1d2889a3a329060`:

- `frontend/src/pages/TradeView.jsx`: Leaflet geographic layers, connected pins, dossiers, leaks, routes; legacy map explicitly falls back to labeled sample mode when disconnected. That legacy fallback is not a connected-data guarantee.
- `frontend/src/pages/tradeview/ClientTradeView.jsx`: guided SiteView tour; existing sample/live-weather interface.
- `backend/app/routers/tradeview.py`: organization-scoped data and rate limits.
- `backend/app/services/stripe_service.py`: subscription lifecycle/webhook synchronization; `backend/app/auth.py`: authenticated organization context.

God's Eye: https://github.com/bilawalsidhu/gods-eye-view at `591f299d11f38a612629a274463196d57ae3862e`, about 50,000 stars at inspection. Reviewed `LICENSE`, `DATA_SOURCES.md` and `src/mapStackController.js`. The source architecture separates map sources and viewer control. No upstream code/assets are copied in this iteration. The full embedded-view objective and all listed dataset integrations remain in scope, per Justynn's clarification. No existing dataset is removed. `gods-eye-layer-catalog.json` retains upstream source entries and activation gates; this is a metadata inventory, not imported datasets or implemented adapters. For the eventual embed, prefer an owned, pinned deployment with an allowlisted message bridge, separate origin and server-side access checks over relying on someone else's public hosted app.

License: https://github.com/bilawalsidhu/gods-eye-view/blob/591f299d11f38a612629a274463196d57ae3862e/LICENSE . Source code is MIT with notice requirements; third-party data/models have separate terms. In particular, listed submarine-cable and Bhote Koshi datasets are noncommercial. Keep their integration entries and future scope; commercial dataset delivery stays inactive until permission covering the intended use is obtained. A subscription to Google or one data provider does not license all other layers. No data subscription is purchased here.

Google guidance:
- https://developers.google.com/maps/documentation/tile/policies
- https://developers.google.com/maps/documentation/places/web-service/policies
- https://developers.google.com/maps/documentation/tile/usage-and-billing

Google geographic/place context does not establish contractor licensing, availability, customer consent, actual job progress or payment status. Imagery is not live video. Billing, attribution, caching restrictions and restricted keys are independent integration requirements.

## Next iterations and ownership

1. **Experience review (this change):** review map selection, trade filtering, handoff wording, reset and Pro lock. Review at 390px and desktop in a real browser before merge. Unit tests are not visual verification.
2. **LBT-OS map team, estimated 1–2 engineering days for the initial adapter seam only:** build a common map-source interface around existing Leaflet and a lazy-loaded Cesium/Google 3D adapter informed by God's Eye's source-controller pattern. Retain all catalogued God's Eye sources for the eventual embedded experience, including layers requiring future subscriptions/permissions. Full upstream parity needs separate per-layer estimates after integration discovery; 1–2 days is not an estimate for all layers. Keep sample positions and organization positions in distinct providers. No automatic external requests from Lite. Source statuses: disabled, license required, credentials required, loading, ready, error. Preserve attribution in every view. Separate map imagery from operational overlays.
3. **LBT-OS billing/data team, estimated 1–2 days:** verify subscription transitions against Stripe sandbox, including trials, delinquency, cancellation, webhook retry/out-of-order events and reconciliation. Confirm the commercial choice of which existing plans include Trade View before rollout. Validate cross-organization denial with two authorized tenants; verify demo-seeded organization records are not presented as customer-verified data. The legacy map's labeled fallback must remain visibly distinct or become a fail-closed Pro route.
4. **LBT-OS + Core shared-contract owners, estimated 2–4 days for a thin pilot:** one authorized property manager's sample, own vendor roster, single-winner assignment, manager approval, completion evidence, audit trail. Core owns shared approval/event contracts; LBT-OS owns UI/adapters. This sample progress button does not implement real dispatch or prove fulfillment.
5. **Google activation, owner-controlled:** select/approve a billing-enabled project, restricted browser/server keys appropriate to each API, domain restrictions and quota limits. Store credentials in deployment secret settings, never chat or repository. Confirm commercial source terms and attribution. No accounts or charges are created here. Zero-cash alternative: keep Lite plus existing authorized business records without Google imagery.

Live video and marketplace payouts are separate later integrations. Video needs consent, access controls and a bandwidth/retention budget; payouts need provider onboarding and a reconciliation/refund/dispute flow. Neither is represented as available in this release.

## Acceptance and launch gates

- Lite works offline; no signup, API request or payment; all requests clearly fictional.
- Filtering and pin/list selection agree; progress is isolated per request; reset clears it; keyboard controls work.
- Pro never substitutes fixture records on 401/403/5xx or invalid source payloads.
- Direct calls to every connected Trade View route deny inactive/no-subscription access before data reads.
- Existing organization-isolation tests continue to pass with eligible subscriptions.
- Production build and standalone export succeed. Browser visual/accessibility checks, actual Google rendering, live webhooks, connected customer data and production deployment are not implied by these results.

Development uses existing hardware and dependencies. Live hosting, imagery APIs, bandwidth, messaging and payment processing may cost money; activation requires a separate approved budget. No subscription revenue, buyer commitment or paid outcome is claimed.

## Executed checks

- Frontend: 62 tests passed across Trade View, Lite/Pro and sample-mode suites.
- Backend: 61 tests passed across pins, actions, leaks and subscription-route denial suites.
- Production `npm run build`: passed (existing large-bundle/Browserslist warnings).
- Standalone exporter: passed; generated HTML renders and switches editions in jsdom.
- `git diff --check`: passed.
- Existing frontend tests emit React act warnings. No actual browser/screenshot, provider-sandbox, Google, real customer or production test was performed.

**Addendum (TW-677 fix round, 2026-10-21):** the browser gate in item 1 above has since been performed on the regenerated preview: real Chromium render at 390px and desktop, zero console errors, no horizontal overflow, honesty labels and the Pro subscription boundary verified, screenshots recorded. Full record: `docs/evidence/TW-677-pr4-fix-round.tdd.md`. Provider-sandbox, Google, real customer and production tests remain unperformed.
