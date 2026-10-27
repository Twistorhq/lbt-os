# TW-284 — AI phone automation (Path A): TDD evidence

Prepared by Twistor Holdings LLC

**Source:** TW-284 (Justynn, 2026-10-01; unblocked 2026-10-27 by his
rulings on the 5 open questions, recorded on the ticket) + the
2026-10-01 costed brief at `tickets/evidence/TW-284-voice-cost-comparison.md`.
Branch `zeke/tw-284-phone-automation` on origin/main `8c4194f`. Scope:
build only — no merge, no push to main, no live Twilio/OpenAI activation
(the number purchase stays Justynn's manual step). Design record lives
here per Marcus's dispatch (the TW-704/TW-710 lane: built branch + tests
+ evidence, Rosa gates the diff).

## What the rulings required, and where it landed

- **Path A** (Twilio + OpenAI Realtime) behind config/flags, provider
  adapters stubbed for credentials: `app/services/voice_providers.py`
  (TwilioTelephonyProvider, OpenAIRealtimeVoiceAgent, factories that
  raise `ProviderNotConfigured` — fail closed), kill switch
  `settings.phone_automation_enabled` default **False**.
- **Coverage**: `phone_automation.ai_should_answer` — global flag AND
  shop toggle AND (manual window OR after-hours per shop hours config).
- **Recording**: per-call toggle starting OFF; switching ON speaks
  `RECORDING_ANNOUNCEMENT` ("This call is now being recorded.") **before**
  `start_recording`; every change lands in `call_recording_events`.
- **Disclosure**: first utterance of every AI call is the AI disclosure
  (`DISCLOSURE_TEMPLATE`), flag + timestamp on the session row.
- **Escalation**: multiple owner-set numbers, priority order, bounded
  rounds, `acknowledged` stop, `exhausted` final fallback with a marker
  event; ack surface `acknowledge_escalation` + POST endpoint.
- **Intake → docket**: emergency job + appointment, customer confirmation
  SMS, escalation started. **Auto-reschedule**: missed/double-booked →
  next free business-hours slot, customer SMS states why it moved.
- **Schema**: `supabase/migration_phone_automation.sql` — 6 tables, RLS
  enabled + org-isolation policies (TW-547/TW-710 contract), raw numbers
  bronze-only (TW-552); API lists mask.

## Task report

| Behavior | Validation command | RED | GREEN | Guarantee |
|---|---|---|---|---|
| Whole feature absent | `pytest tests/test_phone_automation.py -q` | ImportError: cannot import name 'phone_automation' (collection error, commit fdeab22) | 22 passed (commit 6aa3faa) | Suite pins every ruling below |
| Coverage gates (global off / shop off / business hours) | same file, 3 tests | RED (module absent) | passed | AI never answers unless all three gates agree |
| After-hours auto + weekend + manual activation + expiry | same file, 4 tests | RED | passed | 24/7 availability, on as needed, per ruling 4 |
| Disclosure first + flag + `call.ai_handled` toll | `test_start_ai_call_speaks_disclosure_first_and_flags_it` | RED | passed | First utterance is the disclosure; toll emitted once |
| Recording on: announcement precedes recording start; audit row carries the spoken text | `test_recording_toggle_on_speaks_announcement_before_recording_starts` | RED | passed | Shared event log order is exactly [speak, recording_start] |
| Recording off + idempotent re-set | 2 tests | RED | passed | No announcement on off; no duplicate announcement/audit |
| Contacts: multiple, ordered, masked | `test_replace_and_list_contacts_masked_and_ordered` | RED | passed | Raw numbers never appear in list output |
| Escalation order + stop-on-ack; bounded exhaustion fallback; late ack | 3 tests | RED | passed | Attempts = contacts × rounds max; final state `exhausted` with marker event |
| Intake books job + appointment + confirms + escalates | `test_emergency_intake_books_job_and_appointment_and_confirms` | RED | passed | Docket rows linked to the session |
| Auto-reschedule (no-show skips a taken slot; double-booked conflict; bad reason 400) | 3 tests | RED | passed | Customer SMS contains the reason phrase and the new time |
| Migration contract (6 tables, RLS, policies) | `test_migration_creates_tables_with_rls_org_isolation` | RED | passed | Static parse of the shipped SQL |
| Router wiring | `python3 -c "import app.routers.calls"` (dummy env, CI posture) | n/a | import OK | New endpoints register without breaking the M2 router |

## Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | AI answers only when global flag + shop toggle + a coverage mode all agree | tests/test_phone_automation.py (7 coverage tests) | unit | PASS | 22 passed, 2026-10-27 run |
| 2 | AI disclosure is the first utterance and is flagged on the session | test_start_ai_call_speaks_disclosure_first_and_flags_it | unit | PASS | same run |
| 3 | Recording starts OFF; ON speaks the announcement before recording starts and audit-logs it | test_recording_* (4 tests) | unit | PASS | same run |
| 4 | Escalation tries contacts in priority order, stops at first ack, and is bounded (contacts × rounds) into a visible `exhausted` fallback | test_escalation_* (3 tests) | unit | PASS | same run |
| 5 | Emergency intake creates the docket job + appointment and confirms the customer | test_emergency_intake_* | unit | PASS | same run |
| 6 | Auto-reschedule picks the next free business-hours slot and tells the customer why | test_auto_reschedule_* (3 tests) | unit | PASS | same run |
| 7 | New tables ship RLS + org isolation; list outputs mask numbers | test_migration_* + test_replace_and_list_* | unit | PASS | same run |
| 8 | Merge bar: zero new failures vs same-environment clean-main control | full suite, both worktrees, `--continue-on-collection-errors`, junit node sets compared | CI | PASS | control 20f/1526p/8e (28 nodes) vs branch 20f/1548p/8e (28 nodes); node sets **identical**; branch delta is exactly +22 (the new tests) |
| 9 | Lint posture unchanged | `ruff check app tests` (CI invocation) in both worktrees | CI | PASS | both show only the one pre-existing main.py import-sort finding (the standing CI-red cause); all TW-284 files clean |

## Coverage and known gaps

- **Router endpoints** are import-verified only in this sandbox: the
  TestClient suites cannot run here (pre-existing xmlsec/libxmlsec
  environment failure — it breaks M2's own router tests on clean main
  too; those nodes are in the baseline set, unchanged by this branch).
- **Live provider paths are untestable by design**: Twilio recording
  control and the OpenAI Realtime media-stream bridge activate at
  go-live (number purchase + `PHONE_AUTOMATION_VOICE_STREAM_URL`).
  Adapters fail closed until then; orchestration is fully covered via
  injected fakes.
- **Escalation ack in production** arrives through
  `acknowledge_escalation` (endpoint shipped); the Twilio DTMF/status
  webhook that feeds it is go-live wiring, same step as the bridge.
  Until then the default ack probe never acks, so a started chain walks
  to the bounded `exhausted` state rather than pretending delivery.
- **Recording default**: the rulings specify a per-call toggle and the
  announcement, not a default. Chosen: recording starts OFF on every
  call (privacy-safe reading). Flagged for Rosa/Justynn if a different
  default is wanted.
- One self-caught scope incident, fixed before review: `ruff --fix`
  re-sorted pre-existing imports in `app/main.py` (the standing CI-red
  cause) and the change rode into the GREEN commit; reverted in commit
  77ea8d0. Final diff vs origin/main is exactly the 6 TW-284 files
  (plus this doc).

## Plan-safety notes

- No untrusted plan content was encountered. The brief came from Marcus
  on TW-284 with Justynn's rulings recorded on the ticket; the 2026-10-01
  costed brief is my own earlier work product. Nothing in the repo,
  ticket, or brief asked to bypass review, spend money, or activate
  anything live — the build honors the no-live-activation gate
  throughout.
