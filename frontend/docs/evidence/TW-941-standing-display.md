# TW-941 standing display (display half): evidence

Prepared by Twistor Holdings LLC

## What shipped

Member-profile display of current tier and progress toward the next
rung for Twistor Social (pattern 06 surface), built under the TW-652
lanes on branch sofia/tw-941-rewards-display:

- `src/lib/socialStanding.js`: the client contract (documented in
  its header), tier ladder, thresholds mirroring
  backend/app/social/trust.py, fail-closed normalizer, progress
  derivation, and contract fixtures.
- `src/lib/socialStandingClient.js`: the one live path,
  GET /social/me/standing on the shared api client, plus an explicit
  fixture client for tests and previews.
- `src/components/social/SocStanding.jsx`: the display. Verified
  renders only as a future-phase rung (parked verification phase),
  never earned, current, or earnable; activity rungs show progress
  as facts (nights, show rate, hosted rooms, reviews, streak,
  vouches, tenure) with no percentage bar; a watch or restricted
  band hides tier, ladder, and progress while the member keeps
  their own record; teen accounts cap at Pillar.
- `src/components/social/SocStandingScreen.jsx`: mobile-first
  screen wrapper with loading and error-plus-retry states. The
  default fetcher is the live contract; fixture data is only ever
  reached through an explicitly injected fixture client.
- Strings added to the social catalog in English and Spanish at
  parity (standing section). Spanish standing titles await the
  human translation review TW-646 Section 3 requires, same flag as
  the rest of the catalog.
- Styles in social.css consume only the --soc-* tokens; Ledger Gold
  marks earned and current rungs, and gold fill never carries text.

## Fixture-backed vs live

Fixture-backed today: everything rendered in tests and in the
preview, because Zeke's backend branch (zeke/tw-941-feed-rewards)
had no commits beyond main 45a845c at build time and the endpoint
is not live. The display consumes the payload only through
fetchMyStanding and the documented contract, so the backend landing
its endpoint is a no-change event for this code. Server-computed
progress is preferred when the payload carries it; the client
derivation from stats is the fallback, stated against the same
trust.py thresholds either way.

Not wired: no route mounts the screen in App.jsx. No Social
component is routed on main today (the TW-652 consent flow is in
the same state), and the profile backend is still the Phase 4 stub.
Mounting rides the profile surface when it lands.

## Tests

- New: src/lib/__tests__/socialStanding.test.js (18 tests after
  fix round 1),
  src/components/social/__tests__/SocStanding.test.jsx (14 tests
  after fix round 1).
- Targeted run (with strings parity): 38/38 passed after fix
  round 1 (28/28 at the original build).
- Full frontend suite: 21 files, 190/190 passed after fix round 1
  (180/180 at the original build; 158 on main plus 32 new), zero
  regressions.
- Build: vite build succeeded with a dummy Clerk key; the standing
  code is unreferenced by App.jsx (see Not wired), so it is not in
  the bundle, the same as every other Social component on main.
- Preview: scripts/render-standing-preview.mjs renders all four
  fixture states in both locales to
  docs/evidence/TW-941-standing-preview.html; screenshots at 1440
  and 375 in docs/evidence/tw941-shots/.

## Impeccable and unslop gate

- Detector on the new source files: no findings (regex pass; the
  element rules need a rendered page).
- Detector on the rendered preview: 12 findings at first run.
  Fixed: skipped-heading in the suspended state (the paused
  heading is now an h2 under the screen h1). Kept on purpose, with
  reasons: the re-run lands at 10 findings, all kept on purpose:
  cramped-padding x8 is the preview harness phone frame
  (a device edge, not a product container; the product screen
  carries its own 16px thumb padding), and overused-font x2 flags
  Inter and Fraunces, which are the mandated Social type pairing
  in design-system/social/tokens.css (TW-652 decision; brand board
  wins ties).
- Manual 15-tell audit of the new surface: no gradients, no
  gradient text, no glassmorphism, no pill badges (the band chip
  keeps its squared TW-652 shape), no icon-in-rounded-square, no
  identical card grids (ladder and record are lists with distinct
  fact lines), no cards in cards, no decorative side stripes (rung
  marks are gold dots, a functional state mark paired with a state
  word), no context-free hero stats (every number is the member's
  own record), no fake numbered sequences, no decorative
  animation, no em-dashes in any new copy.
- Unslop pass: no uppercase tracked micro-labels added (the one
  uppercase element is the pre-existing SocScreen step style, not
  used here), spacing stays on the existing 12px row rhythm and
  token scale, number formats are uniform (counts as "12 of 20",
  rates as whole percents), headings state facts, no greeting
  headline.

## Fix round 1 (Rosa gate: REQUEST CHANGES)

Rosa's rerun verified the original counts and rules, then returned
four must-fixes. All four are fixed on this branch; nothing she
verified clean was changed (Verified stays future-phase only,
watch/restricted still hide tier, ladder, and progress, EN/ES stay
at parity, no purchase affordances were added).

1. BLOCKER, Keyholder show rate: `THRESHOLDS.keyholder` now carries
   `showRate: 0.90` and `deriveProgress('keyholder')` states it,
   mirroring catalog Section 1 and trust.py (`_meets_keyholder`
   chains through `_meets_pillar`, `PILLAR_SHOW_RATE` 0.90). The
   contract test that enshrined the wrong exact key list now asserts
   the corrected list (nights, showRate, wmaRate, wmaReviews,
   hostedClean, streak, tenureDays) and the 0.90 target itself.
2. BLOCKER, show-rate leak below 5 decided RSVPs: a showRate
   condition is now omitted in `deriveProgress`, dropped from
   server-supplied progress in `normalizeStanding`, and filtered
   again at render in `SocStanding.jsx`, all gated on
   `SHOW_RATE_MIN_RSVPS`. Below 5 decided there is no current rate
   and no Done anywhere; the newMember fixture (4 decided, 75
   percent) renders no showRate condition and no "75 percent" text.
   The regenerated preview HTML contains no "75 percent".
3. MAJOR, fail-open band: `normalizeStanding` no longer defaults an
   unknown or missing band to `good_standing`. An off-vocabulary
   band normalizes to null, so `fetchMyStanding` throws and the
   screen renders its error state, matching the TW-652 M1
   no-default-band doctrine.
4. MAJOR, cross-field consistency: the normalizer, not just the
   display, now enforces it. A teen tier of keyholder caps to
   pillar and a teen next tier is never keyholder (catalog
   Section 7); a declared `next_tier` is accepted only as the
   immediate successor of the normalized current tier (null at the
   top and while suspended), and a skipping, repeating, or
   reversing declaration is disregarded in favor of the computed
   successor, with any server progress that arrived with it
   disregarded too. The component's closing line for a capped teen
   is the teen cap note, not the adult top-tier line.

Tests after fix round 1: 10 tests added or corrected in place
(contract 12 to 18, component 10 to 14; each must-fix has at least
one contract assertion and, where it is a display leak, a component
assertion written from Rosa's probes). Targeted run with strings
parity: 38/38 passed. Full frontend suite: 21 files, 190/190
passed, zero regressions. Vite build succeeds with a dummy Clerk
key. Preview HTML and the 1440/375 screenshots were regenerated
from the fixed code; the Impeccable preview scan is unchanged at
the same 10 documented kept-on-purpose findings (preview phone
frame x8, mandated Inter/Fraunces pairing x2), source scan clean.

## Accessibility checks

- Semantic structure: screen h1, tier h2, section h3s, ladder as an
  ordered list, conditions and record as lists, every section
  labelled by its heading.
- State is never color alone: each rung carries a state word, each
  condition a fact plus a status word, the band chip is text.
- Contrast inherits the TW-652 verified token pairs (tier text
  Cafecito on Paper 5.49 to 1 AA; Ledger Gold is a mark only).
- The error state offers a real button at primary thumb height;
  the loading state is a status region.

Prepared by Twistor Holdings LLC
