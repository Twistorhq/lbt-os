<!-- Prepared by Twistor Holdings LLC -->

# TW-646: Twistor Social: design

Sources: TW-646 architecture spec (cited as "spec S5.2" style below), TW-647
Door Pass spec, reward catalog. Precedence, DECISIONS FIRST: Justynn's
recorded 2026-10-20 decisions, as carried in this package's
`.openspec.yaml` (`x-decisions_2026_10_20`) and the TW-646 ticket record,
override the source specs wherever they conflict. After the decisions,
the source specs govern; after the specs, this design. A disagreement
found anywhere in that chain is a review finding, not a license to
improvise. The edits owed to the source specs themselves so they stop
disagreeing with the decisions on paper are listed as an explicit task
(tasks.md item 43a).

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
on the weights). Any weights with unclear provenance. No paid vendor API
anywhere: there is no fallback path, in the default flow or out of it
(NO VENDOR FALLBACK doctrine, decided 2026-10-20).

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
- **Close friends and audience scoping (decided 2026-10-20):** a
  member-curated close friends list, `rsn_close_friends` (owner_id,
  friend_id, created_at; owner-curated, no reciprocity required, the
  friend is never notified of listing or removal). Posts and events carry
  an audience scope (group, connections, close_friends) enforced at the
  same query layer as S5.9: a close-friends-scoped item is absent, not
  greyed, for everyone outside the list. Audience scoping composes with
  age-band filtering; neither substitutes for the other.
- **RSVP approval sub-state (data-model delta to spec S4 `rsn_rsvps`;
  this package owns adding it, per TW-652 pattern 09 Section D).** The
  spec's RSVP states (going, waitlist, declined, no_show) have no state
  for a host's individual approval, and the home reveal tier below
  rests on exactly that approval, so: `rsn_rsvps` gains a `requested`
  status (pending host decision) with host decision fields
  (`host_decided_by`, `host_decided_at`). The home-event flow is:
  guest RSVP enters `requested`; the host approves (state becomes
  going, a host-approved confirmed guest) or declines (state becomes
  declined). Companion rule (Sofia pattern 09): the host's close
  friends are confirmed on RSVP and never enter `requested`; everyone
  else passes through request, then host approval, before any home
  reveal eligibility exists. The schema task (tasks.md item 13) and the
  RSVP task (item 16) carry this delta.
- **Home hosting (decided 2026-10-20; overrides the S9/S12
  public-venue-only assumption):** events may be hosted at a member's
  home. The home address lives only in `rsn_event_locations` (bronze)
  like any exact location, and is never public: home events do not appear
  on the public index, the directory previews, or any anonymous surface,
  and list payloads show neighborhood only, to eligible viewers only.
  The exact-address reveal tier for a home event is the strictest in the
  product: the host's close friends plus host-approved confirmed guests.
  **Home reveal window: 3 hours** (adopted from TW-652 pattern 09):
  the window opens 3 hours before event start and closes at event end.
  The reasoning is stated so it survives contact with a future editor:
  a home address is the most sensitive location class in the product,
  so it gets the tightest reveal window in the system; spec S8's
  48-hour window governs venue events and does not carry to homes. The
  home reveal condition set is explicit, and narrower than S8's: the
  viewer (a) is the host's close friend or a host-approved confirmed
  guest (RSVP sub-state above), (b) is inside the 3-hour home window,
  (c) holds a current age-band attestation and is band-eligible for
  the event (S5.9), (d) is T2 verified (no reveal of any kind exists
  below the verified floor), (e) has a trust band above restricted,
  (f) has no ban or block conflict with the host in either direction,
  and (g) is inside the event's audience scope (the home-event
  translation of S8's group-membership condition). Every reveal is
  logged in `rsn_location_reveals` exactly as for venue events. The
  full safety check-in loop (geo verdict, QR, encounter confirms,
  welfare, write-ups) applies to home events unchanged; a home is a
  venue for loop purposes, with the address treated at the highest
  sensitivity the model has.
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
- **Ambassador program: review staffing (decided 2026-10-20).** First-line
  human review is staffed by Verified Local Ambassadors, recruited from
  the platform's own high-standing verified locals rather than hired
  cold. The machinery:
  - **Eligibility and recruitment.** Standing tier Pillar or Keyholder,
    safety band trusted or good standing, no substantiated report in the
    trailing 12 months, resident in the metro they will review.
    Recruitment is an invitation: eligible members receive an in-app
    notification and an email invitation (Notify workflow
    `rsn_ambassador_invite`, both locales); nobody is enrolled without
    accepting, and acceptance starts Angela's vetting, not the role.
  - **Vetting (Angela's rules, TW-654).** Identity is already proven at
    T2; on top of it, counsel's vetting package governs: the background
    and conflict checks she specifies, a signed confidentiality and
    evidence-handling agreement, and completed review training before
    any queue access. Evidence access is a privileged role and is
    treated like one from the first ambassador (spec S12 Angela item 7
    applies to ambassadors in full).
  - **Scope (first line only).** Ambassadors work two queues inside
    their assigned metro: the verification exception queue (the engine's
    human review path, S5.2) and report triage (first read, category
    confirmation, priority assignment). Their decisions are bounded:
    they may clear a verification exception or route it to staff, close
    a report as unfounded with a recorded reason, or escalate. They may
    NOT issue or lift any sanction or ban (they recommend; staff
    decide), freeze or open evidence packets, answer disclosures, read
    face templates or door photos, or touch appeals. Serious categories
    (threats, sexual misconduct, identity fraud, anything touching the
    S6.4 emergency path) bypass ambassadors entirely and land on staff
    at intake. Every ambassador action writes `rsn_audit_log` with the
    ambassador as actor, on the same terms as staff actions.
  - **Scoped permissions.** Access is enforced at the entitlement layer,
    not the UI: an ambassador role row (`rsn_ambassadors`: user_id,
    metro, status (invited, vetting, active, suspended, revoked),
    vetted_by, activated_at) with queue-scoped read and action grants
    checked per request. Case data shown to an ambassador is the minimum
    the queue item needs; bronze document and biometric stores are never
    reachable from an ambassador session, and the verification exception
    view shows the attempt's captures only inside the S5.7 staging
    window and only for the item being decided.
  - **Revocation and standing coupling.** Staff may revoke instantly,
    with a mandatory reason, written to the audit log; the ambassador's
    open cases reassign automatically. Ambassador status suspends
    automatically, pending staff review, when the member's standing tier
    drops below Pillar, their safety band drops below good standing, or any
    report against them is substantiated; a suspended ambassador sees
    nothing and queues nothing. Breach of the confidentiality agreement
    is a severe-path conduct case (S6.6): the role's value is that the
    community's reviewers are themselves accountable to the ladder.
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

**Display rules (decided 2026-10-20).** Three rules bind every member
surface, and they refine spec S7.5/S8 where they differ:

- **Standing tier is visible.** The member's social currency tier
  (Verified, Regular, Fixture, Pillar, Keyholder) shows on their profile
  as designed social proof.
- **Punishment state is never displayed.** Safety band demotions (watch,
  restricted), sanctions, and restriction state render on no
  member-facing surface, profile or otherwise, to any viewer. This
  supersedes the S7.5 proposed-visible default for the safety band: the
  band is staff machinery plus the member's own private standing detail
  and Notify notices (due process is notification, not display), and no
  other member ever sees it. A restricted member's entitlements are
  simply absent, the same doctrine as the ghost town.
- **Age band is a color chip.** Profiles carry the band as a chip, never
  a birthdate: 21 plus is a green chip, 18 to 20 is a black chip, and
  the teen band (phase 2) is flagged with the word "teen". The chip
  states the band the door check proved and nothing finer. The chip is
  a door fact and is NOT toggleable: the decided chip rule wins over
  spec S8's band-display toggle. S8's member display toggle survives
  scoped to standing display only: a member may hide their standing
  tier, and that choice reaches nothing else.

## Retention change list (queued from TW-654, for Rosa to verify)

One correction to the source specs, flagged by Angela's legal package
and carried here so Rosa verifies it in this review rather than after
retention code exists:

- **Conflict.** Spec S6.3 keeps face templates for the life of the
  verified tier and verification signals and verdicts for 7 years.
  Colorado HB 24-1130 (biometric identifier duties) requires destruction
  of a biometric identifier no later than 24 months after the
  individual's last interaction with the business, subject to the
  exceptions counsel confirms (incident response, legal hold, and the
  statute's own carve-outs).
- **Corrected rule (Angela's TW-654 rule in full, S1.2 and S1.4).**
  Biometric identifiers (face templates, and door photos under TW-647
  S6.3) are destroyed at the EARLIEST of three triggers: (a) the
  original purpose is satisfied, which includes the S6.3 schedule
  events (account closure, tier surrender, verification revocation);
  (b) 24 months after the member's last interaction with the business
  (the HB 24-1130 dormancy edge); or (c) within 45 days after storage
  is determined no longer necessary (extendable by 45 days for
  complexity, following an annual necessity review). The only holds
  against a fired trigger are counsel-confirmed exceptions (incident
  response, legal hold, the statute's own carve-outs), and any hold is
  itself logged. BIPA's 3-year outer edge is looser; Colorado's 24
  months governs. Non-biometric audit records (signal trails, verdicts,
  the identity anchor hash) follow S6.3 unchanged: the law governs the
  biometric, not the fact that a verdict happened.
- **Dormancy consequence (TW-654 S1.4 item 2).** When trigger (b)
  destroys a dormant member's template, the account consequence fires
  with it: the member's tier drops to unverified, with notice (Notify,
  both locales, before the destruction date where the clock allows),
  write scopes are withdrawn to the ghost-town posture, and
  re-verification is available whenever the member returns. No account
  ever asserts T2 standing with no live template behind it: without
  this consequence the destruction job silently degrades the S6.7
  liveness re-challenge and duplicate search while the account still
  claims verified. The destruction job and the consequence are one
  task (tasks.md item 10a). The published retention schedule (S5.7) is
  updated to state the full rule, all three triggers and the
  consequence, before any production capture.

## Decisions that gate specific phases (not this package)

From spec S12, routed, none silently assumed:

- Justynn, DECIDED 2026-10-20 (spec S12 questions 1 to 4, closed): review
  staffing is the Verified Local Ambassador program (ambassador
  subsection above); home hosting is allowed with close friends and
  audience scoping (social core subsections above), retiring the
  public-venue-only question; there is NO vendor fallback, so the
  benchmark gate has no failure branch to gate, it passes or launch
  waits; display defaults are set (display rules above). Still open:
  organizer price points (gates public launch, not the build).
- Angela via TW-654: BIPA consent and retention (gates production capture),
  callout line (gates member-facing copy), law-enforcement process wording
  (gates the disclosure runbook), meetup liability posture (TW-654 D6
  terms allocation and D7 insurance conversation; gates public launch),
  home-event copy gates (gate the first home event, which can occur in
  soft launch), door photo consent (gates TW-653 step 3), liquor-law
  position (gates venue sales claims and the pilot), scan retention
  schedule, venue contract reliance terms. One gate, one wording: the
  proposal's launch gates carry the same items in the same words.
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
