# TW-661 Review: RSVP opens-at, held-seat accounting, couple-RSVP RPC

Prepared by Twistor Holdings LLC
Reviewer: Rosa Delgado, Chief Quality Guardian
Builder: Dre Coleman. Branch `dre/tw-661-rsvp-schema`, worktree
`~/workspace/worktrees/dre-tw661-rsvp`, base main @ 0beb8e2.
Checkpoints: RED `1c1e33a`, GREEN `4c3f788`, evidence `49d46c9`.

## Verdict: REQUEST CHANGES

Everything Dre claimed reproduces exactly under my own execution,
including the live-Postgres probes and the full-suite node-set
diff. The schema work is solid and the accounting rule holds
everywhere I probed it. One major: a couple RSVP walks the
lower-standing partner through a wave door this ticket built,
before the partner's own window opens, and stamps the partner's
row with the requester's wave label. The wave system is this
ticket's core deliverable; half the rows it can create currently
bypass it.

## Major

### M1: the couple path never checks the partner's wave window, and labels the partner's row with the requester's wave

`request_rsvp` computes `_rsvp_opening` from the requester's
context only. The partner is validated for voice, band,
visibility, restricted band, and an existing RSVP (all TW-660
gates, all holding), but never for the partner's own opening.
Both rows are then written with the requester's single
`rsvp_window`, in the RPC parameters (`p_rsvp_window`) and in the
fallback `_create_rsvp_row` call alike.

My probe (temporary test file, run and removed): event opens at
NOW + 24h, requester is a Keyholder, partner is Verified. Solo,
the partner is refused at NOW with the wave ConflictError, as
designed. Through the couple path at the same moment, the
partner's row lands as `('going', 'keyholder')`.

Against the spec: Section 7.7 opens windows "ahead of it by
tier," has waitlist signup obey the same windows, and records
"the admitting wave" on the row. The partner's row was admitted
by no wave the partner holds, and its label claims the Keyholder
wave did it. Against this ticket's own evidence: "waves refuse
early, admit per standing" and truthful window labels hold for
solo rows only.

Fix shape: compute the partner's opening from `partner_ctx`
through the same `_rsvp_opening`; refuse the pair (409, zero
rows written) while `now` is before the partner's opening; label
each row with its own admitting wave in both the RPC and the
fallback path. Migration note: if the RPC signature changes,
DROP the old overload explicitly in the migration; CREATE OR
REPLACE with a different parameter list creates a second
function and leaves the 9-argument original callable.
Regression tests: partner refused before their own window even
when the requester's window is open; partner admitted inside
their own window carrying their own tier label; RPC and fallback
paths identical on both.

## Minors

- **m1: `_try_couple_rpc` swallows every exception silently.**
  `except Exception: return None`, no log. A broken or undeployed
  RPC would be invisible indefinitely while the fallback quietly
  carries production. Fold a warning log into the fix round; the
  function is being touched anyway.
- **m2: the migration header overclaims the advisory lock.** The
  lock serializes the pair writes, but the capacity statuses are
  computed at the service layer before the RPC is called, so the
  lock cannot make two racing couples' capacity decisions atomic.
  That is consistent with the rest of the core loop (capacity is
  service-advisory everywhere), but the comment should say what
  the lock actually does. One-line wording fix; non-blocking on
  its own.

## Named-gap rulings

- **G1, the `_try_couple_rpc` non-2-row fallback: ACCEPTABLE as
  scoped.** PostgREST returns both rows or an error, so the path
  is defensive only; if it ever fired after a real write, the
  UNIQUE(event_id, user_id) constraint rejects loudly rather than
  corrupting silently. See m1 for the adjacent logging point.
- **G2, host hold create/convert API deferred: ACCEPTABLE.** The
  ticket asked for accounting and release rules in schema, wired
  into capacity decisions; delivered and probed. Requirement
  recorded for the follow-up slice: it must carry Section 7.7's
  host-held policy (hold back at most 20 percent of capacity,
  unclaimed held seats release to the waitlist 24 hours before
  start). The schema as written supports both.
- **G3, no scheduler wiring for the global expiry sweep:
  ACCEPTABLE.** Every capacity consult settles expired holds
  first (initial RSVP, host approval, waitlist promotion; all
  probed), so no decision can be made on a stale expired hold.
  The function already accepts a NULL event id in the sweep
  shape the future job needs.

## Edge probes I ran (beyond Dre's list)

- **Wave boundaries, exact:** Keyholder at opens minus 72h
  admitted, labeled 'keyholder'; one minute earlier refused.
  Fixture at minus 24h and Regular at minus 12h admitted with
  their own labels. Verified at exactly the general opening
  admitted, labeled 'general'; a Keyholder at the general
  opening is also labeled 'general'. PASS.
- **Held seat expiring mid-waitlist-promotion:** a hold expiring
  between waitlist signup and promotion settles during the
  promotion consult and frees its seat (waiter promoted, hold
  row 'expired'); a still-live hold blocks promotion (waiter
  stays waitlisted). PASS both directions.
- **Couple RSVP with a band-invisible partner:** partner in the
  18-20 band, event rated 21 plus. The pair raises NotFoundError
  and writes zero rows for either partner. Fail-closed holds.
  PASS.
- **Seat-hold release on event cancellation:** there is no event
  cancellation operation anywhere in the service layer or the
  routers for rsn_ events (grep-verified; 'cancelled' exists only
  in the TW-651 public events index and as an unused value in the
  rsn_events status CHECK). There is no cancellation path for
  holds to release against in this slice, so this is not a
  TW-661 defect. Requirement recorded for the future cancel
  slice: cancelling an event must settle its holds; the
  ON DELETE CASCADE on the holds table covers hard deletes only.

## Verified by my own execution

- New tests 24/24 (migration contract 12, service behavior 12).
  Scoped suites (social + verify + events): 547 passed.
- RED was real: at `1c1e33a` the two new test files give
  11 failed, 1 passed, 12 errors, exactly as the evidence doc
  states.
- Full suite: branch 1321 passed / 20 failed / 7 errors; clean
  main in the same sandbox 1297 / 20 / 7. Failed-plus-error node
  sets byte-identical (27 nodes, diff empty; both sets captured
  by me). Zero new failures.
- Live PostgreSQL 16.2 (pgserver harness copied to /tmp, run as
  `nobody`, stub `idn_users`, no-op pgcrypto shim in the copied
  tree only; scratch deleted after): verify v1, social v1, then
  v2 applied; v2 run a second time clean, so the migration is
  idempotent on a real server, not just re-runnable on paper.
  Columns and all three functions present.
- Couple RPC on the real DB: happy path returns 2 rows. Dre's
  probe shape reproduced: pairing a fresh user with a partner
  who already holds an RSVP errors inside the RPC and leaves the
  requester with 0 rows. A same-user pair also errors; the table
  total is unchanged. Atomicity is a database property, not a
  service hope.
- Hold accounting on the real DB: with a live hold of 2 and an
  expired-unswept hold of 3, `rsn_active_held_seats` returns 2;
  the sweep settles exactly 1 and stamps `released_at`; active
  is still 2 afterward. CHECK discipline: all four violations
  rejected (held with released_at stamped, released without
  released_at, seats = 0, status outside the enum). RLS:
  rowsecurity on, the deny-all policy present, and a non-owner
  role holding a SELECT grant sees 0 rows.
- Wave offsets match the reward catalog exactly (Regular 12h,
  Fixture 24h, Pillar 48h, Keyholder 72h; Verified at the general
  opening) and Section 7.7 of the architecture spec.
- v1 untouched: the branch diff is 5 files (v2 migration new,
  service, models, two test files). Ruff check and format clean
  on every changed file.
- Observation, no action: Section 7.7 promotes "among members
  whose window has opened." That is satisfied constructively: a
  waitlist row can only be created inside the member's window
  (request_rsvp refuses earlier) and promotion happens strictly
  later, so no waiter can be promoted before their window has
  opened. The promotion code needs no extra check.

## What closes this review

1. M1 fixed with regression tests pinning both paths (RPC and
   fallback), as specified above.
2. m1's warning log folded into the same round. m2 if convenient.
3. On landing I re-run my probe set, the new tests, and the
   scoped suites; the zero-new-failures node-set invariant must
   still hold.

Nothing merges without Justynn's word.
