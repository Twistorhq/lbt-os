<!-- Prepared by Twistor Holdings LLC -->

# Public overlay modules (TW-446)

Framework-agnostic data modules for the Trade View map. **Zero dependencies,
zero framework imports** — pure functions that fetch public data and return
normalized JSON. They run in a browser, in Node 18+, or anywhere `fetch`
exists.

## Portability contract

These files are intentionally dependency-free so both consumers can use them
verbatim:

- **LBT-OS (React/Leaflet):** `import { fetchRecentPermits } from './overlays/permits.js'`
- **Twistor website (Astro, static):** copy this whole directory into the
  Astro project and import the same way. No build step, no adapter.

If you add a module here, keep it dependency-free or it breaks the contract.

## Modules

| File | Source | What it returns |
|---|---|---|
| `http.js` | — | Hardened `fetchJson` (host allowlist, timeout, byte cap) + TTL cache |
| `permits.js` | City and County of Denver, ArcGIS Hub (free, no key) | Recent residential + commercial construction permits, normalized with lat/lon |
| `nws.js` | National Weather Service api.weather.gov (free, no key) | Active weather alerts + current station observations |
| `index.js` | — | Re-exports |

## Data sources

- **Permits:** Denver ArcGIS Hub FeatureServers
  - Residential: `…/ODC_DEV_RESIDENTIALCONSTPERMIT_P/FeatureServer/316`
  - Commercial: `…/ODC_DEV_COMMERCIALCONSTPERMIT_P/FeatureServer/317`
  - Portal: https://opendata-geospatialdenver.hub.arcgis.com
  - Fields: `PERMIT_NUM, ADDRESS, CLASS, VALUATION, DATE_ISSUED (epoch ms), CONTRACTOR_NAME`; geometry returned as WGS84 via `outSR=4326`.
  - NOTE (verified 2026-10-12): Denver retired its Socrata catalog (~2025) and
    moved to ArcGIS Hub. Any Socrata dataset ID for Denver permits you find in
    old docs is dead — use the FeatureServers above.
- **Weather:** `https://api.weather.gov` — `/points/{lat},{lon}` →
  observation stations → latest observation; `/alerts/active?point={lat},{lon}`.
  NWS asks clients to send a User-Agent; browsers send their own automatically.

## Freshness & politeness

| Source | Cache TTL | Typical volume |
|---|---|---|
| Permits | 15 min | ≤150 rows/query, `resultRecordCount` capped |
| NWS alerts | 5 min | handful of features |
| NWS conditions | 10 min | 1 observation |

Every result carries `fetchedAt` (ISO) and `source: { name, url }` so the UI
can render "as of" timestamps and attribution. If a source is unreachable the
module throws an `OverlayError`; the UI layer is expected to catch it and show
the clearly-labeled fallback — never a silent empty map.

## Security (for Rex's review)

- `http.js` enforces an **exact-host allowlist** (`api.weather.gov`,
  `services1.arcgis.com`) and HTTPS-only. No caller can make these modules
  fetch an arbitrary URL — endpoints are module constants, lat/lon are
  validated numbers.
- 10 s default timeout via `AbortController`; 2 MB response cap.
- No secrets, no API keys, no cookies, no PII in requests or logs.

## Provenance labels (Justynn's rule)

Numbers from these modules are **real public data** — label them as such in
the UI. If you derive anything (e.g. "permits near this address"), label the
derivation and show the interval/assumptions. Never present a modeled number
as an observed one.
