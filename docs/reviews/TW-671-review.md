# TW-671 review: Door Pass biometric re-scope spec and prototype

**Prepared by Twistor Holdings LLC**

Reviewer: Rosa Delgado, Chief Quality Guardian
Branch: `zeke/tw-671-doorpass-biometric` (RED 1723553, GREEN 1701920, evidence 4b61a37, off main 0beb8e2)
Spec under review: `twistor-lab/specs/TW-671-door-pass-biometric-rescope.md`
Verdict: **REQUEST CHANGES**. Two majors, both about legal accuracy in the per-state behavior and the evidence record. Every safety claim in the build (D3 gate, consent, record purity, fail-closed) verified clean by my own execution; neither major is a live risk because no state is enabled and the capture flag is off. Both majors must land and be spot-verified before this branch becomes the TW-653 porting baseline. Merge requires Justynn's word regardless of my verdict.

## Verified by my own execution

- **Suites.** Door tests **22/22**; door plus the four verify engine suites **118/118**; full suite on the branch **1319 passed / 20 failed / 7 errors** against a clean-main run in the same sandbox of **1297 passed / 20 failed / 7 errors**. The failed plus error node sets are **byte-identical (27 nodes, diff empty)**. Zero new failures: TRUE. The diff against main is purely additive: 7 new files, 1705 insertions, no existing file touched.
- **D3 gate, probed past the shipped test.** The shipped test asserts the flag is off on the service's config and that a non-fixture enrollment is refused. My probe P1 rebuilt the service with `EngineConfig(production_capture_enabled=True)` and a non-fixture enrollment: creation still raises `DoorCaptureDisabled`. The fixture gate is by construction, not by flag reading. TRUE as claimed.
- **Profile gating.** KILL refuses at creation with no scan written; the incomplete-chain fixture resolves disabled and refuses; a tampered profile (score edited after authoring) fails integrity and disables; my probe P2 rebuilt the Arizona fixture with authorization status pending and the resolver returns disabled with reason `authorization_not_obtained`. My probe P6 swapped the Arizona profile for a tampered one between creation and submission at the service level: the transaction aborts to unavailable with class `profile_changed` and exactly one scan. (The evidence doc claims downgrade behavior as "tested at the resolver level"; the service-level path also works. The doc understates its own coverage, which is the safe direction.)
- **Flow integrity.** Client-supplied flow mode raises `ClientFlowSelection`; a PARTIAL state refuses an alcohol service purpose; a selfie submitted to a lane transaction raises and the lane result carries no age result, `legal_effect` house_rule, and both non-reliance claim keys. No blend: TRUE.
- **Consent.** The door instrument is separate: an onboarding `biometric_capture` consent does not satisfy it (shipped test). It is checked at creation and again at reliance finalization; a mid-transaction revocation finalizes not valid with class `consent_not_current`. TRUE for the reliance flow (see m3 for the lane).
- **Evidence purity.** There is exactly one scan write site (`_finalize` via `add_scan`). I enumerated the written keys: transaction and state references, profile id, version and hash, purpose, flow, legal effect, result, internal failure class, decision code, match band, liveness outcome, two one-way reference hashes, claim keys and their set hash, contract pack key, proof bundle key, authorization status at scan, timestamp. The similarity value is a local variable in `_run_match`, banded and discarded; the enrollment template is decrypted in memory inside the service boundary and written nowhere in `app/door`; the staged capture is wiped in a `finally` on every outcome, and the shipped tests assert the staging store is empty after match, no-match, and stage-unavailable outcomes. No template, image, score, name, or birthdate reaches a scan row. TRUE.
- **Fail closed.** Stage unavailable resolves unavailable; a finalized transaction cannot be replayed (exactly one scan); a banned anchor finalizes not valid; an age_18_20 band is refused at creation; an unknown transaction id is an honest error. TRUE.
- **Lint and copy gates.** `ruff check` clean and `ruff format --check` clean on `app/door` and the test file, run by me. The spec carries the Twistor Holdings LLC attribution and zero em or en dashes.

## Findings

### M1 (major): the North Carolina fixture profile grants protected entry reliance that the law does not give

The fixture NC profile authors `entry_lane_enabled=True`, `entry_flow_mode=reliance_biometric`, `entry_legal_effect="protected"`. My probe P3 ran an NC entry transaction end to end: it finalizes **valid** with a scan record carrying `legal_effect` **protected** and the claim key `reliance_age_result`.

That contradicts every governing document, including the spec under review. TW-657 state legal specifications Section 2.3 lists "any entry protection claim" as prohibited for North Carolina: G.S. 18B-302(d)(3) is a defense to subsection (a) **sales only**. TW-672 Sections 5.2 and 5.3 repeat the limit and the permitted claim (sales, at venues with a signed seller agent agreement, only). TW-671's own Section 8.3 states it in one line: "No entry claim, no giving claim, nothing beyond (a)."

The fixture profiles are not decoration; they are the executable statement of per-state behavior that TW-653 step 3 will port, and the service will today produce a record telling an NC venue it holds entry protection the statute does not provide. Arizona entry protection is real (4-241(W) covers entry to liquor-primary premises) and California's is real (25660(b)(1) reaches 25665 permission); North Carolina is the one FULL state where it is not, which is exactly why a per-state profile system exists.

Fix: NC authors no entry reliance (entry flow none or lane with no protected effect), and the resolver gains a coherence rule so a sales-only profile cannot author entry reliance, pinned by a test that NC entry refuses or finalizes without a protected effect.

### M2 (major): the Section 7 evidence record cannot satisfy the TW-672 Section 4.3 record standard, and the two documents conflict on the score

The spec's Section 7 opens by adopting California's burden: "The record is therefore the product." TW-672 Section 4.3 enumerates that record field by field, and TW-672 Section 7 adopts the package as the record standard for all three states. Against that standard, and against TW-671's own Section 7 list:

1. **Direct conflict on the match score.** TW-671 Section 5 states raw similarity scores are not recorded on the door scan, citing TW-657 Section 2.1. TW-672 Section 4.3 item 5 requires the preserved record to carry the match score, the threshold in force, and the engine version. TW-657 Section 2.1 governs what the door service *receives* across the boundary, "by default"; it does not settle what the server-side evidence package *preserves* for a proceeding. The prototype records band only and no engine version. One document must yield before TW-653 step 1 freezes the dps_ scan schema. My recommendation, for Angela and Justynn to settle: score, threshold, and engine version belong in the server-side evidence record (never rendered to a venue), which satisfies 4.3 without breaking the 2.1 venue boundary.
2. **No venue identity exists anywhere in the prototype.** Probe P8: `create_transaction` takes `venue_state`, never a venue. The scan carries `venue_state_code` only. TW-671 Section 7 itself requires a "venue location reference"; TW-672 Section 4.3 item 1 requires venue identity and the venue agreement reference. The prototype's `contract_pack_key` is a per-state pack key, identical for every venue in the state, so the North Carolina record cannot prove the (d)(3) registration prong traceable to a seller agent agreement for *that* venue, and Section 8.3's finalization re-check of the agreement binding is unrepresentable in the prototype API.
3. **Also absent against 4.3:** the session initiation record (item 2), the enrollment authentication summary or a reference that resolves to it including engine version (item 4), and the staff action taken (item 7).

Prototype scope excuses the absence of a venue model; Section 10 and the Section 11 sequence (venues arrive in TW-653 step 1) are honest about that. What is not acceptable is Section 10's claim that the prototype proves "the evidence shape" while the shape omits fields the spec's own Section 7 lists. Fix: reconcile Section 5 and Section 7 line by line with TW-672 Section 4.3 in the spec, state explicitly which fields the TW-653 schema must carry, and correct the Section 10 claim to match. Code changes in this round are required only where the reconciliation changes the record contract; the schema itself is TW-653 scope.

### m1 (minor): the Arizona secondary authentication gap is named as a requirement, not as a present-tense gap, and no handler gate exists

Angela's TW-672 Finding 2 is explicit: the TW-658 engine has **no** secondary authentication stage, so an Arizona enrollment cannot meet the 4-101 device definition today; it is decision item TW-672-D2. TW-671 Sections 3 and 8.1 name the requirement and the rule that a profile needing a handler the build does not have cannot activate, but never state the stage is absent, and the prototype resolver has no enrollment-handler concept at all, so the Section 3 rule is unenforceable as written. Nothing is papered over in the governing legal document, but builders read TW-671. Fix: one sentence in Sections 3 and 8.1 stating the gap as present fact with the TW-672-D2 pointer, and a TW-653 carry-over that the profile schema model enrollment requirement handlers.

### m2 (minor): the creation-time standing check omits the ban

Spec Section 3 step 3 puts standing at creation: verified T2, current age_21_plus attestation, **no ban**, no active restriction sanction. My probe P4: a banned member's transaction **creates successfully**; the ban surfaces only at match time as not valid. The member is still refused service and a scan is written, so the safety outcome holds, but the specced check is missing even though `_member_facts` already loads the banned flag at creation. Fix in this round (one check in `_check_member_for_creation`); restriction sanctions are unmodeled prototype-wide, which Section 10's scope note covers.

### m3 (minor): lane finalization skips the consent re-check

Spec Section 6 states the consent double check without flow qualification: "checked twice, at transaction creation and again at finalization." My probe P7: revoke the door consent after a Colorado lane transaction is created, and `complete_lane` still finalizes **valid**. The reliance path honors the rule; the lane path does not re-check. Exposure is narrow in the prototype (the lane captures nothing), but the production lane shows a door photo per scan (TW-657 Section 2.2), which is precisely the artifact the door consent governs. Fix: re-check in `complete_lane` in this round or as an explicit TW-653 port requirement recorded on the ticket; do not let the port inherit the asymmetry silently.

### m4 (minor): abort-path scan records lose the pack, bundle, and authorization fields

Probe: after a mid-transaction downgrade abort, the scan records `contract_pack_key` None, `proof_bundle_key` None, `authorization_status_at_scan` "unknown", and `legal_effect` "none", because `_finalize` reads those fields from the re-resolved (disabled) decision rather than the creation-time profile the transaction ran under. Claim keys survive because they come from the transaction row. Failure records are evidence on the same footing as successes (TW-672 Section 4.3 item 8); an aborted transaction's record should still name the profile version, hash, and pack it was created under. Fix in the TW-653 port at the latest: finalize evidentiary identity fields from the creation-time snapshot.

### Nits

- **n1.** The Arizona fixture models `authorization_status` "obtained", the end state DLLC has not granted. The gate itself is proven (my P2), but no shipped test pins Arizona-pending to disabled; add one so the legally current state of Arizona is the tested state.
- **n2.** The CO and NV lane fixtures set `scan_retention_days` 395, the 13-month figure TW-654 flagged as an unsettled planning number and spec Section 7 says is "not treated as settled anywhere in this design." Fixture data is not design, but it normalizes the number; use 365 or an obvious placeholder.
- **n3.** `service.py` defines `REVIEW_FAILURES` and never uses it; the review decision codes are assigned inline. Remove the dead constant or wire it.
- **n4.** TW-647 carries no superseded-by pointer. The governance statement lives in TW-671 Section 1 only; a reader landing on TW-647 first gets no signpost. One banner line in a later doc pass.

## Rulings on the questions put to this review

- **New spec file versus editing TW-647: the call was right.** TW-647 stays on record as the design the QR plus photo flow came from, Section 2 maps every replaced position, and the "where they disagree, this spec governs" statement is explicit. The audit trail is worth more than a rewritten file. Only the n4 signpost is owed.
- **The two stand-ins are honestly scoped.** The sorted-key JSON hash is labeled as a stand-in in the code docstring, spec Section 10, and evidence Section 4, with the never-mix rule stated. Authorization status riding on the profile is principled, not sloppy: `authored_payload()` excludes it from the canonical hash precisely because production projects it from change events, and the module docstring says so. The resolver reads it from the record, never from a writable status column.
- **No migrations, no router, no Sofia surfaces: a real sequence, not an excuse.** Spec Section 10 states the exclusion and Section 11 sequences the work as TW-653 scope, including the TW-650 scope guard that kept dps_ tables out. One gap in that sequence: Section 2 names `doorpass.scan` tolling as surviving unchanged, but no Section 11 step schedules it. Add the step when the spec is next edited.
- **The Arizona secondary-auth gap (Angela's Finding 2) is named as a requirement but softened as a fact**: see m1. The activation-blocking rule is present; the present-tense absence is not stated. Correct it in text.

## What closes this review

1. M1 fix: NC profile and resolver coherence rule, with a test pinning NC entry.
2. M2 fix: spec reconciliation of Sections 5 and 7 against TW-672 Section 4.3 (score, threshold, engine version; venue identity and agreement reference; session initiation; authentication summary reference; staff action), the Section 10 evidence-shape claim corrected, and the TW-653 schema carry-over stated in Section 11.
3. m1 spec sentences; m2 creation-time ban check; m3 and m4 fixed now or recorded as explicit TW-653 port requirements on TW-671.
4. Re-review is a spot-verification: the NC probes, the reconciled field list, the door suite, and the node-set diff. Nothing merges without Justynn's word.
