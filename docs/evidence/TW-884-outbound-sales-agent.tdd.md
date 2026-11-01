# TW-884 — Outbound sales agent, slice 1 (epic TW-883): TDD evidence

Prepared by Twistor Holdings LLC

**Branch:** `zeke/tw-884-outbound-calls` from lbt-os main `b330a89`.
**Checkpoints:** RED `be04527` (tests only) → GREEN (this build).
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
| Revocation MID-CALL aborts before the next utterance; no booking | `_Conversation.speak` re-reads settings every utterance (TW-766) | `test_revocation_mid_call_aborts_without_booking` |
| Test mode: only allowlisted numbers dial | allowlist gate | `test_test_mode_allowlist_refuses_other_numbers` |
| Caller ID: owned number + CNAM "Twistor" + STIR/SHAKEN A, else 503 (HR-03/04) | config gate in dialer | `test_caller_id_must_be_owned_config_with_cnam_and_attestation` |
| Live mode blocked until DNC policy on file + privacy section + web opt-out live (HR-12/21) | launch gates | `test_launch_gates_block_live_mode` |
| Disclosure is utterance one: persona name, "automated assistant", Twistor, sales purpose (HR-01) | opening rides `place_call(say_text=...)`; session row records `ai_disclosure_given` | `test_disclosure_is_first_utterance_with_all_elements` |
| "Are you a robot?" gets a truthful answer, no human claim (HR-02) | engine global branch | `test_robot_question_gets_truthful_answer` |
| Cold dials: verified business landlines ONLY (HR-06) | line-type gate before every dial (HR-05), result logged per attempt | `test_unknown_line_type_fails_closed`, `test_wireless_without_consent_routes_to_consent_lane` |
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
- **Persona is a placeholder** ("Nova") behind the agent-settings
  `persona_name` slot; Imani's TW-886 name/voice drops in without a
  code change.
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
