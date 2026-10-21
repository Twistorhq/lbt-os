# TW-660 verification: Twistor Social core loop, fix round

**Prepared by Twistor Holdings LLC**

Reviewer: Rosa Delgado, Chief Quality Guardian
Branch: `zeke/tw-660-core-loop` (fix round on top of b54d065: RED 589c031, GREEN 13de2c5, format c5cb127, evidence edfc3fe, review doc carried 7dfd160)
Verdict: **APPROVE, conditional on the M2 decision landing** (see below). Every other finding from my review is closed, verified by my own execution.

## Per-finding closure

- **M1 (major), CLOSED.** `_next_gathering` is now a positive allowlist: only `group`-scope gatherings can appear on the ghost shell (`backend/app/social/service.py`, fix commit 13de2c5). I re-ran my exact original probe plus two variants of my own (7 probes total, all passing): a public 16+ group whose only upcoming event is close_friends-scope now reports no gathering, and the restricted event's title and start time appear nowhere in the shell payload; with restricted events starting earlier than a group-scope one, the shell picks the group-scope gathering and neither restricted title leaks; the band filter still applies on top of the scope allowlist. The schema backs the construction: `audience_scope` is NOT NULL with a CHECK over exactly the three values (`supabase/migration_social_v1.sql`), so the allowlist's default cannot mask a fourth value from the database, and any future stricter scope fails closed here by construction. Both of Zeke's regression tests assert the real shell output and pass.
- **Ruling on the M1 reading: the allowlist is correct.** My finding set the minimum (exclude close_friends and connections) and noted the fully conservative alternative (no gathering on any shell). Zeke's reading is the better one: Section 5.11's closed list names "the group's next public gathering" as a shell element, and a group-scope event inside a public group is exactly that element; deleting it would contradict the spec text to fix a leak the allowlist already closes. The residual is stated plainly: titles and times of group-scope events in public groups are public index content, which is what Section 5.11 promises the shell shows. No address, description, or restricted-scope content reaches the shell.
- **M2 (major), OPEN DECISION ITEM, not a failed fix.** No code changed either way, exactly as directed. Hidden groups remain joinable by direct id, landing ACTIVE with no approval, until Justynn decides: my one-condition fix (hidden joins land `invited` and wait for an owner or admin decision) or his explicit ratification of id-as-invitation for hidden groups. This approval becomes unconditional when either (a) the fix commit lands on this branch and I spot-verify it, or (b) Justynn's ratification is recorded on ticket TW-660. Until one of those happens, treat M2 as an open major.
- **m1 (minor), CLOSED.** I ran both myself: `ruff check` passes and `ruff format --check` reports all three previously flagged files (service.py, test_social_core_loop.py, test_social_router.py) already formatted. I also proved the format commit behavior-neutral: the AST of each of the three files is identical before and after c5cb127.
- **m2 (minor), CLOSED.** Band resolution now goes through `_live_band`: newest `band_as_of` governs, `computed_at` breaks that tie, and two live rows tied on both timestamps with different bands fail closed to no band. My own determinism probes, beyond Zeke's three tests: three live rows seeded in all six storage orders always resolve to the newest band; the computed_at tiebreak holds in both insertion orders; an exact tie with different bands yields no band and band_rank 0; an exact tie with the same band is not treated as ambiguous.
- **n1 (nit), CLOSED.** The comment is at the withdrawal write site in `withdraw_rsvp`: no `withdrawn` status exists in the schema, and `host_decided_by` staying null is what distinguishes a member withdrawal from a host decline.
- **n2 (nit), CONFIRMED, no code change.** Nothing in this slice exposes the reveal surface publicly; the watermark stays null until the profiles slice lands, and that remains a launch gate, unchanged from my review.

## Test numbers, reproduced by my own execution

- Core loop + router: **101 passed** (claim: 101). All social test files: **275 passed** (claim: 275). Social + events: **372 passed** (claim: 372).
- Full suite, same sandbox, same methodology: branch **1183 passed / 20 failed / 7 errors** vs a fresh clean-main baseline I measured myself in a scratch worktree @ 8bed424: **1090 / 20 / 7**. The failed + error node sets are **byte-identical (27 nodes, diff empty)**. Zero new failures: TRUE. The +5 net new passing tests are exactly the fix round's new tests.
- RED honesty for the fix round: at 589c031, four of the five new tests fail against the pre-fix service; the fifth (newest-first seeding) passes pre-fix by storage-order luck, which is precisely the defect the m2 test pair documents and exactly what the evidence doc discloses.
- Evidence report (TW-122): the fix-round section of `docs/evidence/TW-660-core-loop.tdd.md` matches every number I reproduced, states the M1 reading note and the M2 deferral plainly, and traces the waves schema follow-up to TW-661. Plan-safety scan: no embedded instruction to bypass validation, review, or disclosure anywhere in the fix-round diff or evidence; the M2 deferral is routed to Justynn's decision, which is the opposite of a bypass.

## Conditions and next steps

1. M2 lands one of the two ways above; if it is the fix commit, I spot-verify it before merge.
2. Merge still requires Justynn's word. Nothing merges on my say-so.
3. Standing notes from my review remain in force: future Social slices of this size should stack, and the reveal surface must not ship publicly before the profiles slice closes the watermark gap (n2).
