# TW-304 + TW-309 — Trade View elevation evidence

Prepared by Twistor Holdings LLC.

## TW-304 — client edition expansion + SiteView

**What:** New client-facing Trade View at `/app/tradeview/client`
(`frontend/src/pages/tradeview/ClientTradeView.jsx`), linked in the sidebar
as "Trade View — Client".

**Client-edition rules enforced (and tested):**
- Zero SQL/code anywhere on the surface — a dedicated test asserts the
  rendered page and the report modal contain no `SELECT`/SQL/sandbox text.
- Talk-or-type: plain-language ask box with suggestion chips and
  `aria-live` answers drawn from the sample dataset.
- ONE primary button: "Get my free trade report" opens a marketing
  one-pager (What Happened / What Will Happen / What Should We Do,
  honest fine print, Print button). Esc closes; focus is managed.
- Faceless: no people imagery — map, motion, and typography only.
- Cinematic scroll: sections scale and shift depth on entry via
  IntersectionObserver; `prefers-reduced-motion` disables it.

**SiteView:** "Take the SiteView tour" opens a fullscreen cinematic
flythrough — Before / During / After chapters with map flyTo moves,
progress indicators, arrow-key navigation, Esc to close, auto-advance
(6.5s) disabled under reduced motion. Repurposes the gods-eye-view
Director pattern (TW-279) as a guided camera tour.

**Brand-board notes:** near-black + indigo-violet system inherited from
`tradeview.css`; photographic-not-AI (no generated people); sample data
labeled on every surface; "Prepared by Twistor Holdings LLC" in footer
and report.

## TW-309 — storm-opportunity layer

**What:** The NWS storm layer flipped from risk to revenue.

- New pure-logic module `frontend/src/pages/tradeview/stormOpportunity.js`:
  NWS event → opportunity mapping (tornado / hail / wind / flood / fallback),
  ray-casting point-in-polygon, per-cell surge computation. All ticket
  ranges are labeled illustrative estimates, never quotes.
- Internal edition (`TradeView.jsx`): new "Storm opportunity (revenue)"
  toggle. One NWS fetch feeds both the risk layer and the revenue layer;
  revenue cells render gold with popups (event, trades, illustrative
  ticket range, surge count with company names, estimate disclaimer).
  A surge banner in the HUD reports per-cell affected counts.
- Client edition gets the alert: a plain-language storm opportunity card
  with a "Notify me about storm work" CTA (demo-honest confirmation).

**Tests:** 31 tradeview tests pass (14 existing incl. 1 new toggle test,
9 stormOpportunity logic, 8 ClientTradeView). Full frontend suite: 72/72.

## Before/after proof

| Shot | Shows |
|---|---|
| `309-before-internal.png` | Internal edition before the new layer (risk toggle only) |
| `309-after-opportunity-layer.png` | Gold revenue swath rendered over 2 sample pins |
| `309-opportunity-popup.png` | Cell popup: event, trades, illustrative ticket, surge names |
| `309-surge-banner.png` | HUD surge banner with per-cell counts |
| `304-client-hero.png` | Client edition hero — cinematic, faceless |
| `304-client-ask.png` | Talk-or-type answering in plain words |
| `304-client-report.png` | One-button marketing report modal |
| `304-siteview-tour.png` | SiteView cinematic tour, chapter 1 of 3 |
| `304-client-storm.png` | Client storm opportunity alert card |

**Capture notes (honest):** Denver had zero active NWS alerts at capture
time, so `api.weather.gov` was intercepted in the screenshot harness and
fulfilled with an NWS-shaped Severe Thunderstorm Warning fixture over
central Denver (labeled DEMO FIXTURE in the fixture headline). Map tiles
do not load inside this sandbox (egress proxy returns empty responses to
the headless browser; curl succeeds) — production tile rendering was
verified live on 2026-10-03. Pins, swaths, banners, and popups all render
from real component code paths.
