# TW-673 Review: Twistor Social safety check-in loop

Prepared by Twistor Holdings LLC
Reviewer: Rosa Delgado, Chief Quality Guardian
Builder: Zeke Okafor. Branch `zeke/tw-673-checkin-loop` @ 9bfefd8, off main 0beb8e2.

## Verdict: REQUEST CHANGES

The privacy core of this product is real, and I verified it the hard
way: there is no coordinate to leak because no coordinate exists
anywhere in the design. The loop's four stages work, the access
control inherited from TW-660 holds on every new route, and the
test evidence reproduces exactly. Three findings block the merge,
all in the trust-record layer: a review edit is invisible to the
ledger it feeds, an incoherent confirm input writes the heaviest
strike in the system, and the QR door assertion is recorded as
strong proof when nothing has proven anything. Each contradicts a
claim in the evidence doc's own guarantees table. All three are
small, surgical fixes in one file area; everything else in this
review is an approve waiting on them.

## Verified by my own execution (not taken from the evidence doc)

**Privacy core (the product's moat).** I inspected all three layers
myself. The wire model `CheckinCreate` has no coordinate field and
`extra='forbid'` makes a coordinate payload unconstructable (test +
my read of the model and the geo module's exact-key-set validator).
The TW-650 schema for `rsn_checkins` is verdict-only: id, event_id,
user_id, proof_type, arrived_at, departed_at. My probe's stored row
carries exactly those six keys, no coordinate- or token-shaped key.
The QR token value appears nowhere in a full dump of the database
after a qr_strong check-in. A location diary is not merely unused
in this design; it is impossible in it. Claim verified in full.

**Arrival.** Outside verdict refused and not recorded; window
start-30m to event end enforced; duplicate check-in idempotent with
one ledger write; ledger weights geo 2 / QR+host 3 as coded (see M3
for the QR half). Host_confirm cannot be self-asserted and the
host-only door path works, including for a restricted-band attendee
(my probe: two strikes landed, self check-in refused, host confirm
at a second event succeeded).

**Encounter confirms.** Confirmer must be checked in; self-confirm
refused; blocked pair answers NotFound; a subject on another event's
roster only answers NotFound. Identity "no" writes identity_strike
(-10) and recomputes the subject on the spot: my probe drove one
subject from good_standing to watch on one strike and to restricted
on a second strike from an independent confirmer. "Didn't meet
them" against a no-check-in subject writes the no-show input,
deduped against completion-stage no-shows (my probe: exactly one
no_show ledger row across both stages).

**Welfare.** Stored rows are enums and timestamps only (seven keys,
verified); the model rejects free-text and latitude extras with a
validation error, so no location or note can ride this record.
Escalation shapes reproduced exactly: need_help (freeze false),
silent_alert (evidence freeze requested true), two unanswered
prompts (no_response), one unanswered prompt (no escalation), and
`dispatches_rescue: false` in every record. The record never claims
rescue. Prompts require a checked-in attendee.

**Completion and write-ups.** Completion is host-only and
exactly-once: a second completion raises Conflict and no-shows are
written once. The subject's summary is aggregates only: my probe
stored a review carrying free text and confirmed the summary exposes
five aggregate keys, no reviewer identity, and the free text appears
nowhere in it.

**Standing.** A stored trust row claiming Keyholder with a score of
500 and zero records recomputes to Verified / score 0 /
good_standing. Five corroborated nights earn Regular per the merged
trust.py; an uncorroborated geo-only night earns zero nights. Score
is the ledger sum. Dormancy steps the tier, never the verification.
On the never-purchasable red line: the only writes to
`rsn_trust_scores` anywhere in the backend are the update and
insert inside `recompute_member_standing`, and there is no payment,
billing, or admin surface in the social package that touches
standing. Standing moves only through showing-up records, by
construction. Verified by grep and by the recompute probes.

**Access control on the six new routes (TW-660 invariants).** A
ghost account (no verification state) is refused with Forbidden on
check-in, encounter confirm, welfare respond, review create, and
event completion. A member banned after RSVPing is refused on
check-in, on the review-summary read (banned gets refusal on reads,
per Section 5.11), and as the subject of a host confirm. A member
whose attestation is gone fails closed with Forbidden. A wrong-band
member (age_18_20 against a 21+ event) gets NotFound, the Section
5.9 invisibility answer, on check-in, confirm, welfare, and review:
for that member the event does not exist. Router error mapping
confirmed: Forbidden is 403, NotFound is 404.

**Tick.** I ran `run_tick` three times against one event: nothing
before midpoint, one welfare prompt per checked-in attendee at
midpoint, zero new prompts on the second tick. Due counts for all
four Section 7.6 Notify workflows are reported. Idempotent as
claimed.

**Tests and regression bar.** RED reproduces exactly: at 3c6617a
the new file runs 46 failed / 1 passed. GREEN: the new file passes
47/47 in my run; social + verify + events suites pass 570 (523 on
merged main + 47 new), consistent with the claimed social 327/327.
Full suite, my runs in this sandbox with
`--continue-on-collection-errors`: branch 1344 passed / 20 failed /
7 errors; clean-main control at 0beb8e2 in the same sandbox 1297
passed / 20 failed / 7 errors; **failed+error node sets
byte-identical (27 nodes, diff empty)** and the passed delta is
exactly +47, the new test file. Zero new failures. (Zeke's absolute
counts, 1377 vs 1330, sit exactly 33 higher on both sides than mine;
absolute counts vary by environment, the node-set identity and the
+47 delta are the invariant, and both reproduce.) Ruff check and
format clean on all touched files in my run.

## Majors (must fix before merge)

### M1 — A review edit is invisible to the ledger, so the score keeps the first submission forever

`create_review` appends the `review_received` trust event and
recomputes the subject only on the insert path. The update path
(latest-per-triple) patches the row and returns early: no ledger
event, no recompute. My probe: reviewer files would_meet_again=True
(subject ledger gains review_received +1, stored score 3), then
edits the same triple to False. The row and the summary now say the
latest answer is no (rate 0.0), but the ledger still holds only the
original +1, the stored score still counts it, and an explicit
recompute still returns score 3 against a would-meet-again rate of
0.0. The two projections of the same fact disagree permanently, and
nothing in the system ever reconciles them, because the score is
the ledger sum by construction.

Spec Section 7.5 states the rule whole: "one write-up per (event,
reviewer, subject) triple with the latest submission counting." The
evidence doc's guarantees table repeats it (#9). As built, the
latest submission counts for rates and tiers but the first
submission counts for the score. Fix: on update, write the ledger
correction (a compensating `review_received` event carrying the
weight delta, or a superseding pair, referencing the review row)
and recompute the subject; add a regression test that asserts the
ledger and the stored score after an edit, not just the row count
and the summary. The existing test
`test_latest_review_for_a_triple_counts_once` asserts rows and
summary only, which is exactly why this slipped through.

### M2 — A confirm that says "I did not meet them" can still write an identity strike

`EncounterConfirmCreate` places no condition on `identity_matched`,
and `record_encounter_confirm` acts on it independently. My probe:
met_them=false + identity_matched=false against an RSVP'd subject
with no check-in wrote BOTH the no-show (-3) and the identity_strike
(-10), and moved the subject's band, from a confirmer who by their
own answer never encountered the person. Spec Section 7.2 is a
sequence: question 2 ("Were they who they said they were?", shown
with the verified profile photo) is asked about someone you met; a
"no" on question 1 "marks a possible no-show," full stop. The strike
is the heaviest single negative in the trust model and it currently
fires on an answer the flow never meaningfully asked. The mirror
input (met=false, identity=true) is likewise accepted and silently
dropped, which shows the contract, not just one branch, is
unconstrained. Fix at the model boundary, mirroring
`CheckinCreate`'s conditional validation: identity_matched must be
None when met_them is false. Regression test for the double-fire.

For the record, the adjacent case is correct and intended: met=true
+ identity=false against an RSVP'd subject who never checked in does
write the strike (my probe). That is the impersonation signal
Section 7.2 exists to catch, and the opened review, not the strike,
is the adjudication. No change there.

### M3 — qr_strong is an unvalidated self-assertion recorded at strong-proof weight (ruling on named gap 1)

The loop may not merge with the door assertion recorded as it is.
My probe: two different members presented the same arbitrary string
("literally-any-string") as a qr_token at the same event; both were
recorded as `qr_strong` check-ins at weight 3, the top proof
weight, equal to a host's own door confirmation. Spec Section 7.1
defines the strong proof as "the host's venue QR code": an issued
artifact. What is built accepts any non-empty string from the
member's own client (the model enforces only min_length 1). The
stored `rsn_checkins` rows are evidentiary records in a product
whose moat is the evidence trail; a row that says qr_strong for a
self-typed string asserts a proof quality the system never
established. Zeke disclosed the missing registry honestly in the
evidence doc; disclosure makes it a known gap, not a true claim,
and guarantees table #4 ("QR token proves arrival at weight 3")
cannot stand as written.

Fix, either shape: (a) until the registry exists, record QR
assertions at the soft, self-asserted weight (2, the geo_soft
level) under a marker that does not claim issued-token proof; or
(b) refuse qr_strong at the service until the registry lands. The
geo and host-confirm paths are unaffected either way. Follow-up,
specified here: a venue token registry slice (host-issued, rotating,
single-use tokens) in Dre's schema lane alongside TW-661; QR
reliance at strong weight stays blocked until it lands and I verify
it. File that ticket when the fix round lands.

## Minors

- **m1 (observed_at freshness).** A geo verdict observed three
  hours before the arrival window opened is accepted when submitted
  inside the window (my probe); only a future observed_at (over 5
  minutes ahead) is refused. The stored arrived_at is server receipt
  time and the window is enforced on receipt, and idempotency caps
  the replay value at one check-in, so this is a small honesty gap
  in the verdict's own timestamp rather than a hole. Either enforce
  a freshness bound (a verdict observed before the window opened is
  not an arrival verdict) or state in the evidence doc that
  observed_at is informational.
- **m2 (person reviews of RSVP-only subjects).** `create_review`
  accepts a person review when the subject merely holds an RSVP.
  Section 7.5's would-meet-again rate is defined over reviews "where
  reviewer and subject both completed the session." A checked-in
  reviewer can currently write a scored review of someone who never
  arrived. Tighten the roster test for person reviews to subject
  check-in or host, or filter in the recompute projection; bundle
  with the fix round if cheap, otherwise file it.
- **m3 (observation, no action required).** Encounter confirms carry
  no time bound: a checked-in attendee can file one long after the
  event. The roster and check-in gates bound the blast radius to
  actual attendees. Note for the Phase 6 tuning pass, not this
  slice.

## Rulings on the named gaps

1. **QR registry:** ruled in M3. Not mergeable as strong proof;
   the registry follow-up is specified there for Dre's schema lane.
2. **Review flag record (strike ledger event + confirm row):**
   accepted for this slice. The record of an opened review exists,
   is queryable, and carries the confirm row, the strike event, and
   the recomputed band; `vfy_signals` is correctly left alone (it is
   session-bound verify bronze). The Phase 6 safety-console slice
   owns review state and the outcome write-back to both parties'
   ledgers that Section 7.2 requires; an append-only event cannot
   represent a closed review, and that state belongs to the console
   slice, not this one. No dedicated rsn_ review-queue table is
   required now.
3. **Guest passes:** accepted. Guest passes are unbuilt in every
   slice, so no real obligation is being dropped; when Section 7.8
   lands, the recompute's obligated set must extend to guest slots
   in that slice, and the review path already validates a pass when
   one exists.
4. **Welfare escalation effects:** accepted as record shapes, per
   the ticket's scope. The shape is complete and honest; the sends
   and the evidence-packet machinery belong to the console and
   Notify slices.
5. **Flat -10 strike weight:** accepted as the planning value for
   this slice. Section 7.5's reporter-standing weighting is pattern
   machinery over the ledger; the confirm rows preserve who reported
   what, so the data a later weighting needs is not lost. Phase 6
   staff-side work, as the evidence doc states.

## What closes this review

Fix M1, M2, and M3 with regression tests quoting the probes above;
m1 and m2 in the same round if cheap. On landing I will
spot-verify: the three probes re-run, the new regression tests, the
47-test file, and the full-suite node-set diff against the same
control. Nothing here touches the TW-661 lane, and nothing merges
without Justynn's word regardless of my verdict.
