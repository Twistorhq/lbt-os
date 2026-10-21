# TW-661 Re-verification: partner wave gate, RPC logging, header precision

Prepared by Twistor Holdings LLC
Reviewer: Rosa Delgado, Chief Quality Guardian
Builder: Dre Coleman. Branch `dre/tw-661-rsvp-schema`, worktree
`~/workspace/worktrees/dre-tw661-rsvp`, base main @ 0beb8e2.
Fix-round checkpoints: RED `28ec3c1`, GREEN `5ce73d1`, evidence
`aa8f99c`. Original review: docs/reviews/TW-661-review.md.

## Verdict: APPROVE

M1, m1, and m2 are all closed, verified by my own execution:
my round-1 probe re-run, probe variations Dre's tests do not
pin, the full-suite node-set invariant reproduced against a
fresh clean-main control, and the migration upgrade path plus
the new RPC exercised on a real PostgreSQL 16.2 server.

## M1: partner wave gate (closed)

- My probe, re-run (temporary probe file, preserved beside this
  note as TW-661-verification-probes.py, removed from tests/
  after the run): event opens at NOW + 24h, Keyholder requester,
  Verified partner. Solo, the partner is refused at NOW; through
  the couple path the pair is now refused with zero rows written
  (pre-fix the partner's row landed as ('going', 'keyholder')).
- Variations Dre's tests do not pin, both pass: (a) reverse gate:
  Verified requester, Keyholder partner, same event: refused,
  zero rows (the partner's open window does not pull the pair
  in). (b) Mid-wave: Regular requester + Verified partner at
  +13h refused (solo parity asserted at the same moment); at
  +24h both rows land, both labeled 'general'.
- Per-row labels on both paths: Dre's tests (Keyholder/Regular
  at +13h land 'keyholder'/'regular' in the RPC params, the RPC
  double's rows, and the fallback rows; RPC never invoked when
  the pair is refused pre-window) run green under my execution,
  31/31 in the two TW-661 files.
- Live DB, new RPC directly: called rsn_rsvp_couple with
  p_rsvp_window 'keyholder' and p_partner_rsvp_window 'regular';
  the table rows came back labeled 'keyholder' (requester) and
  'regular' (partner). The parameter is spent on the partner's
  row only, on a real server, not just in the double.

## Migration upgrade path (reproduced on real PostgreSQL 16.2)

pgserver harness (pip pgserver wheel copied to /tmp, run as
`nobody`, stub `idn_users`, no-op pgcrypto shim in the copied
tree only; scratch deleted after):

- verify v1 + social v1 + OLD v2 (at `4c3f788`) apply clean;
  exactly one rsn_rsvp_couple overload, 9 arguments.
- NEW v2 applied: exactly one overload, 10 arguments including
  `p_partner_rsvp_window text`; 9-argument count is 0. The
  explicit DROP removed the superseded overload.
- NEW v2 applied a second time on the upgraded database: clean,
  still exactly one overload. Idempotent.
- Atomicity with the new signature: pairing a fresh user with an
  already-RSVP'd partner errors inside the RPC and leaves the
  fresh user with 0 rows; table total unchanged.

## m1: RPC failure logging (closed)

- Exception branch: Dre's caplog test runs green (broken-RPC
  double lands the pair via fallback AND emits a warning naming
  rsn_rsvp_couple).
- Non-2-row branch, not pinned by Dre's tests: my probe double
  returns a single row without writing; the warning
  ("rsn_rsvp_couple returned 1 rows, expected 2; falling back
  to service inserts") fires and the fallback lands exactly one
  row per partner. The branch logs before falling back, as
  specified.

## m2: header precision (closed)

Read in the migration itself: the header and the in-function
comment now state that the advisory lock serializes pair writes
per event so inserts cannot interleave, and explicitly that it
does NOT make the caller-computed capacity statuses atomic;
capacity stays service-advisory, consistent with the core loop.
Accurate.

## Suites and invariant

- RED reproduced at `28ec3c1`: 6 failed / 25 passed in the two
  TW-661 files, exactly the claimed failures.
- TW-661 files 31/31. Scoped suites (social + verify + events
  file set): 554 passed (547 + 7), the 2 collection errors are
  the standing xmlsec environment defect.
- Full suite with --continue-on-collection-errors: branch
  1328 passed / 20 failed / 7 errors; my fresh clean-main
  control (0beb8e2) 1297 / 20 / 7. Failed-plus-error node sets
  byte-identical (27 nodes, diff empty). Passed delta exactly
  +31. Absolute counts sit a constant 33 below Dre's on BOTH
  sides (environment difference, same as round 1); the invariant
  and the delta reproduce exactly.
- Ruff check and format clean on every touched Python file.

## Recorded requirements

Confirmed present on TW-661, nothing lost: my review comment
(2026-10-21 16:25 UTC) records the G2 follow-up terms (the host
hold create/convert API must carry Section 7.7's 20-percent
capacity cap and 24h-before-start auto-release) and the
cancel-slice requirement (a future event-cancel operation must
settle the event's holds; ON DELETE CASCADE covers hard deletes
only). Dre's fix-round comment cross-references both rather than
duplicating them.

Nothing merges without Justynn's word. Ticket stays in-progress.
