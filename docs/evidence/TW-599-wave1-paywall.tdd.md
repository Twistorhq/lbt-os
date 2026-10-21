# TDD Evidence: TW-599 Wave 1 — S2S-TRUST v1 + CLIMATESHIFT-1 v1 with day-one paywall

**Prepared by Twistor Holdings LLC**

## 1. Source
- Ticket TW-599 (Justynn 2026-10-18, WAVE 1 triggered): build S2S-TRUST v1
  (subseasonal prediction) + CLIMATESHIFT-1 v1 (crops, hemp-first) FIRST,
  ahead of the other lanes. Follow-up to TW-593/TW-598.
- Diego's V1 specs:
  `twistor-lab/rea/battle-plans/2026-10-18-scientific-datalayers/lane2-subseasonal-prediction.md`
  and `lane5-crop-climate-shift.md`.
- Delivery + paywall spec (Justynn's words, implemented exactly):
  large = API only; medium = API + downloadable Excel; small = downloadable
  Excel, API if needed. EVERYTHING behind the paywall: toll-metered API with
  dynamic derivative pricing, PLUS a one-time Excel download fee ON TOP of
  the Twistor subscription. Excel pricing: small ~$5-7, medium ~$7-10,
  large ~$20-25.
- Branch: `dre/tw-599-wave1-paywall` (from `dre/tw-593-science-v1` @ 338262b).

## 2. What was built

### Shared paywall module (new): `backend/app/data_layer/science/paywall.py`
- **Tiers**: small < 100k rows, medium 100k-1M, large > 1M.
- **Delivery matrix**: small {api, excel}, medium {api, excel}, large {api only}.
- **Excel fees**: small $6.00 / medium $8.50 / large $22.50 (all inside his bands).
- **Subscription gate** (`require_active_subscription`): every science read
  needs `organizations.subscription_status == 'active'`; anything else (incl.
  missing/unknown org, lookup failure) → 402 fail-closed. No free tier.
- **Dynamic derivative pricing** (`metered_toll`): per-unit rate =
  TOLL_RATES[event] × volume multiplier from trailing-30d science spend
  (1.0x < $50, 0.9x $50-200, 0.8x $200-1000, 0.7x > $1000). Rates fall as
  volume rises — the volume-discount derivative.
- **Download toll** (`emit_download_toll`): `data.science.download` event with
  the tier fee as explicit amount_cents, on top of the subscription.
- **Excel builder**: openpyxl workbook, DATA sheet + README provenance sheet
  (dataset, version, tier, fee, synthetic flag, license note).

### S2S-TRUST v1 (subseasonal lane)
- **S2S-TRUST schema** (Diego's spec): `center`, `model_cycle`, `run_type`
  (realtime|reforecast), `truth_source`, `truth_lag_days`,
  `assimilation_window`, `reforecast_design` on ingest/silver/gold schemas +
  `migration_sci_subseasonal_v2.sql`. Seed enriched (ECMWF IFS CY48R1 with
  ERA5 truth, +9h/-3h window declared; reforecast design on-the-fly:20y:11m).
- **Truth quarantine**: ingest records missing `truth_source` quarantine to
  `provenance_incomplete` — never silently mixed in (Diego's spec).
- **Paywall wired**: subscription gate on all reads (402), tolls via
  `paywall.metered_toll`, new `GET /datasets/{version}/download`
  (tier-checked: large → 403 API-only).
- Dataset renamed `twistor-s2s-forecasts` → `s2s-trust`.

### CLIMATESHIFT-1 v1 (crop-climate lane, hemp-first)
- **`traits` panel** (Diego's spec): cannabinoid panel (THC/CBD/CBG),
  fiber quality, lab accreditation ID, COA hash — on Measurement/GoldRow/
  schemas/supabase_store + `migration_sci_crop_climate_v2.sql`. Masked on
  `test_future` like yields (trait panels are answers too).
- **Hemp-first seed** (`seed_hemp_records()`): 30 synthetic hemp trials —
  8 cultivars (Futura 75, Fedora 17, Santhica 27, Carmagnola, CRS-1, Katani,
  X-59, Altair) × 5 extension sites (VT/OR/MO/NY/CO), 2022-2026, with the
  documented Missouri-2026 GxE instability (heat pushes THC toward/over the
  0.3% compliance line). 8 of 30 rows are the withheld 2035-like split.
  The 40-row maize/soy/wheat corpus rides as methodology reference (70 total).
- **Paywall wired**: same as subseasonal — gate on reads, dynamic tolls,
  `GET /datasets/{version}/download` with flattened trait columns.
- Benchmark + dataset renamed to `climateshift-1`.

## 3. Test report
- New `backend/tests/test_science_paywall.py`: 13 tests (tier boundaries,
  delivery matrix, fee bands, gate allow/block/fail-closed, volume multiplier
  tiers, discounted toll math, download fee amount, Excel round-trip + headers).
- Subseasonal: 23 tests (toll spy repointed at `paywall.metered_toll`;
  HTTP 402 gate tests; download 200 on small tier with fee assertion;
  403 on large tier; truth-quarantine test; seed lineage test).
- Crop-climate: 33 tests (hemp seed shape/GxE/withheld counts; trait
  serve/mask discipline; trait ingest; 402 gate; download 200 + $6.00 fee
  toll assertion; 402 without subscription).
- Full science suite: **193 passed**. Ruff: clean.

## 4. Known limitations
- Subscription identity rides the existing `X-Org-Id` header pattern per lane;
  a unified API-key→org binding is V2 (the sci_api_keys lab_binding covers
  mutations today).
- Excel capped at 500k rows per download (beyond that: the API).
- Real (non-synthetic) S2S/CLIMATESHIFT data awaits partner onboarding;
  V1 seeds are synthetic-only and labeled.

## 5. Merge readiness
Rosa review requested on this diff. Merge requires Justynn's exact word.
