<!-- Prepared by Twistor Holdings LLC -->

# TW-646: Twistor Social: proposal

- Ticket: TW-648 (this change) · Parent: TW-646 · Reporter: Justynn · Owner: Zeke Okafor (Lead Build Engineer)
- Sources: `twistor-lab/specs/TW-646-real-social-network-architecture.md` (789 lines, the architecture and safety design), `twistor-lab/specs/TW-647-door-pass-architecture.md` (venue credential), `twistor-lab/brand/twistor-social-reward-catalog.md` (social currency catalog)
- Companions: TW-649 (benchmark harness, Zeke), TW-650 (schema + skeletons, Dre), TW-651 (events ingest, Dre), TW-652 (member surfaces, Sofia), TW-653 (Door Pass, Zeke), TW-654 (legal gates package, Angela)

## Problem

Justynn greenlit the build (2026-10-20) of Twistor Social: a groups-first,
mobile-first social app whose moat is safety through verified identity. The
architecture is fully designed in the source specs. What does not exist yet:

1. Any code. No `backend/app/verify/`, no `backend/app/social/`, no
   `backend/app/door/`, no `vfy_` / `rsn_` / `evt_` / `dps_` tables.
2. Any measurement of the verification stack. The engine is FOSS components
   (PaddleOCR, MRZ/PDF417 checks, YuNet, MediaPipe, dlib, MiniFASNet) whose
   real-world accuracy on ID-photo-to-selfie matching, broken out by
   demographic group, is the product's launch gate (spec Section 5.3). We do
   not ship a trust product on unmeasured trust machinery.
3. A reviewable change package. Per the TW-100 spec-first rule and spec
   Section 13 step 1, Rosa reviews the proposal and design before code.

## Scope (this change package)

This change authors the build plan only. It implements nothing. The build
itself lands as the companion tickets above, sequenced exactly per spec
Section 13 and gated as listed under Launch gates. The package covers:

1. **Verification engine** (spec Section 5): `backend/app/verify/`, five-stage
   pipeline, verdict-only API boundary (Section 5.6), age-band attestations,
   benchmarked against its launch gate in isolation before anything depends
   on it (TW-649 builds the harness first, in parallel with this package).
2. **Schema and service skeletons** (spec Sections 3, 4): `vfy_` then `rsn_`
   migrations, RLS deny-all, service-role only, idempotent (TW-650).
3. **Social core**: groups, events, series, RSVP with standing-tier waves,
   tiered location reveal with logging, couple links, blocks, ghost town
   enforcement at the token and entitlement layer (spec Sections 4, 5.1,
   5.9, 5.11, 7.7). **Home hosting is in scope** (decided 2026-10-20,
   overriding the spec Sections 9/12 public-venue-only assumption):
   a member-curated close friends list, per-post and per-event audience
   scoping, and home events whose exact address is restricted to the
   host's close friends plus host-approved confirmed guests, with the
   full check-in loop and reveal logging applied unchanged.
4. **Safety check-in loop and trust**: geo arrival (on-device verdict), venue
   QR strong check-in, encounter confirms, welfare check, post-event
   write-ups, trust ledger and bands, social currency tiers, guest passes,
   collectibles, recognition artifacts (spec Sections 6, 7; catalog).
5. **Safety system**: reports, staff console, sanctions ladder, appeals,
   identity-anchored bans, evidence packets, disclosure log, audit log,
   account integrity lifecycle with the due-process purge pipeline (spec
   Sections 6.1 to 6.7). Review staffing is the **Verified Local
   Ambassador program** (decided 2026-10-20): first-line review by
   vetted Pillar and Keyholder locals under scoped permissions, with
   serious incidents escalating to staff (design.md carries the role,
   scopes, and revocation).
6. **Events data layer** (spec Section 10): `evt_` schema, ICS poller, API
   client, organizer import, fingerprint dedupe, fail-closed provenance
   gate, public Denver index, claim-a-listing (TW-651).
7. **Organizer billing** (spec Section 4): plan catalog with prices left as
   configuration, organizer plans, idempotency-keyed tolls on the platform
   ledger. Attendees are never charged.
8. **Member and operator surfaces** (spec Sections 1, 11): mobile-first PWA
   for member flows; desktop operator layers (organizer console, venue
   dashboard, staff safety console); English and Spanish at launch, human
   translated (TW-652, Sofia). Display rules (decided 2026-10-20):
   standing tier is visible on profiles; punishment and restriction
   state is never displayed; age band renders as a color chip (21 plus
   green, 18 to 20 black, teen flagged "teen").
9. **Door Pass** (TW-647): device keys, minting, nonce registry, verify
   endpoint with three result states, door photo behind its legal gate,
   venue domain (`dps_`), signage kit, scan tolls (TW-653).
10. **Retention schedule conflict fix** (queued from TW-654, Angela):
    the spec Section 6.3 retention table conflicts with Colorado
    HB 24-1130 for biometric identifiers (destruction no later than 24
    months after the member's last interaction). The corrected rule is
    carried in design.md's change list; Rosa verifies it against
    counsel's text before the Phase 2 build writes any retention job.

Phase 2 (designed, not in this build): teen tier and school-ID verification
(spec Sections 5.10, 5.12), native apps, push beyond the v1 evaluation.

## Launch gates (hard, in order)

1. **Rosa review of this package** before implementation code merges.
2. **Verification benchmark gate** (spec Sections 5.3, 5.5; harness TW-649):
   end-to-end verify accuracy and false-match / false-non-match rates
   measured on our own stack and **broken out by demographic group**, written
   into a launch evidence report. Planning target: false-match rate at or
   below 1 in 10,000; any group materially worse blocks the T2 launch until
   threshold policy, model swap, or review-path design closes the gap and a
   re-measurement passes. **NO VENDOR FALLBACK, ever** (decided 2026-10-20):
   there is no paid bridge and no interim vendor check. The engine passes
   its benchmark on its own, or the launch waits.
3. **Angela consent gate** (TW-654; spec Sections 5.7, 12): the engine
   captures **no production face, ever**, until Angela signs the BIPA-class
   consent flow and the retention and destruction schedule in writing. The
   benchmark harness runs on synthetic and properly consented or public test
   data only, so the gate never blocks measurement, only production capture.
   Door photo capture (TW-647 Section 6.3) carries its own separate consent
   gate. The remaining TW-654 items gate as Angela's package states them,
   in the same words as design.md: the liquor-law position gates venue
   sales claims and the venue pilot; the callout line gates member-facing
   copy; law-enforcement process wording gates the disclosure runbook;
   the meetup liability posture (TW-654 D6, terms allocation adopted;
   D7, insurance conversation held) gates the public launch; and the
   home-event copy gates gate the first home event, which can occur in
   soft launch.
4. **Density gates** (spec Section 9): soft launch at 500 T2-verified
   members and 10 seeded groups (public venues plus close-friends-scoped
   home events, per the home-hosting decision); public Denver launch at
   2,500 verified-active members, soft-launch weekly fill rate at or
   above 70 percent, the ambassador program recruited and vetted so the
   review SLA is staffed, benchmark gate passed, and Angela's launch
   items cleared (TW-654 D6 terms allocation adopted, D7 insurance
   conversation held), matching tasks.md item 41.

## Out of scope

- Merging anything under this ticket. This package is docs; merges happen per
  companion ticket, each with Rosa's review and Justynn's merge word.
- Teen tier build, school-ID pipeline, school registry population (phase 2;
  the band machinery that ships is band-generic by design).
- Native mobile apps, wallet passes (TW-647 v2 candidate), marketplace,
  ticketing, attendee payments, dating features, algorithmic ranking,
  machine-learned trust model: all out per spec Section 11.
- Paid verification vendors in any path. NO VENDOR FALLBACK (decided
  2026-10-20): the engine passes its benchmark on its own or the launch
  waits; there is no bridge, interim or otherwise.
- Setting organizer prices (Justynn's launch decision; primitives ship
  price-free).

## Acceptance

- This package (proposal, design, tasks) exists under
  `openspec/changes/tw-646-twistor-social/`, follows the repo's existing
  change conventions, and traces every build phase to its spec section and
  companion ticket.
- Launch gates are stated as blocking conditions, not aspirations.
- The spec Section 6.3 retention conflict (Colorado HB 24-1130) is
  carried in design.md as a change-list item with the corrected rule
  stated, for Rosa's verification against TW-654.
- Rosa reviews and approves the package; the build tickets proceed in the
  Section 13 sequence only after that approval.
