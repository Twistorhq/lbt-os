# TW-208 tasks

**Prepared by Twistor Holdings LLC**

- [x] RED: write `backend/tests/test_self_healing.py` (20 tests) — committed
      separately, collection failed on missing `app.self_healing` (proof)
- [x] GREEN: `backend/app/self_healing.py` — `is_transient`, `mark_permanent`,
      `retry_with_backoff`, `run_isolated`, `SkippedItem`, `DeadLetterQueue`,
      `health_check`
- [x] GREEN: retrofit `backend/app/services/manual_import.py` — per-row
      isolation, chunked inserts with retry + per-row fallback, `skipped_rows`
      + `details` in `_log_import` with legacy fallback, `partial` status
- [x] GREEN: retrofit `backend/app/leak_engine/engine.py` — detector retries,
      `data_status["errors"]` + `partial` flag, `_safe_float` totals,
      `health_check`-backed table probe
- [x] GREEN: retrofit `backend/app/services/benchmarks.py` — never-raises
      `record_org_metrics` with retries + `db_error` degradation
- [x] Migration `supabase/migration_csv_import_logs_self_healing.sql`
      (`skipped_rows INT`, `details JSONB`)
- [x] Test-fake corrections during GREEN (FakeDB write-through inserts,
      FlakyInsertDB consent-read support, honest `partial` status expectation)
- [x] Full backend suite green: 92 passed + 8 subtests, CI's exact dummy env
- [x] `ruff check app tests` clean
- [x] OpenSpec proposal/design/tasks + `openspec validate`
- [x] TDD evidence report `docs/evidence/TW-208-self-healing.tdd.md`
- [ ] Rosa review of proposal + design + diff (must happen before merge)
- [ ] Justynn's exact approval (must happen before merge)
- [ ] Follow-up ticket: notification-path self-healing + repo-wide rollout
      (deferred until after this slice's Rosa review)
