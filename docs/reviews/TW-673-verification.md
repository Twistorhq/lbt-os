# TW-673 Spot-Verification: check-in loop fix round

Prepared by Twistor Holdings LLC
Reviewer: Rosa Delgado, Chief Quality Guardian
Builder: Zeke Okafor. Fix commit `eafc7cf` on `zeke/tw-673-checkin-loop`.

## Verdict: APPROVE

All three majors and both bundled minors from my REQUEST CHANGES
review are closed, verified by my own execution: ten probes I wrote
myself (preserved beside this note as `TW-673-verification-probes.py`),
the builder's 52-test file re-run, and a full-suite node-set diff
against a clean-main control I ran in this sandbox. The guarantees
table no longer claims anything my round-1 probes disproved.

## M1 — review edits reconcile (closed)

The update path writes the compensating `review_received` event
carrying `REVIEW_WEIGHTS[new] - REVIEW_WEIGHTS[old]` against the same
review row, and recomputes on every person-review edit (the event is
skipped only when the delta is 0). My probes beyond the builder's
replay:

- False→True edit: ledger weights [-1, +2], stored score 3,
  explicit recompute 3, summary rate 1.0, one review counted.
- Same-value edit (True→True): no compensating event written, ledger
  unchanged, score unchanged. Delta-0 writes no phantom, and the
  unconditional recompute keeps the projections reconciled anyway.
- None→True edit (weight-0 review carried by `good_person`, since an
  all-empty person review is refused at insert): weights [0, +1],
  score 3.

Ledger, stored score, recompute, and summary rate now agree in every
direction an edit can move.

## M2 — didn't-meet can never strike (closed)

`EncounterConfirmCreate` refuses both incoherent mirrors at the model
boundary (met_them=false with identity_matched False or True both
raise). Service level, my probes: met=false + identity=None writes
exactly one no_show and zero strike rows, and the subject's band is
untouched. The intended adjacent case is preserved: met=true +
identity=false against an RSVP'd subject who never checked in still
writes the -10 strike and opens the review.

## M3 — qr_strong refused, totally (closed; refusal shape judged sound)

`create_checkin` refuses qr_strong and `CHECKIN_WEIGHTS` no longer
carries a qr_strong entry at all (my probe asserts the table is
exactly {geo_soft: 2, host_confirm: 3}), so no code path can weight
an unvalidated assertion. The refusal message names the two live
paths (geo verdict, host confirmation), my probe confirms both
refusal and message. The builder's probe-verbatim test (two members,
same arbitrary string, both refused, no rows, no ledger events)
passes in the 52.

Judgment on the shape choice: refusal over soft weight is correct,
and the reasoning holds. A soft-weight row would still store the
label `qr_strong`, and that label is itself the false claim; the
schema CHECK constraint fixes the proof_type label set, so a
non-claiming marker needs a migration that belongs to the schema
lane. Refusal keeps both the check-in table and the ledger honest
with zero schema surface. TW-676 (venue QR token registry) exists,
is assigned to Dre alongside TW-661, and is scoped as I specified:
host-issued, rotating, single-use, event-bound tokens validated
server-side; qr_strong restored at weight 3 only behind registry
validation; the token value itself still never stored.

## Minors (both closed)

- m1 observed_at freshness: bounded on both ends. My boundary
  probes: a verdict observed exactly at the window opening
  (start-30m) is accepted; one minute earlier is refused with
  Conflict and no row is written. The builder's probe (observed
  start-3h, submitted in-window) is refused in the 52.
- m2 RSVP-only subject: `create_review`'s roster test is now
  subject check-in or host; the RSVP clause is gone. The builder's
  test (checked-in reviewer, RSVP-only subject, completed event)
  answers NotFound with no review row and no ledger event.

## Suites and the regression bar

- Check-in loop file: 52/52 in my run. My 10 probes: 10/10.
- Full suite, my runs in this sandbox with
  `--continue-on-collection-errors`: branch at eafc7cf 1359 passed /
  20 failed / 7 errors; clean-main control at 0beb8e2 (fresh worktree)
  1297 passed / 20 failed / 7 errors. **Failed+error node sets
  byte-identical (27 nodes, diff empty).** The passed delta is 62,
  which is the builder's 52 plus my 10 probe tests present in the
  tree during the branch run; with the probes removed (they now live
  beside this note, outside tests/) the delta is exactly the claimed
  52. Zero new failures.
- On the disclosed intermediate flake (one run at 21 failed / 1348
  passed, flipped node not captured): accepted as disclosed. The two
  bracketing builder runs reproduce the control set exactly, and my
  own fresh branch run in this verification reproduces it a third
  time (20/7, identical node set) on the first attempt. Three clean
  reproductions against one uncaptured intermediate; no further run
  required.
- Ruff check and format clean on models.py, service.py, and
  test_social_checkin_loop.py in my run.

## Evidence doc and standing rulings

The guarantees table reads true now: #4 states the refusal guarantee
(the round-1 "QR token proves arrival at weight 3" claim is
withdrawn, not edited around), #3 carries the two-ended freshness
bound, #6 carries the model-enforced identity rule, #9 carries
subject-completed plus ledger reconciliation in every projection.
Section 6 records the fix round against each finding accurately,
including why M1 slipped through (rows-and-summary-only assertions)
and the M3 shape reasoning. My other round-1 rulings stand
untouched, confirmed in the doc's own words: the review-flag record
(strike event + confirm row, Phase 6 owns review state), the
guest-pass omission, welfare effects as record shapes, and the flat
-10 strike weight as the planning value. None were reopened.

One process note, same as my TW-665 verification: the builder's
disclosure of the uncaptured flake, recorded on the ticket and in
the evidence doc rather than dropped, is the right instinct and I
am scoring it as such.

The ticket stays in-progress: the merge still needs Justynn's word.
