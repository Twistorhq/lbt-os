# TW-884 — Outbound sales agent, slice 1 (epic TW-883): TDD evidence

Prepared by Twistor Holdings LLC

**Branch:** `zeke/tw-884-outbound-calls` from lbt-os main `b330a89`.
**Checkpoints:** RED `be04527` (tests only) → GREEN (this build).
Fix round (Rosa REQUEST CHANGES + Rex findings TW-888/889/890):
RED `80cdb90` → GREEN (see "Fix round" section at the end).
**Posture:** TEST MODE ONLY. Global kill switch defaults OFF, test mode
defaults ON (allowlisted numbers only), every provider fails closed,
zero live prospect dials, zero spend.

## Source

- TW-882 local outreach pack (Dez): `TALK-TRACK.md` (script lines,
  objection branches, compliance block, honest no-date start line),
  `FIRST-BATCH.md` (ranked list over
  `sales/denver-metro-hvac-prospects.csv`), `CALENDAR-90.md`
  (Day 0/3/7/12 cadence).
- TW-885 gate memo (Angela): 22 hard rules HR-01..HR-22 written as
  testable config assertions.
- TW-284 merged phone lane: provider ports, per-call recording toggle
  with spoken announcement, disclosure-first invariant, fail-closed
  factories — reused, not rebuilt.
- TW-727 note: the ticket text says "Path A: Twilio + OpenAI"; TW-727
  superseded the default provider to Fonoster. This build talks only
  to the TW-284 provider PORTS, so it is correct under either
  selection.

## What was built

| Piece | Where |
|---|---|
| Dialer + gate chain + conversation engine + rollup + purge | `backend/app/services/outbound_calls.py` |
| Calendar port + fail-closed Google seam | `backend/app/services/calendar_providers.py` |
| Line-type + registry ports (fail closed) | `backend/app/services/outbound_lookups.py` |
| Router (settings/revoke/import/list/scrub/opt-out/dial/rollup) | `backend/app/routers/outbound.py`, registered in `app/main.py` |
| Config (kill switches, caller ID, launch gates) | `backend/app/config.py` (TW-884 block) |
| Schema: agent settings, prospects, attempts, suppression, consents, scrub runs, purge log; RLS + org-isolation policies on all 7 | `supabase/migration_outbound_calls.sql` (NOT applied — apply is a separate word) |
| Tests (48) | `backend/tests/test_outbound_calls.py`, `backend/tests/test_calendar_providers.py` |

## Task report

- **RED:** tests written first against fakes in the house
  FakeDB/FakeQuery pattern; collection failed on the missing modules
  exactly as expected. Committed `be04527`.
- **GREEN:** 48/48 new tests pass; TW-284 suite still 24/24.
- **Full suite, paired node-set comparison** (TW-284/TW-769
  methodology): control = clean main `b330a89` (detached worktree),
  same command (`pytest -q --continue-on-collection-errors`).
  Control: 21 failed / 2557 passed / 76 skipped / 9 errors.
  Branch: 21 failed / **2605 passed** / 76 skipped / 9 errors.
  Failed+error node sets are **identical (30 nodes each, empty
  branch-only set)** — zero regressions; the +48 passes are exactly
  the new tests.
- **Ruff:** `ruff check` clean and `ruff format --check` clean on
  every authored file. (`app/config.py` has pre-existing format drift
  in sections this diff does not touch; left alone deliberately.)
- **Router proof:** import smoke in-suite (all 7 routes present).
  `import app.main` fails in this sandbox on the pre-existing xmlsec
  environment error — verified identical on clean main, so it is an
  environment limit, not this diff (same posture as TW-284).

## Guarantees table

| Guarantee | Enforced where | Test |
|---|---|---|
| Global kill switch OFF → no dial, 409 | `outbound_calls_enabled` gate | `test_global_kill_switch_blocks_dial` |
| Per-agent disable/revoke → no dial | agent settings gate | `test_agent_disabled_blocks_dial`, `test_agent_revoked_blocks_dial` |
| Revocation MID-CALL aborts before the next utterance | `_call_alive` (global kill switch + per-agent row, re-read fresh) checked by `_Conversation.speak` before every utterance (TW-766) | `test_revocation_mid_call_aborts_without_booking`, Rex `test_revoke_mid_conversation_stops_utterances`, Rex `test_global_kill_switch_stops_in_flight_call` (TW-889) |
| Revocation at the CLOSE books nothing — and a booking created in the sliver before the abort shows is voided (TW-888) | `book()` re-checks `_call_alive` before any side effect; `_find_demo_slot` re-checks immediately before `create_event`; `close_booking` compensates via the calendar port's `delete_event` and marks `booking_voided` in the audit answers | Rex `test_revoke_at_close_creates_no_booking`, `test_revoke_during_booking_creation_voids_the_event` |
| Test mode: only allowlisted numbers dial | allowlist gate | `test_test_mode_allowlist_refuses_other_numbers` |
| Caller ID: owned number + CNAM "Twistor" + STIR/SHAKEN A, else 503 (HR-03/04) | config gate in dialer | `test_caller_id_must_be_owned_config_with_cnam_and_attestation` |
| Live mode blocked until DNC policy on file + privacy section + web opt-out live (HR-12/21) | launch gates | `test_launch_gates_block_live_mode` |
| Disclosure is utterance one: persona name, "automated assistant", Twistor, sales purpose (HR-01) | opening rides `place_call(say_text=...)`; session row records `ai_disclosure_given` | `test_disclosure_is_first_utterance_with_all_elements` |
| "Are you a robot?" gets a truthful answer, no human claim (HR-02) | engine global branch | `test_robot_question_gets_truthful_answer` |
| Cold dials: verified business landlines ONLY (HR-06) | line-type gate before every dial (HR-05) reads the lookup's `verified` flag: the landline path requires `verified is True`; an unverified landline label takes the HR-07 consent gate (consent-capture lane without PEWC, dials with it). Result logged per attempt | `test_unknown_line_type_fails_closed`, `test_wireless_without_consent_routes_to_consent_lane`, Rex `test_unverified_landline_classification_refuses_dial`, `test_unverified_landline_routes_to_consent_capture`, `test_unverified_landline_with_pewc_consent_dials` (last five: fix round, TW-890) |
| Wireless/residential dial only with PEWC record naming Twistor + AI-voice scope (HR-07) | consent ledger check | `test_wireless_with_written_consent_dials` |
| Unconfigured carrier lookup → 503, never a guessed dial (HR-05) | fail-closed port factory | `test_missing_line_type_provider_fails_closed` |
| Suppression checked AT DIAL TIME, not batch load (HR-09) | ledger re-read in the gate chain | `test_suppression_after_import_blocks_dial_at_dial_time` |
| DNC mid-call: pitch stops, suppression written before the call closes, honored permanently (HR-08/10) | engine DNC branch writes the ledger row first | `test_dnc_mid_call_stops_everything_and_suppresses` |
| Registry scrub required + logged; hits suppressed (HR-11) | `scrub_prospects` + scrub gate in the dialer | `test_scrub_marks_registry_hits_and_logs_metadata`, `test_unscrubbed_prospect_never_dials` |
| Windows are prospect-local; unknown state/TZ never dials (HR-13) | window gate keyed to prospect TZ | `test_outside_window_defers_without_dialing`, `test_unknown_timezone_never_dials` |
| TX + FL never dial until counsel clears (HR-16); state windows loaded (HR-14) | `STATE_RULES` gate | `test_counsel_gated_states_never_dial`, `test_legal_gate_config_matches_memo` |
| 3 attempts per number per rolling 24h, house-wide (HR-15) | attempt-cap gate | `test_three_attempts_per_24h_house_cap` |
| Recording off by default; announcement BEFORE capture in all states; objection stops capture + flags the number permanently (HR-17/18) | TW-284 `set_recording` reused unchanged on the outbound session | `test_recording_announcement_precedes_capture_on_outbound`, `test_recording_objection_stops_and_flags_number` |
| Cadence Day 0/3/7/12; 4th touch rests the prospect | `due_prospects` + finalize | `test_cadence_day3_not_day1`, `test_fourth_touch_rests_prospect` |
| Daily cap enforced across the queue | `run_dialer` | `test_run_dialer_honors_daily_cap` |
| Demo booking: window + duration + buffer; never double-books; event carries prospect + audit answers, no raw phone | calendar port + slot finder | `test_happy_path_books_demo_with_audit_answers`, `test_demo_slot_respects_buffer_and_never_double_books` |
| Booking seam down → callback disposition, never a crashed call (self-healing) | `close_booking` degradation | `test_missing_calendar_degrades_to_callback_not_failure` |
| Booking confirmations identify Twistor + the automated assistant + a human reschedule path (HR-22) | event description | asserted in `test_happy_path_books_demo_with_audit_answers` |
| Talk track verbatim where the pack quotes it: ServiceTitan branch, send-info review path, honest no-date start line | engine branches | `test_servicetitan_objection_branch`, `test_send_info_books_review_only_audit`, `test_honest_no_date_start_line` |
| Voicemail carries disclosure + callback number (47 CFR 64.1200(b)) | voicemail branch | `test_voicemail_gets_disclosure_message_and_cadence` |
| Rollup: conversations/audits/demos/DNCs + escalations WITH transcripts | `daily_rollup` | `test_daily_rollup_counts_and_escalations_carry_transcripts` |
| Retention executed, not just declared: expired transcripts purged + purge-logged; suppression never purged (HR-20) | `purge_expired` | `test_retention_purge_expired_transcripts_only` |
| Transcripts/numbers bronze-only; API surfaces masked only (HR-19) | migration contract + router masking | `test_migration_contract`, router `_masked` |

## Coverage and known gaps (honest slice-1 boundaries)

- **Conversation classification is keyword-based.** The engine is a
  deterministic state machine over a responder port; tests inject
  scripted responders. The production responder (live voice-agent
  bridge that hears, classifies, and speaks) is go-live wiring — the
  HTTP dial endpoint therefore fails closed with 503 in slice 1.
- **Line-type lookup and registry access are ports, not wiring.** The
  production factories raise NotConfigured (fail closed). Wiring
  Twilio Lookup / registry access lands at go-live with Justynn's
  number purchase.
- **Google Calendar is a fail-closed seam** behind the same port;
  tests use an in-memory provider. The confirmation a prospect
  receives in production is the calendar invite itself (HR-22 content
  is in the event description).
- **Stubbed per the memo:** HR-12 (written DNC policy document) is
  Angela's companion filing — encoded as the
  `outbound_dnc_policy_on_file` launch gate, default False. HR-21
  (privacy-policy section + public web opt-out page) is Sofia's
  surface — encoded as launch gates, default False; the ledger
  intake endpoint (`POST /outbound/opt-out`) is built and real.
  HR-20 retention periods are the memo's proposed schedule (pending
  Justynn's approval); the purge job covers DB transcripts —
  provider-side audio purge is go-live wiring.
- **Persona:** the slot is the agent-settings `persona_name`
  (config-driven, unchanged). The shipped default is now **Tori
  Calloway** (TW-886, approved by Justynn 2026-11-01); slice 1
  shipped a "Nova" placeholder default, and the fix round renamed
  the constant to `DEFAULT_PERSONA_NAME`, changing only the value.
- **Migration is written, not applied.** Production apply is a
  separate word (the decs T7 pattern), and the router TestClient
  suites cannot run in this sandbox (pre-existing xmlsec limit, same
  on clean main).

## Plan-safety notes

- Gates after this build: Rosa review + Rex retest before merge;
  merge is Justynn's separate word. Go-live additionally requires
  Angela's section 8 counsel items, the number purchase, and TW-704.
- The TW-882 batch's 190 phones are untouched by this build: import
  sorts nothing until a human runs it, and every number still has to
  survive scrub + line-type + consent gates at dial time.

## Fix round — Rosa REQUEST CHANGES + Rex findings (2026-11-01)

Rosa's review of the slice-1 build returned REQUEST CHANGES with 3
blockers (matching Rex's 3 retest findings) and 5 majors; Rex's
adversarial suite (44 tests, committed `4ef027e`) went 41 kills /
3 findings. This round closes all of it. RED for the round is Rex's
3 committed reproductions plus 8 new proofs in
`tests/test_outbound_calls.py` (checkpoint `80cdb90`, all failing
against the slice-1 build); GREEN is this section's build.

**Corrections to the slice-1 record (Rosa B1/B3):** two rows of the
guarantees table above overclaimed at slice 1. The revocation row
cited a test that revokes at Q1 for a claim that included "no
booking" at the close — the close path was NOT proven (and was in
fact broken, TW-888). The HR-06 row claimed VERIFIED landlines; the
slice-1 gate read the line-type label but never the lookup's
`verified` flag (TW-890). Both rows are corrected above with the
fix-round tests that now actually prove them.

| Fix | Change | Proof |
|---|---|---|
| TW-888 (B1) phantom booking | `book()` re-checks liveness before any side effect; `_find_demo_slot` re-checks immediately before `create_event`; on abort after a create, `close_booking` voids the event via the new calendar-port `delete_event` and records `booking_voided` in the audit answers | Rex close test + `test_revoke_during_booking_creation_voids_the_event` (revocation lands inside `create_event` itself) |
| TW-889 (B2) global brake | one liveness definition, `_call_alive` = global flag AND per-agent row, re-read fresh; used by `speak()` and the booking path | Rex `test_global_kill_switch_stops_in_flight_call` |
| TW-890 (B3) verified flag | landline path requires `verified is True`; unverified labels route through the HR-07 consent gate | Rex unverified test + route/consent semantics pair (see interpretation note) |
| M1 owner-callback escalation | `place_outbound_call` sets `answers["owner_callback"]` on owner-reached callback outcomes (the rollup read it; nothing set it — dead code) | `test_owner_callback_request_escalates_in_rollup` + gatekeeper negative control |
| M2 TX Sunday window | `_window_open` enforces `sunday_start` (TX noon) on Sundays | `test_texas_sunday_window_opens_at_noon` (direct gate test; TX stays counsel-gated off for dials) |
| M3 recording config pre-dial | voice-provider resolution hoisted before `place_call` (503 with zero dial/session/attempt side effects); a mid-setup toggle fault degrades to an unrecorded call that still ends with a disposition | `test_recording_config_failure_lands_before_the_dial` |
| M4 batch fault isolation | `run_dialer` catches provider exceptions per prospect as dead-letter result records; `_handle_recording_objection`'s toggle is guarded | `test_run_dialer_isolates_per_prospect_failures`, `test_recording_objection_survives_toggle_failure` |
| M5 persona default | `DEFAULT_PERSONA_NAME = "Tori Calloway"` (slot config-driven, value only) | existing persona assertions now bind the real name |
| N1 purge proof | `purge_expired` writes its purge-log row on EVERY run, including no-ops | `test_purge_logs_every_run_even_when_nothing_expires` |

**Calendar port change:** `CalendarProvider.delete_event(event_id)
-> bool` added (ABC + fail-closed Google seam + test fakes). It
exists solely for TW-888 compensation.

**Interpretation note (TW-890):** Rosa's B3 text says unverified
"falls to the blocked path"; the fix brief said "consent-required
bucket". TW-890's own fix text names "blocked_line_type /
consent-capture handling". Chosen: consent-capture (the HR-07 gate)
— an unverified landline without PEWC lands in the
`consent_capture` lane with a `blocked_consent_required` attempt,
never dialed; with a valid PEWC record it dials lawfully, exactly
like a consented wireless number. Flagged for Rosa's re-review.

**Nits disposition:** N1 done (above). N2 (empty holiday sets incl.
CO) — field present per HR-14; date content is Angela's pre-go-live
task, noted on TW-884. N3 (opt-out endpoint is org-authenticated;
HR-21's public surface needs an unauthenticated rate-limited
variant) — flagged on TW-884 for Angela/Sofia at go-live wiring;
not redesigned here. N4 (review-unit size) — noted; this round's
RED/GREEN checkpoint split is the mitigation.

**Known gap added this round:** if `set_recording(on=True)` ever
partially succeeds at the production bridge (announcement spoken,
capture started) and then raises, the M3 degrade proceeds with
`recording_on=False` while capture may be live. The pre-dial
resolution removes the realistic cause (unconfigured provider);
partial-toggle semantics are a go-live bridge item, same class as
the responder bridge.

**Verification (fresh, this round):**
- Outbound family: `test_outbound_calls.py` 55 + `test_calendar_providers.py` 4 + Rex `test_outbound_rex_retest.py` 44 = **103/103 pass**; TW-284 suite 24/24.
- Paired full suite vs clean main `b330a89` (same command as
  slice 1): control 21 failed / 2557 passed / 9 errors; branch
  21 failed / **2660 passed** / 9 errors. Failed node sets
  identical, error counts identical — **zero regressions**; the
  +103 passes are exactly slice 1's 48 + Rex's 44 + this round's 11.
- `ruff check` + `ruff format --check` clean on every touched file.

## TW-891 — compensation failure is recorded, never silent (2026-11-01)

Rosa's re-review APPROVED the fix round with one nit (R1):
`_void_booking` never verified or recorded its own result. Rex's
retest round 2 turned the nit into a MEDIUM finding with three
reproductions (suite committed `62db6ff`): delete raises → swallowed,
phantom survives with zero trace; delete returns False →
`booking_voided=True` written anyway (the audit trail lies); adapter
with no `delete_event` → getattr skip, same silent phantom.

**Change** (`app/services/outbound_calls.py`): `_void_booking` now
treats only `delete_event(...) is True` as voided. Any other outcome
records `booking_void_failed=True` + `orphaned_event_id=<id>` in the
audit answers — and `booking_voided` is written only on a confirmed
void. `daily_rollup` surfaces the day's orphans as
`orphaned_events: {count, event_ids}`, so a stranded demo is a
cleanup work-order, not an accident. Best-effort semantics kept: a
void failure never masks the abort.

**RED → GREEN:** Rex's 3 acceptance tests failed against `62db6ff`
(3 failed / 22 passed in `test_outbound_rex_retest2.py`); one added
proof, `test_failed_void_records_orphan_and_rollup_surfaces_it`
(RED checkpoint `0d93ec7`), covers the rollup surfacing Rex's tests
don't reach. After the fix: Rex round-2 **25/25**, outbound family
**129/129** (`test_outbound_calls` 56 + `test_calendar_providers` 4
+ Rex round-1 44 + Rex round-2 25), TW-284 suite 24/24.

**Paired full suite vs clean main `b330a89`:** control 21 failed /
2557 passed / 9 errors; branch 21 failed / **2686 passed** / 9
errors. Failed+error node sets identical (30 nodes, empty diff) —
**zero regressions**; the +129 over control is exactly the outbound
family, and the +26 over the fix-round branch (2660) is exactly
Rex's round-2 suite (25) + the one TW-891 proof.
`ruff check` + `ruff format --check` clean on both touched files.
