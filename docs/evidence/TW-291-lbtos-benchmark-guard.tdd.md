# TW-291 — lbt-os BenchmarkGuardTest fix (TW-178 merge follow-up).tdd.md

Prepared by Twistor Holdings LLC.

## Source

- Ticket: TW-291 ("Merge all" run); follow-up on the lbt-os TW-178 merge.
- Justynn's call (2026-10-03): fix the 3 benchmark tests first, then push.
- Pre-existing failures on unmodified lbt-os main: 3 failures in
  `backend/tests/test_leak_engine.py::BenchmarkGuardTest` + 7 collection
  errors from missing Supabase/Clerk/Stripe env secrets (environmental).

## Task report

**Root cause.** The 3 `BenchmarkGuardTest` tests are pure unit tests of the
pure function `should_record_benchmarks` (TW-204 guard: never record benchmark
metrics when a detector errored). Each test lazily imported
`app.routers.leak_engine`, whose import chain (`..auth` -> `app.config`)
instantiates `Settings()` at import time (`app/config.py:145`), which
requires Supabase/Clerk/Stripe secrets. With no secrets in the environment
the import raised `pydantic.ValidationError` and all 3 tests failed. The
tests did not need secrets at all — only the import chain did.

**Fix (test-only, no production code touched).** In
`backend/tests/test_leak_engine.py`: added `_DUMMY_ENV_VARS` (obviously-dummy
`test-only-*` values) and a `_import_leak_engine_router()` helper that
performs the router import under `mock.patch.dict(os.environ, ...)`; the 3
tests now use the helper. `patch.dict` restores the real environment
afterwards; the assertions on the guard are byte-for-byte unchanged.

RED (before fix):
```
$ python3 -m pytest tests/test_leak_engine.py -q -p no:cacheprovider
FAILED tests/test_leak_engine.py::BenchmarkGuardTest::test_no_recording_when_detectors_errored
FAILED tests/test_leak_engine.py::BenchmarkGuardTest::test_recording_when_clean
FAILED tests/test_leak_engine.py::BenchmarkGuardTest::test_recording_when_no_data_status
3 failed, 30 passed in 1.69s
```
Failure mode: `pydantic.ValidationError` — `Field required` for
`supabase_url`, `supabase_service_key`, `clerk_*`, `stripe_*` at
`app/config.py:145: settings = Settings()`.

GREEN (after fix):
```
$ python3 -m pytest tests/test_leak_engine.py tests/test_browser_automation.py -q -p no:cacheprovider
43 passed in 0.82s
$ python3 -m pytest tests/ -q -p no:cacheprovider [7 secret-dependent files ignored]
70 passed in 3.84s
```

**What this guarantees.** The TW-204 guard keeps its exact assertions
(errored detectors -> no recording; clean/insufficient/no-status -> record).
The tests are now hermetic: they pass with zero ambient secrets, so the guard
is verified in any environment, including secret-less CI sandboxes.

## Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | No benchmark recording when detectors errored | `backend/tests/test_leak_engine.py::BenchmarkGuardTest::test_no_recording_when_detectors_errored` | unit (RED->GREEN) | PASS | RED: `3 failed, 30 passed` pre-fix; GREEN: `43 passed` post-fix |
| 2 | Recording when brief is clean / insufficient / missing | `BenchmarkGuardTest::test_recording_when_clean`, `::test_recording_when_no_data_status` | unit (RED->GREEN) | PASS | same runs as #1 |
| 3 | No regressions in leak-engine or TW-178 browser-automation suites | `pytest tests/test_leak_engine.py tests/test_browser_automation.py` | unit | PASS | `43 passed in 0.82s` |
| 4 | Full collectible backend suite green | `pytest tests/` (7 secret-dependent files ignored, see gaps) | unit | PASS | `70 passed in 3.84s` |
| 5 | Lint clean on changed file | `ruff check backend/tests/test_leak_engine.py` | manual | PASS | `All checks passed!` |
| 6 | No secrets committed | pre-review self-scan of diff | manual | PASS | dummy `test-only-*` values only; no real credentials |

## Coverage and known gaps

- **7 collection errors intentionally left red** (environmental, per brief —
  do not invent credentials): `test_analytics_events.py`,
  `test_demo_audit_limit.py`, `test_integrations_platform.py`,
  `test_pilot_blockers.py`, `test_security_fixes.py`,
  `test_stripe_checkout.py`, `test_visitor_events.py`. All fail at import
  with `pydantic.ValidationError` (missing Supabase/Clerk/Stripe secrets).
  These are integration-style modules that genuinely need a wired app; they
  were red on unmodified base main too and are unrelated to TW-178.
- `ruff format --check` on `test_leak_engine.py` reports the file would be
  reformatted — **pre-existing drift** (verified via `git stash`: the
  committed file was already format-dirty; the flagged hunks are fixtures I
  never touched). Not reformatted to avoid a ~700-line unrelated diff.
- Production code untouched: `app/routers/leak_engine.py`,
  `app/auth.py`, `app/config.py` not in the diff. Rosa re-review not needed
  per the brief (pure test fix).

## Plan-safety notes

- No untrusted plan content encountered. The parent brief explicitly
  forbade inventing credentials for the 7 collection errors — honored;
  dummy values used are unmistakably `test-only-*` and never leave the test
  process (restored by `mock.patch.dict`).
