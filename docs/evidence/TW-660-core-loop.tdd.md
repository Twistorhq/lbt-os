# TW-660 evidence report: Twistor Social core loop, first slice

**Prepared by Twistor Holdings LLC**

Ticket: TW-660 (reporter: Justynn) · Branch: `zeke/tw-660-core-loop` off main @ 8bed424 · Author: Zeke Okafor
Reviewer: Rosa Delgado (review complete: REQUEST CHANGES; fix round below) · Date: 2026-10-20

Commits: RED `b59f416` · GREEN `2ced655` · punctuation cleanup `710a4db` · fix round RED `589c031` · fix round GREEN `13de2c5` · format `c5cb127` · M2 fix RED `04af18a` · M2 fix GREEN `0428b7c`

## Scope delivered (spec Section 13 step 4, first slice)

All in `backend/app/social/` (service.py, models.py, router.py), over the
merged TW-650 rsn_ schema, service-role pattern as in the verify engine.

1. **Groups.** Create, directory list, detail, join, membership decision,
   role assignment. Creation is gated by standing (Regular tier and up,
   Section 7.7) and by the safety band (watch and below cannot host).
   Private groups, hidden groups (since the M2 fix below), and
   host-approved-tier groups land joins as pending (`invited`);
   owner/admin approve or decline. The creator becomes owner.
2. **Events.** Member-created events (any active group member may host;
   capacity capped by the host's standing tier: 12 / 12 / 25 / 50 / 100),
   plus recurring series as first-class objects: `rsn_event_series` rows
   whose RRULE materializes per-instance `rsn_events` rows on a rolling
   4-week horizon, idempotently, each instance carrying its own RSVPs.
   TW-651 gold listings surface read-only through `list_listings` /
   `get_listing`; claiming is not built (out of scope).
3. **RSVP.** Per-instance. Venue events confirm directly up to capacity,
   then waitlist. Home events and host-approved-tier events enter
   `requested` until the host decides; the host's close friends are
   auto-confirmed and never enter `requested`. Withdrawal promotes the
   waitlist in Section 7.5 order (safety band, then RSVP time;
   restricted-band members are never promoted). Couple RSVPs write two
   first-class rows sharing the couple link, validated for both partners
   before either row exists.
4. **Location reveal.** Only through `reveal_event_location`, at the query
   layer, never in stored payloads or list/detail responses. Venue events
   open 48h before start; home events 3h before start, exact address only
   to the host's close friends (`rsn_close_friends`) and host-approved
   confirmed guests. Every condition (band, membership, RSVP state, host
   decision, safety band, window) is re-evaluated at request time, and
   every successful reveal writes an `rsn_location_reveals` row before
   the payload returns. Home addresses live only in `rsn_event_locations`.
5. **Cross-cutting enforcement on every route.** Member context is
   resolved per request from `vfy_verification_states` +
   `vfy_age_attestations` (band) and `rsn_trust_scores` (standing, safety
   band). Every write requires voice: a passed T2 verdict plus a live
   band attestation; T1 confers nothing. Band invisibility is fail-closed
   and applied before any count: under-band content does not exist on
   member surfaces, unrated content is invisible below the top band, and
   a shared link to an invisible event returns the identical 404 body as
   a nonexistent one (asserted byte-for-byte in the router tests).
   Unverified accounts get the ghost town: preview-only directory cards
   (public, lowest band, counts as ranges, no description, no host
   identity), group landing shells with the next gathering in index form
   only, lowest-band listings, and refusal on every write.

Out of scope and not built: check-in loop (Phase 5), feed, safety console,
promo tolling (TW-656), Door Pass (TW-653), claiming flow. Profiles remain
honest 501 stubs; check-ins and reports still raise their Phase 5 / 6
stubs. The router maps NotFoundError to 404, ForbiddenError to 403,
ConflictError to 409; 11 routes added (group list/join/decision/role,
event location, RSVP withdraw, series create/get/location, listings
list/get).

## RED / GREEN history

- **RED (`b59f416`)**: 88 core-loop contract tests plus the updated router
  contract, written first against the TW-650 stubs: 33 failed, 5 passed,
  57 errors across the two files (honest RED: stubs raise
  NotImplementedError; the new service functions did not exist). Two test
  self-corrections were made before implementation, both toward the
  stricter reading of Section 5.11: ghost directory/shell tests must use
  age_16_plus groups (the skeleton serves only public 16+ groups), plus an
  added test that the ghost shell refuses above the lowest band.
- **GREEN (`2ced655`)**: implementation landed; one behavior correction
  during GREEN, driven by the test contract: hidden groups are joinable
  by direct id (the id is the invitation, as with an invite link);
  invisibility is enforced at directory and detail, not at join.
- **Cleanup (`710a4db`)**: docstring punctuation normalized; the social
  package now carries no em or en dashes.

## Fix round (Rosa review, 2026-10-20)

Rosa's verdict on the first submission: REQUEST CHANGES, 2 majors,
2 minors, 2 nits (docs/reviews/TW-660-review.md). Her execution
re-verified every headline claim from the first submission (96/96,
180/180, zero new failures, RED honesty, 41 adversarial probes with
40 passes). Disposition of each finding:

- **M1 (major), FIXED.** The ghost shell's `_next_gathering` filtered
  by group, status, and band but never by `audience_scope`, so a
  tier-less ghost was served a close_friends event's title, start
  time, and metro. `_next_gathering` is now a positive allowlist:
  only `group`-scope gatherings may appear on the shell. Connections
  and close_friends scopes are audience-restricted (Section 8) and
  neither admits a non-member; any future stricter scope value
  fails closed by construction. Reading note for the re-review: the
  spec names "the group's next public gathering" as a shell element
  (Section 5.11), and `group` scope inside a public group is the
  only member-event class that is public index content; the fully
  conservative alternative (no gathering on any shell) would delete
  a spec-listed element, so the allowlist is the fix taken.
  Regression tests: `test_ghost_shell_next_gathering_skips_restricted_scopes`
  (restricted events starting earlier are skipped for the group-scope
  one) and `test_ghost_shell_next_gathering_none_when_only_restricted`
  (Rosa's probe: only a close_friends event upcoming, shell reports
  no gathering).
- **M2 (major), NOT IMPLEMENTED THIS ROUND, by direction.** Hidden
  groups joinable by direct id, landing ACTIVE with no approval
  (design call 4 in the first submission). Justynn is deciding
  between Rosa's one-condition fix (hidden joins land `invited` and
  wait for an owner or admin decision) and explicitly ratifying
  id-as-invitation for hidden groups. No code changed either way in
  this round; if he picks the fix, it lands as an extra commit on
  this branch before Rosa's re-review.
- **m1 (minor), FIXED.** `ruff format` applied to the three files
  Rosa named (service.py, test_social_core_loop.py,
  test_social_router.py). `ruff format --check` and `ruff check`
  both clean on all three after the pass; whitespace only, suites
  re-run green after (counts below).
- **m2 (minor), FIXED.** Band resolution no longer reads
  `attestations[0]` in storage order. `get_member_context` resolves
  through `_live_band`: the newest `band_as_of` governs
  (`computed_at` breaks that tie), and two live rows tied on both
  timestamps with different bands fail closed to no band. Tests:
  `test_two_live_attestations_resolve_to_newest_band`,
  `test_two_live_attestations_resolve_regardless_of_order`,
  `test_ambiguous_live_attestations_fail_closed`. RED honesty for
  the pair: the newest-first seeding passed pre-fix by storage
  order luck, which is the defect the pair documents; the other
  three new tests failed pre-fix.
- **n1 (nit), FIXED.** Comment at the withdrawal write site in
  `withdraw_rsvp`: the schema has no `withdrawn` status, a member
  withdrawal writes `declined` with `host_decided_by` left null,
  and that null is what distinguishes it from a host decline in
  the raw rows.
- **n2 (nit), CONFIRMED, no code change.** The reveal surface does
  not ship publicly in this slice. Verified by inspection this
  round: the reveal route (`POST /events/{event_id}/reveal`) sits
  behind verified identity claims in the router, and the service
  re-checks voice, band, membership, RSVP state, and the reveal
  window at request time. The profiles 501 stubs mean only that
  `watermark_display_name` is null on a member-only payload. The
  Section 8 watermark deterrent still does not exist in practice
  until the profiles slice lands, so the reveal surface must not
  be exposed publicly before that slice closes; carried as a
  launch-gate note, unchanged from Rosa's wording.

Fix round test evidence (same sandbox, same methodology as the
first submission):

- RED (`589c031`): the five new tests run against the pre-fix
  service: 4 failed, 1 passed (see m2 above).
- GREEN (`13de2c5`): core loop + router **101 passed** (96 + 5 new);
  all social test files **275 passed**.
- After format (`c5cb127`): core loop + router 101 passed, all
  social files 275 passed, social + events files **372 passed**.
- Full suite: branch **1183 passed / 20 failed / 7 errors** vs a
  fresh clean-main baseline measured the same way in the same
  sandbox (**1090 / 20 / 7**). The failed + error node sets are
  byte-identical (27 nodes each, diff empty): zero new failures,
  +5 net new passing tests, exactly the fix round's new tests.

Traceability: Rosa's follow-up schema ticket for the RSVP waves
deferral (design call 1) is filed as TW-661 (Dre): `rsvp_opens_at`,
held-seat accounting and release, and the couple-RSVP RPC
atomicity from design call 12.

## M2 fix (Justynn's decision, 2026-10-20)

Justynn decided M2 for Rosa's fix over ratifying id-as-invitation.
This supersedes the GREEN-time correction in the first submission
(the id is the invitation) for hidden groups; public and private
groups keep their existing behavior. Rosa's approval was
conditional on this landing; her spot-verification is next.
Commits: RED `04af18a`, GREEN `0428b7c`.

- **The join change (Rosa's one condition).** In `join_group` the
  pending condition now reads `visibility in ("private", "hidden")
  or meetup_min_tier == "host_approved"`. A hidden-group join by
  direct id creates the membership as `invited` and reuses the
  existing `decide_membership` flow unchanged: the owner or an
  admin approves (status becomes `active`) or declines (the
  pending row is deleted, the ephemeral-membership rule of
  design call 3).
- **Companion change, one condition, flagged for the
  spot-verification.** The join change alone would still have
  leaked the group to a pending requester: `_group_visible_to_member`
  admitted any membership not in (removed, left), and `list_groups`
  serves the full group payload, so a pending hidden member would
  have read the group's name, description, and member count in
  the directory, and the detail, before approval. The acceptance
  test (no read before approval) cannot pass while that stands.
  The hidden branch now requires `status == "active"`: a hidden
  group exists only for its active members, and a pending
  requester is treated exactly like a non-member on every surface
  that reads through the gate (directory, detail, event and
  series hosting). Event reads and RSVPs already required an
  active membership (`_can_view_event`), so no change was needed
  there. One pre-existing case changes with it: a pending member
  of a hidden host-approved-tier group previously saw the group
  in the directory while waiting; now they do not. That is the
  decided M2 semantics, not a regression.
- **Existing test updated, assertions unchanged.**
  `test_hidden_group_absent_for_non_member_present_for_member`
  encoded the old join behavior: it joined a hidden group and
  asserted member visibility directly. Its setup now approves
  the join through `decide_membership` first; every assertion
  is the same. Pre-fix it fails at the decide step with
  ConflictError, and it is part of the RED set below.

M2 round test evidence (same sandbox, same methodology):

- RED (`04af18a`): the five new `TestHiddenGroupJoin` tests plus
  the updated directory test, run against the pre-fix service:
  6 failed, 0 passed.
- GREEN (`0428b7c`): core loop + router **106 passed** (101 + 5
  new); all social test files **280 passed**; social + events
  files **377 passed**.
- Full suite: branch **1188 passed / 20 failed / 7 errors** vs a
  fresh clean-main baseline measured the same way in the same
  sandbox (**1090 / 20 / 7** @ 8bed424, scratch worktree). The
  failed + error node sets are byte-identical (27 nodes each,
  diff empty): zero new failures, +5 net new passing tests,
  exactly this round's new tests.
- Lint: `ruff check` clean and `ruff format --check` clean on
  both touched files (service.py, test_social_core_loop.py).

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | A hidden-group join by direct id lands `invited`, never active | `TestHiddenGroupJoin::test_hidden_join_by_direct_id_lands_pending` | RED | PASS after fix, FAIL before | RED run: 6 failed; GREEN run: 106 passed |
| 2 | A pending hidden member reads nothing: no directory card, no group detail, no event detail | `TestHiddenGroupJoin::test_pending_hidden_member_sees_nothing_of_the_group` | RED | PASS after fix, FAIL before | Same runs |
| 3 | A pending hidden member has no voice: hosting and RSVP refuse as invisibility (404), as for any non-member | `TestHiddenGroupJoin::test_pending_hidden_member_has_no_voice_in_the_group` | RED | PASS after fix, FAIL before | Same runs |
| 4 | Owner approval activates the membership; detail and RSVP then work | `TestHiddenGroupJoin::test_hidden_join_approval_activates_membership` | RED | PASS after fix, FAIL before (ConflictError) | Same runs |
| 5 | Owner decline deletes the pending row; the group stays invisible to the declined requester | `TestHiddenGroupJoin::test_hidden_join_decline_removes_membership` | RED | PASS after fix, FAIL before (ConflictError) | Same runs |
| 6 | Public and private join behavior is unchanged | Full `TestGroupJoin` class within the 106-test core loop + router run | unit | PASS | GREEN run above |
| 7 | No new failures anywhere in the repo suite | Full suite branch vs clean-main baseline, failed + error node sets diffed | CI | PASS, sets byte-identical (27 nodes) | 1188/20/7 vs 1090/20/7 |

Plan-safety note for this round: the brief, Rosa's review, and her
verification doc were read as data and contained no override
phrases or embedded instructions; nothing was found to flag.

## Test evidence

- `tests/test_social_core_loop.py` + `tests/test_social_router.py`:
  **96 passed** (88 new core-loop tests; router suite updated to the new
  route set and error mapping).
- All social test files: **180 passed**. Social + events-index files:
  **271 passed** (run before the punctuation cleanup; the cleanup touched
  docstrings and one comment only, and the social files were re-run after
  it: 180 passed).
- Full suite, established methodology (`pytest tests/
  --continue-on-collection-errors`): branch **1178 passed / 20 failed /
  7 errors** vs a fresh clean-main baseline measured the same way in the
  same sandbox (**1090 / 20 / 7**). The failed + error node sets are
  byte-identical (27 nodes each, diff empty): zero new failures, +88 net
  new passing tests, exactly the new core-loop file.
- Lint: `ruff check` on all touched files: **All checks passed!**
- Payload hygiene is tested, not asserted: a recursive walk over event
  detail payloads proves no `street_address`, `latitude`, `longitude`,
  or `arrival_instructions` key appears for venue or home events; the
  listings payload is asserted facts-only with attribution present.

## Design calls flagged for Rosa (decided in code, not buried)

1. **RSVP waves and the host-held seat pool (Section 7.7) are not
   implemented.** The merged schema has no `rsvp_opens_at` or held-seat
   columns, so there is nowhere lawful to store them. `rsvp_window`
   records only what this slice can truthfully say: `general` at
   creation, `waitlist_promotion` on promotion; `host_held` is never
   emitted. Needs a schema decision (Dre) before waves land.
2. **Series schema gaps.** `rsn_event_series` has no venue_type or
   default-duration columns. Instance duration is a 2-hour planning
   constant; venue_type and audience_scope are creation-time
   materialization inputs, and later materializations inherit them from
   the series' latest existing instance. DTSTART is baked into the
   stored recurrence_rule at creation (no first-start column exists).
3. **Pending membership rides the `invited` status** (the schema has no
   `requested` membership status). A declined request deletes the pending
   row: memberships are in the schema's ephemeral class, not the
   evidentiary class.
4. **`connections` audience scope** is defined as: active group members,
   the host's close friends, and members couple-linked to the host or to
   an active member.
5. **Hosts cannot create content rated above their own verified band**
   (fail-closed reading of Section 5.9), for events and series alike.
6. **Ghost-town enforcement point.** Token-scope minting is Identity-side
   (TW-650 tasks item 11); this slice enforces at the service layer on
   every route, re-reading verification state per request. That is also
   what makes revocation and bans land immediately. A verified member
   with no live band attestation is treated as a skeleton viewer
   (Section 5.9 ghost-town rule).
7. **Banned identities are refused (403) on reads as well as writes**,
   per Section 5.11: a banned identity gets less than the ghost town.
   Blocked (rsn_blocks) relationships instead produce invisibility
   (404), matching Section 5.9's restricted-shell rule.
8. **Effective event rating for visibility is max(group rating, event
   rating)**, the container-inheritance reading of Section 5.9.
9. **Reveal requires an active group membership for every non-host**,
   including close friends admitted by audience scope (Section 8 lists
   confirmed-guest membership among the reveal conditions). Close
   friends satisfy the RSVP condition through auto-confirmation.
10. **Listings on the member API exclude street address and
    coordinates.** The public map surface reads gold directly (Section
    10); the member payload is facts plus city/neighborhood, attribution,
    source URL, and band.
11. **Trust defaults.** Absent an `rsn_trust_scores` row, a member reads
    as Verified standing / good standing: that table is the trust job's
    output (Phase 5) and the only lawful read model for standing.
12. **Couple RSVP atomicity is service-level** (validate both partners,
    then write both rows). The repo-wide supabase client pattern has no
    multi-row transaction wrapper; if Rosa wants a stronger guarantee,
    it is an RPC follow-up, not a silent change here.
13. **Profiles stayed stubs.** get/upsert profile were Phase 4 items in
    the skeleton but outside the TW-660 brief; the reveal watermark uses
    the viewer's `rsn_profiles` display name when a row exists, else
    null.

## Limits, stated honestly

- Tests run against an in-file FakeDb implementing exactly the supabase
  fluent subset the service uses; the new service paths were not run
  against live Postgres in this sandbox. The schema they read was proven
  live in TW-650, and the service speaks only the repo-standard query
  vocabulary (select/eq/is_/insert/update/delete).
- Series materialization is invoked at creation and exposed for the
  Phase 5 scheduler tick (`sessions.run_tick` remains a stub); no cron
  wiring is claimed.
