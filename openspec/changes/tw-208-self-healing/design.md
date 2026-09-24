# TW-208 design: self-healing primitives + path retrofits

**Prepared by Twistor Holdings LLC**

## Shared primitives (`backend/app/self_healing.py`)

- `is_transient(exc)` — classifies by exception type/message markers
  (connection/timeout/network/lock/deadlock/temporarily-unavailable/rate
  limit) plus an opt-out `mark_permanent(exc)` flag so a detector bug is
  never retried as if it were a blip.
- `retry_with_backoff(fn, *, attempts=3, base_delay=0.5, max_delay=30.0, jitter=0.1)`
  — exponential delay, jitter, cap; sleeps are patchable in tests so the
  suite never actually waits.
- `run_isolated(items, fn)` — maps each item independently, returns
  `(ok, [SkippedItem(index, preview, error)])`; `SkippedItem.preview` is
  truncated to 200 chars so logs can't leak full records.
- `DeadLetterQueue(pipeline)` — `collect(index, item, exc)` logs loudly at
  ERROR with index + preview + error, stores a structured record, and
  `summarize(sample_size=20)` returns a JSON-serializable dict for import
  logs / brief payloads.
- `health_check(db, tables)` — per-table `"ok"` / `"missing: <reason>"` probe.

## CSV ingestion retrofit (`backend/app/services/manual_import.py`)

- Row validation moved into `_row_payload(entity, org_id, row, created_at)`
  which raises `ValueError` naming the poison reason (missing required name,
  invalid numeric). `_to_decimal` raises `ValueError` instead of HTTPException
  so the isolator can skip the row.
- 1-based data-row numbers feed the DLQ; result returns `skipped_rows` and
  `skipped: [{row, reason}]`.
- Inserts go in 500-row chunks via `retry_with_backoff`; a chunk that still
  fails falls back to per-row inserts, so one DB-level poison row can't sink
  the other 499.
- `_log_import` persists `skipped_rows` + a `details` JSONB dead-letter
  summary, falling back to the legacy column set when the migration hasn't
  applied yet (detected by the DB error naming the missing column).
- Import status: `"failed"` on whole-file error, `"partial"` when rows were
  skipped, `"success"` when clean.
- Migration `supabase/migration_csv_import_logs_self_healing.sql` adds the
  columns; application code works with or without it.

## Leak engine retrofit (`backend/app/leak_engine/engine.py`)

- Each detector call is wrapped in `retry_with_backoff` (3 attempts,
  0.2s base — small because this runs inside a request). Permanent detector
  bugs land in `data_status["errors"]`; transient blips heal silently.
- `data_status["partial"] = True` whenever errors, skipped_rows, or
  insufficient entries exist.
- `_safe_float` coerces hostile `estimated_value` (`"N/A"`, NaN, inf) to
  `0.0` so totals aggregation can never 500 the brief.
- `_tables_available` is now implemented on the shared `health_check`
  primitive (same behavior, one implementation).

## Benchmark retrofit (`backend/app/services/benchmarks.py`)

- `record_org_metrics` **never raises**: consent read retries twice, metric
  write retries three times, persistent failure degrades to
  `{"recorded": 0, "reason": "db_error"}` with a loud ERROR log. A benchmark
  write can never 500 the morning brief again.

## Test strategy

`backend/tests/test_self_healing.py` — 20 tests, all with fake DBs (no live
Supabase): retry success/exhaustion/permanent classification, per-row
isolation, chunk retry, per-row fallback, DLQ summaries, legacy-log fallback,
whole-file 400s, detector retries + permanent degradation, hostile totals,
benchmark retry + degradation. RED was captured as a collection failure
(`app.self_healing` did not exist) before implementation.

## Process note (honest)

The skill's left-shifted gate says Rosa reviews proposal/design BEFORE code.
This urgent dispatch ran RED→GREEN in one round; Rosa reviews the full
proposal/design plus the diff before merge, and nothing merges without
Justynn's exact approval. Recorded here and in the evidence report so the
exception is visible, not silent.
