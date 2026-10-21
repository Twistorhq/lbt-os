# TW-660 evidence report: Twistor Social core loop, first slice

**Prepared by Twistor Holdings LLC**

Ticket: TW-660 (reporter: Justynn) · Branch: `zeke/tw-660-core-loop` off main @ 8bed424 · Author: Zeke Okafor
Reviewer: Rosa Delgado (pending) · Date: 2026-10-20

Commits: RED `b59f416` · GREEN `2ced655` · punctuation cleanup `710a4db`

## Scope delivered (spec Section 13 step 4, first slice)

All in `backend/app/social/` (service.py, models.py, router.py), over the
merged TW-650 rsn_ schema, service-role pattern as in the verify engine.

1. **Groups.** Create, directory list, detail, join, membership decision,
   role assignment. Creation is gated by standing (Regular tier and up,
   Section 7.7) and by the safety band (watch and below cannot host).
   Private groups and host-approved-tier groups land joins as pending
   (`invited`); owner/admin approve or decline. The creator becomes owner.
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
