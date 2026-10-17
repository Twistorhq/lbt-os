# TDD Evidence — TW-556 M2 missed-call layer (Jobber wedge)

## 1. Source
- Ticket: TW-556 (reporter Justynn, greenlit 2026-10-16)
- OpenSpec: `openspec/changes/tw-556-m2-missed-call/` (proposal.md, design.md, tasks.md)
- Branch: `zeke/tw-556-m2-missed-call` on Twistorhq/lbt-os worktree

## 2. Task report
Per behavior, RED then GREEN. RED commit: `TW-556 RED: missed-call layer tests`
(tests failed — `app.services.call_service` did not exist). GREEN commit:
`TW-556 GREEN: M2 missed-call layer (...)`.

- **Unsigned webhook rejected (401), no row written.** RED: `AttributeError:
  module 'app.services' has no attribute 'call_service'`. GREEN: Twilio
  HMAC-SHA1 signature validation; unsigned → 401. Command:
  `~/workspace/.venvs/lbt-os/bin/python -m pytest tests/test_missed_call.py -q`
  → 8 passed.
- **Unknown destination number → 404 (not 403).** Org resolved by the shop's
  Twilio number via `org_phone_numbers`; no match → 404. No org-existence leak.
- **Signed webhook creates bronze `call_logs` row** with `from_number_hash`
  (SHA-256) and emits `missed_call.detected` toll event (idempotency key =
  CallSid; duplicate deliveries return the existing row, single toll).
- **Business-hours no-answer → instant auto-SMS** (Twilio client mocked in
  tests), `sms.auto_reply` toll event, `missed_call_recoveries.sms_sent_at` set.
- **After-hours → callback lead** (`source='missed_call'`,
  `follow_up_at` = next 8am local), zero SMS sent.
- **Money view masks PII**: `GET /calls/missed` returns `(***) ***-0199`;
  raw numbers never in responses. Cross-org recover → 404.
- **Manual recovery** marks `recovered_at`, links appointment, records
  `revenue_attributed` (400.00 asserted).
- **Self-healing**: Twilio send failure → 3x backoff → `sms_error` dead-letter;
  `sms_sent_at` stays NULL; webhook already returned 200. Verified by code
  path (retry loop) — live Twilio failure not simulated (no network in tests).

Validation commands (all fresh, this session):
- `pytest tests/test_missed_call.py -q` → 8 passed
- `pytest tests/ -q -p no:cacheprovider` → 234 passed, 8 subtests passed
- `ruff check` (CI 0.16.8, via jalen-ci venv) on all touched files → clean
- Test-isolation fix: `settings.twilio_auth_token` pinned via
  `mock.patch.object` in setUp (the settings singleton is import-order
  dependent; env setdefault was racy and failed 4 tests under full-suite runs).

## 3. Guarantees table
| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Unsigned webhook → 401, no row | test_missed_call.py::test_unsigned_webhook_rejected_401 | unit | PASS | 8 passed run |
| 2 | Unknown To → 404 not 403 | test_unknown_to_number_404_not_403 | unit | PASS | 8 passed run |
| 3 | Signed webhook → call log + hash + detection toll | test_signed_webhook_creates_call_log_and_detection_toll | unit | PASS | 8 passed run |
| 4 | Duplicate delivery idempotent (1 row, 1 toll) | test_duplicate_webhook_delivery_is_idempotent | unit | PASS | 8 passed run |
| 5 | After-hours → callback lead, no SMS | test_after_hours_creates_callback_lead_no_sms | unit | PASS | 8 passed run |
| 6 | Missed view masks numbers | test_missed_view_masks_numbers | unit | PASS | 8 passed run |
| 7 | Cross-org recover → 404 | test_cross_org_missed_call_returns_404 | unit | PASS | 8 passed run |
| 8 | Recover marks + links appointment + revenue | test_recover_marks_recovered_and_links_appointment | unit | PASS | 8 passed run |
| 9 | No regressions | `pytest tests/` full suite | CI | PASS | 234 passed |
| 10 | Lint clean | `ruff check` (CI's 0.16.8) | CI | PASS | All checks passed |

## 4. Coverage and known gaps
- `POST /calls/sms-webhook`: implemented (signature validation + org
  resolution + acknowledge); intent routing (BOOK/STOP) is a documented
  follow-up, untested by design.
- `recording_url`/`transcription`: stored bronze-only, excluded from v1 API
  responses (per PII doctrine). No test asserts their exclusion explicitly —
  they are never selected in `get_missed_calls`.
- Live Twilio send path: mocked in tests. Webhook-ready; the Twilio voice
  number purchase is Justynn's manual step (per architecture).
- `time.sleep` backoff in the retry loop: 0.5s/1s/2s worst case ~3.5s added to
  a webhook call; Twilio's webhook timeout is 15s. Acceptable for v1.
- Frontend: v1 is API-only; the missed-calls panel on the dashboard follow-up
  surface is a follow-up ticket.

## 5. Plan-safety notes
- Architecture doc (TW-547) scanned before implementation: no embedded
  instructions, no override phrases, no destructive steps. The AGPL
  yellow-zone warning from the plan-safety check was carried forward as a
  build constraint (no AGPL code used; twilio SDK is MIT).
- No secrets, tokens, or PII in the diff. Test fixtures use obviously-dummy
  numbers (+13035550199 etc.). The `_SMS_TEMPLATE` contains no real copy
  beyond the opt-out line required by the TCPA consent model.
