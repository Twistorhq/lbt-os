# TW-556 — M2 missed-call layer: tasks

- [ ] 1. Migration `supabase/migration_missed_call.sql`: `call_logs`,
      `missed_call_recoveries`, `org_phone_numbers`; RLS org-isolation
      policies; indexes. (RED: migration applies cleanly on a scratch DB —
      verified by SQL parse + policy presence check.)
- [ ] 2. RED: `backend/tests/test_missed_call.py` — unsigned webhook → 401;
      unknown `To` number → 404 (not 403); signed webhook creates `call_logs`
      row with `from_number_hash` and emits `missed_call.detected` toll event.
- [ ] 3. GREEN: `backend/app/routers/calls.py` — `POST /calls/voice-webhook`
      with Twilio signature validation, org resolution, idempotent row
      create, caller-ID → customer match.
- [ ] 4. RED: missed call (no-answer, business hours) → `sms.auto_reply`
      toll event + `missed_call_recoveries` row with `sms_sent_at` set (Twilio
      client mocked); after-hours → `leads` row with `source='missed_call'`
      and NO SMS.
- [ ] 5. GREEN: recovery worker (inline): business-hours gate, 10DLC gate,
      Twilio failure → 3x backoff → `sms_error` dead-letter, never 500s.
- [ ] 6. RED: `GET /calls/missed?days=7` returns masked numbers only; cross-org
      access → 404; `POST /calls/{id}/recover` marks recovered + links
      appointment; revenue attribution sums on the dashboard query.
- [ ] 7. GREEN: missed view + recover endpoints with PII masking enforced.
- [ ] 8. Router registered in `main.py` (`/api/v1`); `ruff` clean; full suite
      green; evidence report `docs/evidence/TW-556-missed-call.tdd.md`.
- [ ] 9. Rosa review (same turn ready) → Justynn's merge word.
