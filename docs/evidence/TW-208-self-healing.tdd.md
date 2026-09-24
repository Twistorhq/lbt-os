# TW-208 evidence report — self-healing pipeline standard (leak engine + CSV ingestion)

**Prepared by Twistor Holdings LLC**

Ticket: TW-208 · Branch: `feature/tw-208-self-healing` · Author: Zeke Okafor
Reviewer: Rosa Delgado (pending) · Date: 2026-09-24

## Source

Ticket TW-208 (reporter: Justynn): *"I want my pipelines to be self healing
so one error doesn't crush the full process."* Scope for this run: the leak
engine and CSV ingestion paths (benchmark recording is inline in the brief
path, so it rides along). Notifications and the repo-wide rollout are
deferred to a follow-up ticket after Rosa reviews this slice.

Plan: `openspec/changes/tw-208-self-healing/` (proposal.md, design.md,
tasks.md) — `openspec validate --changes`: **1 passed, 0 failed**.

## RED / GREEN history

- **RED (test, committed separately as `48be168`)**: the 20 tests were
  written first; collection failed because the implementation did not exist:

  ```
  ImportError while importing test module '.../tests/test_self_healing.py'.
  E   ImportError: cannot import name 'self_healing' from 'app' (.../app/__init__.py)
  ```

- **GREEN (this round)**: `backend/app/self_healing.py` created and the
  three paths retrofitted. `pytest tests/test_self_healing.py -q` →
  **20 passed**. During GREEN three test-fake bugs surfaced and were fixed
  in the test file (not by weakening assertions): `FakeDB` inserts wrote to
  a copied list instead of the shared table, `FlakyInsertDB.execute()` always
  returned `[]` (hiding the consent read), and one log-status expectation
  said `"success"` where the honest implementation reports `"partial"`.
- **Full suite** (CI's exact dummy env from `.github/workflows/ci.yml`):
  `pytest tests/ -q` → **92 passed, 8 subtests passed**.
- **Lint**: `python3 -m ruff check app tests` → **All checks passed!**
- Two lint fixes during GREEN were real code issues (unused locals in tests,
  unsorted import block), fixed by deletion/reordering. Separately, the diff
  intentionally adds ~10 `# noqa` directives (broad `except Exception`
  markers in the self-healing machinery): each is commented, and broad-except
  *is* the mechanism here — isolation, classification, and DLQ capture only
  work by catching broadly. Ruff is clean with those documented exceptions.

## Fix round (Rosa REQUEST_CHANGES → this commit)

Rosa's review found three majors plus nits; all fixed here:

1. **Evidence-doc accuracy** — the first version claimed "zero new ignores"
   (false: ~10 commented `# noqa`), "200-char" preview truncation (actual:
   120), and an ERROR-log assertion test that didn't exist. All corrected; a
   real ERROR-log test now exists (`test_dlq_logs_error_with_redacted_preview`).
2. **At-least-once insert semantics** — documented explicitly in the module
   docstring and design.md: ambiguous chunk failures (committed server-side,
   response lost) can re-insert rows on retry. A visible, dedupe-able
   duplicate beats silent data loss, so the pipeline retries rather than
   risks dropping; validation-rejected rows are never retried.
3. **PII redaction in DLQ previews** — `_preview` now redacts email/phone-shaped
   values (key- and pattern-based) before they reach logs or JSONB; covered
   by `test_preview_redacts_pii_shaped_values` and the ERROR-log assertion test.
4. **Nits** — jitter applied before the `max_delay` cap (true ceiling, pinned
   by `test_retry_delay_never_exceeds_max_delay`); removed unreachable
   `assert/raise` tail; status codes match as word-boundary tokens
   (`test_transient_status_codes_need_word_boundaries`); `run_isolated`
   caches the preview; `health_check` documents its `id`-column assumption
   and has a dedicated test; `_log_import` tries the legacy shape on *any*
   extended-insert failure and logs loudly if both fail; design.md synced to
   the implementation (signature, `"unreachable:"`, 120 chars).
- New tests in the fix round: 6 (26 total). `pytest tests/ -q` →
  **98 passed, 8 subtests passed**; `ruff check app tests` clean.

## Guarantees

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|--------------------|----------------------|------|--------|----------|
| 1 | One error never crushes the full process: 20 RED-first tests fail before, pass after | `backend/tests/test_self_healing.py` | RED | PASS | RED: `ImportError: cannot import name 'self_healing'`; GREEN: `20 passed` |
| 2 | Poison CSV rows are skipped loudly with row numbers + reasons; good rows import | `test_poison_rows_skipped_good_rows_imported` | unit | PASS | 2 imported, 2 skipped, `skipped[0]["row"] == 2` |
| 3 | Transient chunk failures retry, then all rows land | `test_transient_chunk_failure_retries_then_imports_all` | unit | PASS | `attempts >= 2`, 2 leads persisted |
| 4 | DB-level poison row isolated by per-row fallback after batch failure | `test_db_level_poison_row_isolated_by_per_row_fallback` | unit | PASS | `["Adaeze", "Chidi"]` persisted, POISON row in DLQ |
| 5 | Import log records exact skipped details; legacy schema falls back | `test_import_log_records_skipped_details`, `test_import_log_falls_back_when_details_columns_missing` | unit | PASS | `skipped_rows == 1`, `status == "partial"`, details JSONB present; legacy insert succeeds |
| 6 | Whole-file failures stay honest 400s (not swallowed by isolation) | `test_whole_file_failures_still_reject` | unit | PASS | empty CSV → 400 |
| 7 | Transient detector failures retry; permanent failures degrade into `data_status.errors` with `partial: true` | `test_transient_detector_failure_retries`, `test_permanent_detector_failure_degrades` | unit | PASS | flaky detector called ≥2×; bad detector in errors list, scan completes |
| 8 | Hostile `estimated_value` ("N/A") cannot 500 the brief totals | `test_hostile_estimated_value_cannot_crash_totals` | unit | PASS | totals computed, no raise |
| 9 | Benchmark writes never raise; transient retries, persistent → `db_error` | `test_transient_db_failure_retries`, `test_persistent_db_failure_degrades_without_raising` | unit | PASS | `recorded == 1`; `{"recorded": 0, "reason": "db_error"}` |
| 10 | Nothing else broke | `pytest tests/ -q` (CI dummy env) | CI | PASS | `92 passed, 8 subtests passed` |
| 11 | Style gate | `ruff check app tests` | CI | PASS | `All checks passed!` |
| 12 | Spec artifacts valid | `openspec validate --changes` | manual | PASS | `1 passed, 0 failed` |
| 13 | DLQ previews never carry raw email/phone | `test_preview_redacts_pii_shaped_values`, `test_dlq_logs_error_with_redacted_preview` | unit | PASS | `[redacted]` in preview + ERROR log; raw values absent |
| 14 | Retry delay never exceeds `max_delay` | `test_retry_delay_never_exceeds_max_delay` | unit | PASS | all sleeps ≤ 1.0 with base 10.0 / cap 1.0 |
| 15 | Status codes classify as tokens, not substrings | `test_transient_status_codes_need_word_boundaries` | unit | PASS | "503" transient, "15035" not |
| 16 | Permanent opt-out skips retries | `test_mark_permanent_opts_out_of_retry` | unit | PASS | 1 call, 0 sleeps |
| 17 | Health check reports dead tables honestly | `test_health_check_reports_unreachable_tables` | unit | PASS | `unreachable: RuntimeError: ...` |

## Coverage and known gaps

- The 20 tests use fake DBs (no live Supabase); the migration
  `supabase/migration_csv_import_logs_self_healing.sql` is **not** applied in
  tests — the pre-migration fallback path is tested instead, and the
  migration itself is a plain `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
- Retry sleeps are patched out in tests; real backoff timing is not exercised.
- DLQ previews truncate to 120 chars and redact email/phone-shaped values;
  ERROR-log emission is covered by `test_dlq_logs_error_with_redacted_preview`
  (assertLogs assertion, added in the fix round as documentation of existing
  behavior).
- **Intentional gaps (deferred, not covered):** notification-path retries/DLQ
  and the repo-wide pipeline rollout — follow-up ticket after Rosa's review
  of this slice.

## Plan-safety notes

- **Process exception, recorded honestly:** the skill's left-shifted gate
  says Rosa reviews proposal/design BEFORE code. This urgent dispatch ran
  RED→GREEN in one round on Marcus's "get moving" order. Rosa reviews the
  full proposal/design plus the diff before merge; nothing merges without
  Justynn's exact approval.
- **Shared-checkout incident:** the RED commit `48be168` was cherry-picked
  from Dre's branch (`feature/tw-209-analytics-schema`) where it had landed
  by mistake, and WIP was restored byte-complete. Lesson re-applied: verify
  `git status` (branch name) and the exact uncommitted diff immediately
  before every `git add`/commit. This GREEN commit was staged from a fresh
  `git status` + diff inspection on `feature/tw-208-self-healing`; the
  branch contains only TW-208 files (no TW-209 analytics files).
- No secrets, credentials, or PII in the diff. No new network/subprocess/file
  operations beyond the existing Supabase calls. The `# noqa` directives in
  the diff are intentional, commented broad-except markers (isolation and
  classification require catching broadly) — `ruff` is clean with them.
