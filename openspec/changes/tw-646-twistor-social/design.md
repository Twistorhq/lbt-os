<!-- Prepared by Twistor Holdings LLC -->

# TW-646: Twistor Social: design

Sources: TW-646 architecture spec (cited as "spec S5.2" style below), TW-647
Door Pass spec, reward catalog. Where this design and a source spec disagree,
the source spec wins and the disagreement is a review finding, not a license
to improvise.

## Module layout (lbt-os backend, existing Notify/Telemetry pattern)

| Package | Contents | Owner ticket |
|---|---|---|
| `backend/app/verify/` | Engine pipeline, sessions, verdict API, attestations, door photos, benchmark harness (`benchmark/` subpackage) | TW-649 harness; engine build follows |
| `backend/app/social/` | `models.py`, `service.py`, `router.py`, `safety.py`, `trust.py` (pure functions), `sessions.py` (scheduler tick), `geo.py` (pure functions) | TW-650 skeletons, then feature phases |
| `backend/app/door/` | Door Pass minting, nonce registry, verify endpoint, venue domain | TW-653 |
| `backend/app/social/events_index/` (or Dre's placement per TW-651) | `evt_` ingest: ICS poller, API client, organizer import, dedupe, provenance gate, gold read model | TW-651 |

Conventions inherited from Core (spec S3): table prefixes `vfy_` / `rsn_` /
`evt_` / `dps_`; RLS deny-all on every table, service role only; idempotent
migrations (`IF NOT EXISTS`); slowapi buckets on every endpoint, strictest on
verification, location reveal, and report filing; fail-soft for telemetry and
notification sends, fail-closed for location reveal, tier gates, and ban
checks; scheduler tick (TW-625 Flows pattern) drives time-based work; all
user-facing strings from a catalog, locales en + es at launch.

## Verification engine (spec S5)

Five stages in order (S5.2), any stage may route to human review:

1. Document capture and extraction: PaddleOCR (Apache-2.0), MRZ parse with
   check-digit validation (passporteye, MIT), PDF417 decode (zxing-cpp,
   Apache-2.0); front-to-barcode field agreement is a tamper signal.
2. Authenticity signals: MRZ checksums, template geometry, capture forensics.
3. Face match: YuNet detection (MIT), MediaPipe landmarks (Apache-2.0), dlib
   ResNet embedding via `face_recognition` (Boost / freely usable model).
   Cosine distance against a tuned threshold: match, no-match, or review.
4. Liveness: guided capture with randomized active challenges plus
   MiniFASNet passive signal (Apache-2.0), one signal among many, never a
   sole gate.
5. Fraud signals and verdict: duplicate search over templates and identity
   anchors, stolen-photo cross-reference inside the platform corpus, velocity
   limits; verdict pass / fail / human review with the full signal trail in
   `vfy_signals`.

**Excluded:** InsightFace pretrained packs (non-commercial research license
on the weights). Any weights with unclear provenance. No paid vendor API in
the default path (FOSS-first).

**API boundary (S5.6), verdict-only:** `POST /verify/sessions`,
`POST /verify/sessions/{id}/document`, `POST /verify/sessions/{id}/selfie`,
`GET /verify/sessions/{id}` (status, tier, confidence band, age band only),
`POST /verify/anchors/lookup` (service-to-service, booleans only). Images go
to the encrypted staging path, are destroyed on decision, 72-hour hard
ceiling; face templates live encrypted in bronze; every cross-system
reference is the keyed HMAC identity anchor (S5.7, S4 `vfy_identity_anchors`).

**Age bands (S5.8):** the engine computes the band at decision time into
`vfy_age_attestations`; raw date of birth never leaves bronze; RSN receives
bands and per-event booleans only. Graduation is automatic off
`next_band_date`, held inside the verify service only.

## Benchmark gate mechanics (spec S5.3; harness TW-649)

The gate is code, not a meeting. The harness computes, from scored attempts
produced by the real stack on registered test datasets:

- Face match: false-match rate (FMR) and false-non-match rate (FNMR) at the
  production candidate threshold, overall and **per demographic group**.
- Document stages: per-field OCR exact-match accuracy, MRZ check-digit
  validation rate, PDF417 front-to-barcode agreement rate.
- Liveness: APCER / BPCER (ISO 30107-3 terms).
- Gate evaluation: PASS, FAIL, or INSUFFICIENT_DATA. A group with too few
  attempts to measure cannot pass by silence; minimum sample sizes are part
  of the criteria. Planning criteria ship as configuration in the harness
  (FMR ceiling 1 in 10,000 per S5.3; per-group FNMR ceiling and a
  cross-group FNMR gap ceiling as planning values); final numbers are set
  with the measured report in front of Rosa and Justynn before T2 opens.

Datasets: synthetic and properly consented or public research sets only,
each registered in the harness with its license and consent basis. No scraped
faces, no production data, no member data. The harness README is the
registry's public face.

## Social core and safety (spec S4, S5.1, S5.9 to S5.12, S6, S7)

- **Identity (reuse, TW-619):** accounts, RS256 JWTs, session families. RSN
  adds only the verification pointer on `idn_users`. RSN write scopes are
  minted from the pointer: no passed T2 verdict, no write scopes. The ghost
  town (S5.11) is therefore enforced at token minting and re-checked at
  action time for sensitive writes (RSVP, reveal, hosting).
- **Tier ladder (S5.1):** T0 account (ghost town), T1 reputation state
  (unlocks nothing), T2 verified. Groups and hosts may raise gates above
  verified; nothing lowers them.
- **Age-gated visibility (S5.9):** `min_age_band` NOT NULL on groups, events,
  series; viewer band resolved per request and applied at the query layer on
  every surface; counts computed after filtering; fail-closed on unrated
  content and on members without an attestation; shared links and OG
  previews return the generic restricted shell.
- **Check-in loop (S7):** on-device geofence verdict (no coordinates cross
  the wire, no coordinate column exists), venue QR strong proof, encounter
  confirms, welfare check with the help sheet and silent alert, structured
  post-event write-ups. Scheduler tick per S7.6; Notify workflows per S2.
- **Trust (S7.5):** append-only `rsn_trust_events`; score, band, and social
  currency standing tier (Verified, Regular, Fixture, Pillar, Keyholder;
  thresholds owned by the reward catalog) recomputed by pure functions in
  `trust.py`; dormancy decays the tier only, never verification; standing is
  never purchasable (S1 red line).
- **Rewards mechanics (S7.7 to S7.10):** tiered RSVP waves on one capacity
  pool, host-held seats capped at 20 percent, standing capacity entitlements
  plan-independent, guest passes on `rsn_guest_passes` with all outcomes
  written to the anchor's ledger, collectibles derived by rule table
  (`rsn_collectible_rules`) with revocation cascade, recognition artifacts
  as derived renderings.
- **Safety system (S6):** reports with SLA timers, sanctions ladder
  (warning, restriction, identity ban) with the appeals workflow (24-hour
  acknowledgement, 7-day decision by a fresh reviewer), evidence packets
  with hash-chained manifests, disclosure log, append-only audit log,
  integrity lifecycle (S6.7): nightly incremental and monthly full duplicate
  sweeps, risk-triggered plus roughly 1 percent monthly random liveness
  re-challenges, purge pipeline (flag, challenge, 14-day grace, purge) as
  its own sanction class, 12-month dormant-ghost hygiene sweep.
- **Media ingest:** one choke point; every upload re-encoded, EXIF and GPS
  stripped before storage; originals never persisted (S4, S8).
- **Billing (S4):** `rsn_plan_catalog` with `price_cents` NULL until Justynn
  sets launch prices; `social.organizer_plan_period`,
  `social.event_promoted`, `social.listing_claim` tolls on the platform
  ledger, idempotency-keyed; entitlement checked before the toll write.

## Events data layer (spec S10; TW-651)

Medallion: bronze raw payloads where source terms allow retention, silver
harmonized facts with provenance, gold public read model served through a
read-only role (the only public `evt_` surface). Three pipes (ICS poller,
API client, organizer OAuth import), fingerprint dedupe (normalized title +
venue fingerprint + 5-minute start bucket), freshness and cancellation
rules, fail-closed provenance gate. Listings carry facts and attribution
only; never descriptions, photos, or branding from sources (S10.6).
Claim-a-listing proof ladder: source-account OAuth, then domain control,
then staff review; claim approval confirms the age rating and writes the
claim toll. Public surface follows the TW-604 portal pattern, schema.org
Event JSON-LD, Leaflet + OpenStreetMap previews, directions as deep links
to the phone's maps app; geocoding is cache-first behind a provider
interface (source coordinates, venue cache, rate-limited Nominatim queue,
self-hosted Nominatim or Photon as the scale answer).

## Door Pass (TW-647; TW-653)

New audience on Identity's signing machinery, not new key infrastructure:
compact JWS, RS256, `aud=doorpass`, 45-second maximum life, single-use `jti`
in `dps_nonces`, device-key-bound minting (WebCrypto), per-member pass epoch
in `dps_pass_state` for instant revocation. Verify endpoint validates
cheapest first and answers from live state (account disabled, ban,
restriction, attestation status, band, device status, epoch). Three result
states only (valid, not valid, unavailable), uniform failure rendering, per
-scan signed photo URL, animated validity element with live server clock.
Door photo is a separate consented artifact in `vfy_door_photos`, served
only at scan moments, behind its own Angela gate (TW-647 S9). Venue domain
(`dps_venues`, locations, lanes, staff, plans) with claim proof ladder,
signage kit behind its copy gate, `doorpass.scan` tolls idempotency-keyed
`doorpass:{jti}`, TW-608 no-surprise bundle warnings at 80 percent.
Roughly 70 percent reuse per TW-647 S10.3; genuinely new is the `door/`
package and the venue domain.

## Surfaces (spec S1, S11; TW-652, Sofia)

Mobile-first PWA for member flows (thumb-first, in-venue, one-handed,
low-light), desktop operator layers (organizer console, venue dashboard,
staff safety console), Astryx with shared brand tokens. This product is the
decided Social-only exception to the house browser-first rule (Justynn,
2026-10-20). iOS home-screen web-app push is evaluated in v1 as the welfare
loop mitigation; native remains a v2 decision on loop data.

## Decisions that gate specific phases (not this package)

From spec S12, routed, none silently assumed:

- Justynn: review staffing and coverage hours (gates soft launch),
  public-venue-only soft launch confirmation (gates soft launch), vendor
  fallback trigger (gates only the failure branch of the benchmark gate),
  trust band display default (gates profile surface only), organizer price
  points (gates public launch, not the build).
- Angela via TW-654: BIPA consent and retention (gates production capture),
  callout line (gates copy), law-enforcement process wording (gates the
  runbook), meetup liability posture (gates launch), door photo consent
  (gates TW-653 step 3), liquor-law position (gates venue sales claims and
  the pilot), scan retention schedule, venue contract reliance terms.
- Phase 2 only: minors package, guardian consent, biometrics on minors,
  school registry sourcing (spec S12 Angela items 4 and 8).

## Sequencing (spec S13, binding)

1. This package; Rosa reviews proposal and design before code. (TW-648)
2. Verification engine, benchmarked in isolation; harness first. (TW-649)
3. Schema and migrations (`vfy_`, then `rsn_`), RLS deny-all, skeletons.
   (TW-650)
4. Groups, events, RSVP, location reveal; then the check-in loop; then feed;
   then safety console and bans; then the privacy pass. Events ingest and
   the public index run in the same window; claim-a-listing lands once
   groups exist. (feature phases; TW-651 for ingest)
5. Sofia's surfaces in parallel from step 4, same lane, not after. (TW-652)
6. Door Pass after its dependencies (Identity audiences, attestation store,
   Social standing) exist as specced. (TW-653)
7. Denver soft launch behind the spec S9 gates.

Every phase: RED-first tests, TDD evidence report in `docs/evidence/`,
Rosa review before merge, Justynn's merge word per merge.
