# TW-673 Evidence: Twistor Social safety check-in loop

Prepared by Twistor Holdings LLC

## 1. Source

Ticket TW-673 (reporter Justynn, high): Section 13 next slice after TW-660
(merged 0beb8e2). Spec: `twistor-lab/specs/TW-646-real-social-network-
architecture.md` Section 7 (7.1 geo arrival, 7.2 encounter confirms,
7.3 welfare, 7.4 write-ups, 7.5 trust model, 7.6 scheduling). Standing
thresholds: `twistor-lab/brand/twistor-social-reward-catalog.md` as
implemented by the merged TW-650 pure functions in `trust.py`.
Branch `zeke/tw-673-checkin-loop`, own worktree, off main @ 0beb8e2.
No schema migration: every loop table already exists in
`migration_social_v1.sql` (TW-650), so this slice cannot collide with
Dre's in-flight TW-661 RSVP schema work.

## 2. Task report

**Stage 1 geo arrival (verdict only).** `service.create_checkin` accepts
a geo verdict from a member with a going RSVP (the host is an implicit
attendee). The geofence is evaluated on-device per spec; the wire model
has no coordinate field and `rsn_checkins` has no coordinate column,
and a test asserts no coordinate-shaped key exists on the stored row.
Outside verdicts are refused, not recorded. Arrival window: start minus
30 minutes through event end, enforced on server receipt; the verdict's
own `observed_at` is freshness-bounded on both ends (not over 5 minutes
in the future, not before the window opened). The response carries an
arrival class (early / on_time / late) since the schema has no column
for it. Ledger: `checkin_completed` at weight 2 (geo_soft) or 3
(host_confirm), per the proof-quality row of the Section 7.5 input
table. Duplicate check-in returns the existing row with no second
ledger write.
QR check-in is refused at the service (fix round, Section 6 below):
Section 7.1's strong proof is the host's issued venue QR code, no
token registry exists in the merged schema, and any non-empty string
from the member's own client was being recorded at weight 3. Until
the registry lands (TW-676), `create_checkin` refuses qr_strong
outright; the token value is never stored either way.
RED: 46 failed / 1 passed at 3c6617a (the 1 pass is the pre-existing
CheckinCreate coordinate-refusal contract). GREEN: 47/47.

**Host confirm (design call 1).** `models.CheckinCreate` states a
host confirmation "carries nothing the client asserts," so a member
cannot create one for themselves: `create_checkin` refuses
host_confirm, and `service.host_confirm_checkin` (host-only, new
route `POST /events/{id}/attendees/{user_id}/host-confirm`) stamps
the attendee's arrival. This is also the only arrival path for a
restricted-band member (Section 7.5: restricted loses reveal, can
still attend public-venue meetups where the host confirms them).

**Stage 2 encounter confirms.** Confirmer must be checked in,
subject must be on the roster (RSVP, check-in, or host), self-confirm
and blocked pairs refused (blocks answer NotFound, Section 8).
The model enforces the Section 7.2 sequence: `identity_matched`
must be None when `met_them` is false, so a didn't-meet confirm can
only ever write the no-show input, never the identity strike (fix
round, Section 6). met_them=true + identity_matched=true writes
`identity_confirm_positive` (+1) on the subject. met_them=false
against a subject with no check-in writes `no_show` (-3, deduped by
event). identity_matched=false writes `identity_strike` (-10) and
opens the verification review (design call 2), then recomputes the
subject's standing: one strike moves the band to watch, two to
restricted, both proven by tests. Identical re-confirm is idempotent;
a changed re-confirm is a Conflict.

**Stage 3 welfare.** `prompt_welfare_check` records a prompt row;
`respond_welfare_check` answers the oldest open prompt (or records a
safety-button response directly). Rows are enums + timestamps only.
Escalation record shape (for the Phase 6 safety console):
`{reason, help_options, notify_trusted_contact,
evidence_packet_freeze_requested, dispatches_rescue: false,
recorded_at}`. Reasons: need_help, silent_alert (freeze requested),
no_response (two unanswered prompts, via `welfare_escalation`).
The record never claims rescue is dispatched (spec hard rule).

**Completion + Stage 4 write-ups.** `complete_event` (host only)
closes the session: going RSVPs with no arrival become `no_show`
rows + ledger events (deduped against encounter-stage no-shows),
and every attendee's standing is recomputed. `create_review`
requires a completed event and a reviewer's check-in; a person
review's subject must have completed the session too (check-in or
host, Section 7.5; an RSVP alone is not attendance). One live
review per (event, reviewer, subject) triple with the latest
submission counting (update in place, Section 7.5) in every
projection: an edit writes a compensating `review_received` event
carrying the weight delta against the same review row and
recomputes the subject, so the ledger, the stored score, and the
summary rate can never disagree (fix round, Section 6). Person
reviews write `review_received` (+1 / -1 / 0 by would_meet_again)
on the subject. `my_review_summary` returns the member's own aggregates
only: counts and rates, no reviewer identity, no free text.
Event reviews carry subject_id NULL (the schema FK points at
users); guest-pass reviews key on the pass.

**Standing recompute (Section 7.5 application order).**
`recompute_member_standing` projects check-ins, encounter confirms,
reviews, vouches, and the ledger into `trust.MemberStats`, runs the
merged pure functions (earned tier, then dormancy step-down, then
band), and upserts `rsn_trust_scores`. Completed night = completed
event + own check-in + corroboration (host_confirm proof, an
encounter confirm received with met_them true, or hosting with
another verified attendee checked in). The score is the ledger sum:
every point is explainable from append-only rows. Tests prove: a
stored trust row claiming Keyholder with no records recomputes to
Verified; five corroborated nights earn Regular; uncorroborated
geo-only check-ins earn no nights; dormancy steps Fixture down to
Regular after the grace math while verification itself is untouched.
Nothing in the projection reads payments, posts, or activity other
than showing up; standing is never purchasable by construction.

**Scheduler tick (Section 7.6).** `sessions.run_tick` no longer
raises: at a published event's midpoint it records one welfare
prompt per checked-in attendee (idempotent across ticks) and
reports due counts for the arrival, encounter, welfare, and
write-up Notify workflows. Records are state, not sends.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Geo verdict inside creates a verdict-only check-in + ledger weight 2; no coordinate key on the stored row | test_social_checkin_loop.py::TestGeoArrival | RED | PASS | RED 46F/1P at 3c6617a, GREEN 47/47 at d447cba |
| 2 | Outside geo verdict, ghost, no-RSVP, waitlisted, banned, restricted (self path) arrivals refused; under-band answers 404 | TestGeoArrival / TestHostConfirm | RED | PASS | 47/47 |
| 3 | Arrival window enforced (start-30m to end) on receipt; observed_at freshness-bounded (not >5m future, not before the window opened); duplicates idempotent with one ledger write | TestGeoArrival | RED | PASS | 52/52 after fix round |
| 4 | QR assertions are refused at the service until the venue token registry lands (TW-676): a self-typed string is never recorded as check-in proof at any weight; the token value is never stored | TestGeoArrival | RED | PASS | 52/52 after fix round; Rosa M3 probe test |
| 5 | host_confirm cannot be self-asserted; host-only door path works incl. for restricted-band attendees | TestHostConfirm | RED | PASS | 47/47 |
| 6 | Positive confirm +1 to subject; met=false without check-in = no_show only (identity_matched must be None when met_them is false, model-enforced, so a didn't-meet confirm can never write a strike); identity=false after a meeting = strike -10 + review opened + band watch, two strikes restricted | TestEncounterConfirms | RED | PASS | 52/52 after fix round |
| 7 | Welfare prompt/response records enums only; need_help and silent_alert escalate with help options and no rescue claim; two unanswered prompts escalate | TestWelfare | RED | PASS | 47/47 |
| 8 | Completion flips silent going RSVPs to no_show exactly once; only the host completes | TestCompletionAndReviews | RED | PASS | 47/47 |
| 9 | Reviews require completion + reviewer check-in + subject who completed the session (check-in or host); latest per triple counts once in row, ledger, stored score, and summary; a review edit writes the compensating ledger delta and recomputes; subject summary is aggregates only | TestCompletionAndReviews | RED | PASS | 52/52 after fix round |
| 10 | Standing derives only from showing-up records; claimed tiers are overwritten; dormancy steps tier, never verification | TestStandingRecompute | RED | PASS | 47/47 |
| 11 | Tick records midpoint welfare prompts exactly once; nothing before midpoint | TestTick | RED | PASS | 47/47 |
| 12 | No regressions: full suite 1349 passed / 20 failed / 7 errors vs same-sandbox clean-main control at 0beb8e2 1297 / 20 / 7, failed+error node sets byte-identical (27 nodes, diff empty), passed delta exactly the 52 tests in the new file | pytest full run + node-set diff | unit | PASS | diff empty, two verification runs |
| 13 | Lint bar: ruff check + ruff format --check clean on all touched files | ruff | unit | PASS | run at GREEN |

## 4. Coverage and known gaps

- Coverage: the loop's service paths are covered by 47 new tests;
  router wiring for the five new routes is covered by the existing
  route-subset contract (EXPECTED_ROUTES is a subset assertion, so
  additions cannot silently break it); no HTTP-level loop tests,
  matching the TW-660 slice's service-level bar.
- QR token validation against a venue-issued rotating token: no
  token registry exists in the merged schema. Rosa's TW-673 M3
  ruling: an unvalidated self-assertion is not mergeable as strong
  proof, so the service now refuses qr_strong check-ins outright
  until the registry lands (refusal shape, her option b; the soft-
  weight shape would still store a `qr_strong` row claiming a proof
  quality never established, and the schema CHECK constraint bars a
  non-claiming label without a migration). The registry (host-
  issued, rotating, single-use) is filed as TW-676 in Dre's schema
  lane alongside TW-661; QR reliance at strong weight stays blocked
  until it lands and Rosa verifies it.
- Design call 2 (review flag): an identity strike's review record
  is the strike ledger event + the confirm row, consumed by the
  Phase 6 safety console. `vfy_signals` is session-bound bronze in
  the verify service and RSN does not write there; if Rosa wants a
  dedicated rsn_ review-queue table, that is a schema slice.
- Guest passes (Section 7.8) are not built in any slice yet, so the
  recompute's obligated set omits guest slots and guest-pass
  reviews have no passes to attach to in production data; the
  review path validates a pass when one exists.
- Welfare escalation effects (Notify send to a trusted contact,
  evidence packet freeze) are record shapes only; the sends and
  the packet machinery belong to the safety console slice.
  Trusted-contact management (rsn_trusted_contacts) is likewise
  that slice's surface.
- Notify delivery of the four loop prompts is reported by the tick
  as due counts; the send wiring is the Notify lane's next step.
- Strike weighting by reporter standing and independence (Section
  7.2 pattern escalation) is staff-side pattern detection over the
  ledger; the flat -10 weight is the planning value this slice
  ships, named here so Rosa can rule on it.
- Collectibles derivation (Section 7.9) reads the same records and
  lands with the tick's later jobs, not this slice.

## 5. Plan-safety notes

No untrusted plan content was found in the spec, catalog, ticket, or
codebase while working. Nothing in this diff reads credentials,
makes network calls, or writes outside the social package's tables.
The one interpretation recorded, per the plan-as-data rule: where
the spec text and the merged model contract conflict on who may
create a host_confirm check-in, the model contract (nothing
client-asserted) governed, producing design call 1 above.

## 6. Fix round: Rosa review REQUEST CHANGES (2026-10-21)

Rosa's review (`docs/reviews/TW-673-review.md`) verified the privacy
core, access control, tick, and node-set bar, and returned three
majors plus two bundleable minors, all probe-proven. Fix round TDD:
regression tests quoting her probes written first, RED 6 failed /
46 passed (the met=false + identity=None no-strike pin already
passed, as it should: it preserves correct behavior), then fixes,
GREEN 52/52 in `test_social_checkin_loop.py`.

- **M1 (review edits ledger-invisible).** `create_review`'s update
  path patched the row and returned early: no ledger event, no
  recompute, so the stored score kept the first submission forever
  while the summary read the latest. Fix: a person-review edit
  writes a compensating `review_received` event carrying the weight
  delta (REVIEW_WEIGHTS[new] - REVIEW_WEIGHTS[old]) referencing the
  same review row, then recomputes the subject; delta 0 still
  recomputes so every projection stays reconciled. Why it slipped
  through: `test_latest_review_for_a_triple_counts_once` asserted
  rows and summary only; it now also asserts the ledger weights
  ([+1, -2] across a True-to-False edit), and
  `test_review_edit_reconciles_ledger_score_and_summary` replays
  Rosa's probe end to end (stored score 3 after True, 1 after the
  edit, explicit recompute 1, summary rate 0.0).
- **M2 (didn't-meet confirm could write an identity strike).**
  Fix at the model boundary, mirroring CheckinCreate's conditional
  validation: `EncounterConfirmCreate` now raises unless
  `identity_matched` is None when `met_them` is false (both the
  false and the silently-dropped true mirror are refused). The
  service double-fire is unconstructable; regression tests cover
  the model refusal and the service-level guarantee that a
  didn't-meet confirm writes only the no-show input. The intended
  adjacent case is unchanged: met=true + identity=false against an
  RSVP'd subject who never checked in still writes the strike (the
  impersonation signal Section 7.2 exists to catch).
- **M3 (qr_strong unvalidated at strong weight).** Rosa's ruling:
  not mergeable as strong proof; record at soft weight under a
  non-claiming marker, or refuse at the service until the registry
  lands. Shape taken: refusal. The soft-weight shape still stores
  a `qr_strong` row (the schema CHECK constraint fixes the label
  set, and a migration belongs to Dre's lane), and the row itself
  is the false claim; refusal keeps the ledger and the check-in
  table honest with zero schema surface. `create_checkin` refuses
  qr_strong with a message naming the two live arrival paths, and
  `CHECKIN_WEIGHTS` no longer carries a qr_strong entry at all, so
  no code path can weight it. The venue token registry follow-up
  Rosa specified is filed as TW-676 (Dre's schema lane, alongside
  TW-661). The replaced test asserted the disproved claim; its
  successor replays her probe verbatim (two members, same
  arbitrary string, both refused, no rows, no ledger events).
- **m1 (observed_at freshness).** Cheap, bundled: a geo verdict
  whose `observed_at` predates the arrival window's opening is
  refused with Conflict (the future bound already existed). The
  verdict's own timestamp is now bounded on both ends, so the
  evidence doc no longer needs an "informational" caveat.
- **m2 (person reviews of RSVP-only subjects).** Cheap, bundled:
  the roster test in `create_review` now requires subject check-in
  or host; the RSVP-only clause is gone. A checked-in reviewer
  writing a scored review of someone who never arrived now gets
  NotFound, and no review row or ledger event is written.
- **m3 (no action):** encounter confirms carry no time bound;
  noted for the Phase 6 tuning pass per Rosa, not this slice.

Rulings accepted without change, per the review: the review-flag
record (strike ledger event + confirm row) stands, Phase 6 owns
review state; guest-pass omission stands; welfare effects stay
record shapes; the flat -10 strike weight stands as the planning
value.

Verification after the fix round: check-in loop file 52/52; social
+ models files 80/80; full suite 1349 passed / 20 failed / 7 errors
against the same-sandbox clean-main control at 0beb8e2 (1297 / 20 /
7), failed+error node sets byte-identical (27 nodes, diff empty),
passed delta exactly the 52 tests in the new file. One intermediate
full run showed 21 failed / 1348 passed with no node captured; the
two verification runs bracketing it reproduce the control node set
exactly, and the standing baseline includes timing-sensitive
HTTP-endpoint suites, so that run is recorded here as an
environment flake rather than silently dropped. Ruff check and
format clean on all three touched files (`models.py`, `service.py`,
`test_social_checkin_loop.py`).
