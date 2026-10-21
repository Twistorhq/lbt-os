# TW-671 TDD evidence: Door Pass biometric door transaction prototype

Prepared by Twistor Holdings LLC

## 1. Source

Ticket TW-671 (reporter Justynn, decision 2026-10-21): Door Pass re-scopes to the biometric version. Members take their own picture at the door; the TW-658 verification engine verifies it against the ID uploaded at onboarding. Re-scope spec: twistor-lab/specs/TW-671-door-pass-biometric-rescope.md, built on TW-647 (original architecture), TW-657 (state profiles, Rosa approved), and Angela's TW-657 state legal specifications.

## 2. Task report

Behavior 1: server derived flow from the state profile. A transaction is created for a venue state and a purpose; the resolver derives reliance_biometric (FULL) or lane_qr_photo (PARTIAL); a client supplied flow mode is rejected; a KILL state refuses at creation; a disabled profile (incomplete approval chain) refuses at creation; a PARTIAL state refuses an alcohol service purpose and never blends.

Behavior 2: the reliance transaction. Door consent (separate door_biometric_capture instrument) is required at creation and re-checked at finalization; standing and age_21_plus band are read from the verify store; a randomized liveness challenge set is issued per transaction; the door selfie is staged, embedded at the TW-658 port boundary, matched 1 to 1 against the enrollment template persisted by the TW-658 pipeline, and banded with the engine thresholds; the staged capture is destroyed on every outcome.

Behavior 3: fail closed mapping. No match, review band, liveness failure, weak passive signal (review decision code), banned anchor, multiple faces, and a mid transaction consent withdrawal finalize as not valid; a model stage unavailable or a mid transaction profile change finalizes as unavailable. A finalized transaction cannot be replayed.

Behavior 4: the evidence record. Every finalized transaction writes one scan record before answering: profile id, version and canonical hash, purpose, flow, legal effect, result, internal failure class, decision code, match band, authorization status at scan, proof bundle key, claim keys and their set hash, and one way reference hashes for the biometric transaction and the enrollment. No image, template, score, name, or birthdate field exists on the record.

Behavior 5: the D3 hard gate. The service transacts only on enrollments flagged as consented fixtures; a non-fixture enrollment is refused with the production capture flag off (the test asserts the flag is off on the config the service carries).

Validation commands actually run:

- RED (commit 1723553): `python3 -m pytest tests/test_door_pass_biometric.py -q` on the branch with tests only. Output: `ModuleNotFoundError: No module named 'app.door'`, collection error, 0 tests run. The module did not exist; the tests define it.
- GREEN (commit 1701920): same command. Output: `22 passed in 0.37s`.
- Neighboring suites: `python3 -m pytest tests/test_door_pass_biometric.py tests/test_verify_engine_pipeline.py tests/test_verify_engine_core.py tests/test_verify_engine_router.py tests/test_verify_engine_gate.py -q`. Output: `118 passed`.
- Full suite on the branch: `python3 -m pytest tests -q --continue-on-collection-errors`. Output: `1319 passed, 20 failed, 7 errors`. The same command on clean main (0beb8e2) in the main checkout produced a failed plus error node list of 27; the branch list is 27 and `diff` of the two sorted node lists is empty. Zero new failures, byte identical node sets.
- Lint: `python3 -m ruff check app/door tests/test_door_pass_biometric.py` clean; `python3 -m ruff format --check` clean after `ruff format` (3 files reformatted, behavior neutral formatting only).

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | A live door selfie matching the enrollment template, with liveness answered, is a valid reliance result in a FULL state, and the evidence record carries the profile version and hashes | test_door_pass_biometric.py::test_biometric_match_is_valid_reliance_in_full_state | RED | PASS | RED at 1723553 (module missing), GREEN 22/22 |
| 2 | A stranger selfie is not valid with internal class face_no_match, staged capture wiped | ::test_no_match_is_not_valid | RED | PASS | GREEN run |
| 3 | A review band similarity fails closed at the door | ::test_review_band_match_fails_closed_at_the_door | unit | PASS | GREEN run |
| 4 | Unanswered active liveness challenges are not valid | ::test_liveness_failure_is_not_valid | unit | PASS | GREEN run |
| 5 | A weak passive liveness signal never manufactures a pass | ::test_weak_passive_liveness_never_manufactures_a_pass | unit | PASS | GREEN run |
| 6 | A banned anchor is refused at the door | ::test_banned_anchor_is_refused | unit | PASS | GREEN run |
| 7 | A model stage unavailable resolves to unavailable, fail closed, capture wiped | ::test_model_unavailable_fails_closed_to_unavailable | unit | PASS | GREEN run |
| 8 | A KILL state refuses transaction creation and writes no scan | ::test_kill_state_refuses_transaction_creation | RED | PASS | GREEN run |
| 9 | A profile with an incomplete approval chain (no justynn_enable) resolves disabled and refuses | ::test_disabled_state_refuses_transaction_creation | unit | PASS | GREEN run |
| 10 | A PARTIAL state refuses reliance, its lane transaction has no biometric step (a selfie on it is an error), and the lane result carries the non reliance claim keys and no age result | ::test_partial_state_refuses_reliance_and_never_blends | RED | PASS | GREEN run |
| 11 | A client supplied flow mode is rejected | ::test_client_supplied_flow_mode_is_rejected | unit | PASS | GREEN run |
| 12 | Entry in a FULL state also rides the biometric flow | ::test_full_state_entry_also_rides_the_biometric_flow | unit | PASS | GREEN run |
| 13 | Missing door consent refuses creation; the onboarding biometric consent alone does not satisfy the door | ::test_missing_door_consent_refuses_reliance, ::test_onboarding_consent_alone_does_not_satisfy_the_door | RED | PASS | GREEN run |
| 14 | Consent withdrawn mid transaction finalizes not valid, class consent_not_current | ::test_consent_withdrawn_mid_transaction_refuses_capture | unit | PASS | GREEN run |
| 15 | A non-fixture (production) enrollment is refused while the D3 capture flag is off | ::test_production_enrollment_is_refused_while_capture_flag_off | RED | PASS | GREEN run |
| 16 | An age_18_20 band cannot create a service transaction | ::test_under_21_band_cannot_transact_for_service | unit | PASS | GREEN run |
| 17 | The evidence record carries no biometric material keys (template, image, score, name, dob, birth, photo) and carries the CA proof fields | ::test_evidence_record_carries_no_biometric_material | unit | PASS | GREEN run |
| 18 | A finalized transaction cannot be replayed; exactly one scan exists | ::test_a_finalized_transaction_cannot_be_replayed | unit | PASS | GREEN run |
| 19 | Profile hashes differ per state and a tampered profile (score edited after authoring) fails integrity and disables | ::test_profile_hash_binds_the_evidence_to_the_reviewed_version, ::test_a_tampered_profile_fails_integrity_and_disables | unit | PASS | GREEN run |
| 20 | No regression: full suite failed plus error node sets byte identical to clean main | pytest full suite on branch and on main, diff of sorted FAILED/ERROR lists | CI | PASS | 27 nodes both, diff empty; 1319 passed on branch |

## 4. Coverage and known gaps

- Coverage is the prototype service, resolver, and store at unit level with the model boundary stubbed by deterministic fakes, the TW-658 test discipline. No HTTP router is built in this ticket; the router surface is TW-653 build scope (re-scope spec Section 11).
- No migrations: the dps_ tables do not exist in lbt-os yet (the TW-650 schema excluded them by scope guard). The in-memory store mirrors the TW-657 shapes; production persistence is TW-653 step 1.
- No biometric accuracy claim of any kind. The match runs on synthetic fixture embeddings. Real accuracy is the TW-649 benchmark on consented volunteers, and TW-659 must land before any document side benchmark claim.
- The production resolver's RFC 8785 (JCS) canonical hash, approval and change event projections, decision freshness watermark, and cache invalidation are designed in TW-657 and scoped in the re-scope spec Section 11; the prototype's resolver implements the same decision rules over in-memory records with sorted key JSON hashing, labeled as a stand-in in code.
- Authorization status rides on the prototype profile as a stand-in for the TW-657 change event projection; named in code and in the re-scope spec.
- Venue onboarding, signage and contract generation, tolling, and downgrade notification are not in this prototype; they are TW-653 steps 4 to 6. Downgrade behavior inside a transaction (abort at finalization) is implemented and tested at the resolver level.

## 5. Plan-safety notes

No untrusted plan content was encountered. All design inputs came from the ticket, the named Twistor specs, Angela's legal specifications, and the merged TW-658 code. Nothing in any source read during this build instructed a scope change, a gate bypass, or a capture enablement; the D3 production capture flag was verified off in the test suite and was never enabled.
