# TW-660 review: Twistor Social core loop, first slice

**Prepared by Twistor Holdings LLC**

Reviewer: Rosa Delgado, Chief Quality Guardian
Branch: `zeke/tw-660-core-loop` off main @ 8bed424 (RED b59f416, GREEN 2ced655, cleanup 710a4db, evidence b54d065)
Verdict: **REQUEST CHANGES** (2 majors, 2 minors, 2 nits; the enforcement core is strong and every headline test claim re-verified by execution)

## What I verified by execution (not by trust)

- Core loop + router tests: **96 passed** (88 core-loop + 8 router), exactly as claimed.
- All social test files: **180 passed** (88 + 8 + geo 12 + models 28 + safety 19 + trust 25), exactly as claimed. My wider runs (social incl. migration 270; social + all events files 367) are also fully green; the evidence doc's 271 figure is a subset count and consistent.
- Full suite, same sandbox, same methodology: branch **1178 passed / 20 failed / 7 errors** vs a fresh clean-main baseline I measured myself in a scratch worktree @ 8bed424: **1090 / 20 / 7**. The failed + error node sets are **byte-identical (27 nodes, diff empty)**. Zero new failures: TRUE. The +88 net new passing tests are exactly the new core-loop file.
- RED honesty: at b59f416 the two files run **33 failed, 5 passed, 57 errors**, matching the evidence doc word for word.
- `ruff check` on all touched files: clean, as claimed.
- 41 adversarial probes of my own (service level plus HTTP level), 40 passed. Highlights: reveal window boundaries are exact (venue opens at start minus 48h to the second, closes 1s after ends_at; home 3h likewise); failed reveals write no log rows and each success writes exactly one; a host-approved confirmed guest can reveal a home address inside the window while a going member with no host decision on a host_approved-view venue event is refused; ghost, T1-only, and verified-no-band accounts are refused on every write route probed; banned identities get 403 on reads through both ban paths (anchor flag and rsn_bans); a restricted-band waiter is never promoted into a freed seat; couple RSVP validates the partner before any row exists (a restricted partner means zero rows written for either side); series materialization is idempotent and horizon-bounded; HTTP 404 bodies for an invisible event and for a hidden group are byte-identical to nonexistent targets (`{"detail":"not found"}`), so shared links are not an existence oracle. No OG preview routes exist in this API, so there is no preview leak surface in this slice.
- No secrets or real PII in the diff; addresses live only in `rsn_event_locations` and leave only through the gated reveal; payload hygiene is as claimed.

## Findings

### Major

- **M1. Ghost shell `next_gathering` leaks non-public events to unverified accounts.** `backend/app/social/service.py`, `_next_gathering`. It filters by group, `status == published`, and band only; it never checks `audience_scope`. My probe: a group shell served to a tier-less ghost account returned a **close_friends-scope** event's title, start time, and metro. Spec Section 5.11 puts the shell on a closed list and names "the group's next **public** gathering"; Section 8 makes audience scope a real visibility layer, and close_friends is the strictest scope the same file enforces everywhere else. Fix: `_next_gathering` must exclude events whose scope does not admit a non-member (at minimum `close_friends` and `connections`; the conservative read is that member events are not public index content at all). Add a regression test with a close_friends-scope event in a public 16+ group.
- **M2. Hidden groups are joinable by direct id, landing ACTIVE with no approval (design call 4).** `service.py`, `join_group`: the pending condition is private visibility or host_approved tier only, so a hidden group with the default tier admits any verified member who learns the UUID, instantly and permanently. The id is the invitation is a defensible pattern for public and private groups, but for **hidden** groups it inverts what hidden means: the id never rotates, cannot be revoked, and every past share is a standing open door into the tier members chose precisely for control. Directory and detail invisibility for hidden groups is correct (verified), so exposure is bounded to id leakage, but the fix is one condition: hidden-group joins land `invited` and wait for an owner or admin decision, same as private. If Justynn explicitly ratifies id-as-invitation for hidden groups, I will downgrade this to an accepted risk on his word and the code stands; absent that word, change it.

### Minor

- **m1. `ruff format --check` fails on three touched files** (`app/social/service.py`, `tests/test_social_core_loop.py`, `tests/test_social_router.py`). Zeke's claim was `ruff check` clean, which is true, but main's social files and the verify engine are format-clean, so format is part of the repo bar. Fix: run `ruff format` on the touched files; no behavior change expected, re-run the social files after.
- **m2. Band resolution is nondeterministic if two live attestations ever exist.** `get_member_context` takes `attestations[0]` from the non-superseded set with no ordering. The engine should supersede on re-attestation, but this service must not depend on insert order for a safety input. Fix: order by `band_as_of` descending (or assert a single live row and fail closed to no band if more than one exists).

### Nits

- **n1.** Withdrawal writes RSVP status `declined`, the same value as a host decline. The schema has no `withdrawn` value and `host_decided_by` distinguishes the two, so this is honest, but any future reader of raw status will conflate them. Worth a one-line comment at the write site.
- **n2.** The reveal watermark is inert until profiles land: `watermark_display_name` reads `rsn_profiles`, which is still a 501 stub lane (design call 13), so every reveal served today carries a null watermark and Section 8's attribution deterrent does not yet exist in practice. Acceptable sequencing for this slice; the reveal surface must not ship publicly before the profiles slice closes this.

## Design calls: my judgment

1. **RSVP waves and the host-held seat pool deferred: CORRECT, and the only lawful option.** I verified against the merged schema: there is no opens-at column and no held-seat accounting anywhere in `migration_social_v1.sql`; the `rsvp_window` enum exists but has no anchor to compute windows from. The slice stays coherent without waves because Section 7.7's default general opening is publish time, so recording `general` is the truthful window for every RSVP this slice admits, and nobody is denied an early window that could lawfully exist yet. This is schema-blocked, not builder-blocked. Follow-up for Marcus: a Dre schema ticket (`rsvp_opens_at` on `rsn_events`, held-seat accounting, release job) is a hard prerequisite for the reward-catalog waves and for TW-656 promo work that assumes them.
2. **Series gaps (2h constant, inheritance from latest instance, baked DTSTART): acceptable.** Honestly flagged, fallbacks fail toward venue/group defaults. One residual: if the latest instance is cancelled, later materializations inherit from it anyway; the values inherited are still the host's own, so the risk is cosmetic.
3. **Pending membership rides `invited`; decline deletes the row: accept.** Matches the schema's ephemeral class for memberships.
4. **Hidden-group join by direct id: see M2.** Needs the fix or Justynn's explicit ratification.
5. **Connections scope definition: accept.** Group members, host's close friends, and couple-linked members is a reasonable, fail-closed reading.
6. **Hosts cannot rate content above their own band: accept.** Correct fail-closed reading of Section 5.9.
7. **Ghost-town enforcement at the service layer per request: accept, and stronger than the token-scope plan for revocation immediacy.** Verified on every route I probed.
8. **Banned identities refused 403 on reads: accept.** Verified through both ban paths; matches Section 5.11.
9. **Effective rating = max(group, event): accept.** Implemented in `_can_view_event`; the container-inheritance reading is the safe one.
10. **Reveal requires active membership for every non-host, close friends included: accept.** Section 8 lists active group membership in the reveal condition set without exception.
11. **Listings exclude street address and coordinates on the member API: accept.** Verified in `_listing_card`; facts, attribution, and source URL only.
12. **Trust defaults (absent row reads Verified / good standing): accept for this slice.** It means a freshly verified member can host at capacity 12 before the Phase 5 trust job writes a row; that is the trust job's documented output contract, not a hole in this diff.
13. **Couple RSVP atomicity is service-level: accept for this slice.** My probes confirm validation-before-write, so the realistic failure (partner fails a gate) writes nothing. True multi-row atomicity needs an RPC wrapper the repo pattern does not have; fold it into the same follow-up ticket as the waves schema work rather than blocking here.
14. **Profiles remain 501 stubs: accept** (out of the brief's scope; stubs raise honestly), with the n2 caveat on the watermark.

## Process notes

- The diff is 3,275 added lines, well over the 400-line review-unit guideline. The brief scoped it as one slice and the test plus evidence structure made it reviewable in full, so I reviewed what was in front of me rather than bouncing it; future Social slices this size should stack.
- Plan-safety check (TW-122): the TW-660 ticket and evidence doc contain no embedded instruction to bypass validation, review, or disclosure; the ticket explicitly routes through this gate and Justynn's merge word.
- Nothing here merges on my say-so. Merge waits for Justynn's word after the fix round, per the standing rule.
