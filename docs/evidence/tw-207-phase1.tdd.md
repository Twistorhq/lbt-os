# TW-207 Phase 1 evidence report — Morning Brief cinematic page + onboarding seam

**Prepared by Twistor Holdings LLC**

Ticket: TW-207 · Branch: `feature/tw-207-cinematic-brief` · Author: Sofia Reyes
Reviewer: Rosa Delgado · Brand: Imani Carter (approved with changes, all applied) · Date: 2026-09-24

## What this is

Phase 1 of TW-207 ("the site is the trailer; the product is the film"): a new
Morning Brief page at `/app/brief` that renders the leak-engine brief
(`GET /api/v1/leaks/brief`) as three narrative acts — What Happened / What
Will Happen / What Should We Do — plus the onboarding→brief continuity seam.
Additive only: no changes to auth, billing, signup, dashboard, or the leak
engine itself. Backend touch is 3 copy-only string edits in
`backend/app/leak_engine/detectors/hvac.py` per Imani's brand review.

## RED / GREEN history

- RED (build): vitest@5 peer-conflicted with the repo's vite 5. GREEN: pinned
  `vitest@2` + jsdom + testing-library; 8/8 component tests pass.
- RED (test): two test-side bugs — `<Link>` rendered outside a Router, and a
  double `render()` in one container leaking the SAMPLE DATA badge between
  assertions. GREEN: `MemoryRouter` wrapper + `rerender`; both were test bugs,
  not component bugs.
- RED (Rosa round 1): the branch's first commit accidentally included Zeke's
  in-flight TW-204 detector code (shared-checkout hazard) — tuple returns the
  base engine couldn't handle, crashing `/leaks/brief`. Also: `aria-live` on
  per-frame count-ups, `text-white/40` kickers below the 4.5:1 contrast gate,
  unconditional `animate-pulse` skeletons. GREEN (this round): branch rebuilt
  from the base commit — backend is now the 3 copy-only edits, verified by
  diff stat; animated numbers are `aria-hidden` with a visually-hidden live
  region announcing final values; kickers bumped to `text-white/60`;
  skeletons use `motion-safe:animate-pulse` with `role="status"`.

## Verification (run at commit time, this round)

- `npx vitest run` — 8/8 pass (`morningBrief.test.jsx`: detectorLabel mapping,
  scene narrative structure, SAMPLE DATA badge, severity edge mapping,
  hero voice line + totals, act ordering, honest empty state).
- `npm run build` (vite production) — green.
- `ruff check app tests` (backend) — clean.
- `pytest backend/tests/test_leak_engine.py` — 22/22 pass (the TW-201 suite;
  TW-204's hostile-cell tests live on Zeke's ticket/branch, not here).
- Diff stat verified at commit: backend = 3 string edits only; frontend =
  new `brief/` components, `leakApi.brief()`, route, sidebar nav item,
  onboarding seam. No other backend files touched.

## Brand gates (Imani, verified in code)

- All 4 required changes landed: day-agnostic "Your replacement call list";
  severity as edge accents only (`border-l-4`), surfaces near-black;
  scripted handoff line "The trailer ends here. Your first Morning Brief is
  ready." + first-run redirect to `/app/brief`; `detectorLabel()` — no raw
  slugs in eyebrows. Recommended: "Per industry research" attribution and the
  billing-failure reframe also applied.
- No fake metrics: hero and scenes render only API-supplied totals/counts/
  values (nulls hidden, never zero-filled); honest empty state points at
  `/app/connections`; `SAMPLE DATA` badge on `is_demo` findings.

## Known limits / Phase 2

- No scroll-driven in-app scenes yet (Phase 2, post-launch).
- `useCountUp` announces via a single live region on final values; per-frame
  announcements are suppressed by design.
- Kicker contrast bumped to `text-white/60` in new code; the site-wide
  TW-165 `text-white/40` convention may need a brand-board call (flagged, not
  changed here).
