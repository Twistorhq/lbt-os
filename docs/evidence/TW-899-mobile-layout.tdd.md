# TW-899 Evidence: Mobile App Shell Layout Fix

Prepared by Twistor Holdings LLC

## Problem
Justynn reported 2026-11-02 (phone screenshot): the demo workspace rendered the desktop layout squeezed onto a phone — sidebar occupying the left two-thirds, content crushed into a one-word-per-line strip, and a circular sign-in element overlapping the sample-data banner. Reproduced at phone viewport before the fix (evidence: twistor-lab/evidence/tw-899/before-mobile-justynn-screenshot.jpg).

## Change (branch sofia/tw-899-mobile-layout @ 05fb01c, merged to main @ bcf9b02)
- `frontend/src/components/layout/Layout.jsx`: below the `md` breakpoint the sidebar becomes an off-canvas drawer (fixed, translated off-screen and invisible when closed); main content is `min-w-0 flex-1`; drawer closes on Escape, backdrop click, and route change.
- `frontend/src/components/layout/Sidebar.jsx`: drawer behavior props with safe defaults; 44x44px toggle and close targets with aria labels, expanded, and controls wiring.
- `frontend/src/components/SampleBanner.jsx`: banner text flexes (`min-w-0 flex-1`); the sign-in call to action goes full width on small screens inside the card instead of overlapping.
- `frontend/src/components/layout/Layout.test.jsx`: 3 new regression tests (closed-state classes plus `md` overrides, open/close interaction, banner render).
- Desktop is unchanged: `md:` overrides restore the prior sidebar geometry exactly (sticky, h-screen, w-64); verified visually at 1440px.

## Verification
- Rosa Delgado reviewed and APPROVED: inspected all four evidence screenshots, ran the suite herself (19 files, 158/158 passed), and confirmed `vite build` succeeds.
- Evidence shots: twistor-lab/evidence/tw-899/ (before, after-mobile-390, after-mobile-drawer-390, after-desktop-1440).
- Post-merge test run on main recorded on ticket TW-899.

## Known non-blocking nits (logged on TW-899)
Drawer close snaps instead of sliding out; no focus move into the drawer on open; pre-existing ~27px sidebar group toggles untouched by this diff.
