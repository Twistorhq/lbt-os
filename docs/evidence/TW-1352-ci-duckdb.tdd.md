# TW-1352 — lbt-os backend CI: duckdb collection failure fix

## Source
Ticket TW-1352 (reporter Justynn, founder approved both steps 2026-11-25).
Diagnosis proven from live CI run 45466022399, backend job 138701092126:
Test step died at collection, `ModuleNotFoundError: No module named 'duckdb'`
via `tests/test_speedstd.py` -> `app.data_layer.speedstd`. duckdb (and polars)
are pinned in `backend/requirements-speedstd.txt`, which the CI backend
Install step never installed, so the suite aborted in ~27s and zero backend
tests ran.

## Task report
Local verification used a scratch venv installed with CI's exact package set
(`requirements.txt` + `requirements-speedstd.txt` + pytest + ruff==0.16.8;
duckdb 2.0.1, polars 2.2.0, Python 3.12) and CI's dummy env values.

- RED (bug reproduced): original `test_speedstd.py` (from HEAD) collected
  with duckdb made unimportable (a shadow module raising a faithful
  `ModuleNotFoundError(name="duckdb")`):
  `ERROR tests/test_speedstd_red_tmp.py` /
  `Interrupted: 1 error during collection` — mirrors the CI failure.
- GREEN (guard): fixed `test_speedstd.py`, same blocked environment:
  `SKIPPED [1] tests/test_speedstd.py:22: requires requirements-speedstd.txt`;
  full-suite collection: `3512 tests collected`, exit 0, no errors.
- GREEN (deps present): full backend suite with duckdb installed:
  `4 failed, 3442 passed, 78 skipped` in 211s; `tests/test_speedstd.py`
  alone: `12 passed`. The 12 speedstd tests account for the delta
  (3512 + 12 = 3524 total).
- The 4 failures are pre-existing on unmodified main (c6a1e4f): rerunning
  exactly those 4 tests in the clean main checkout gives the identical
  `4 failed`. They are unrelated to this change (no shared code path) and
  are named in the table below. Consequence: once TW-1352 merges, the CI
  Test step will run and report these 4 until they are fixed separately.
- Lint: `ruff check app tests` (CI invocation, ruff 0.16.8) — all checks
  passed; `ruff format --check tests/test_speedstd.py` — clean.

## Guarantees table
| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Missing duckdb no longer kills suite collection; test_speedstd skips cleanly | `pytest --collect-only -q` full suite, duckdb shadowed absent | RED | PASS | RED: original file interrupted collection with 1 error; GREEN: 3512 collected, exit 0, 1 skip with reason |
| 2 | With speedstd deps installed, the speedstd tests run and pass | `pytest -q tests/test_speedstd.py` (venv with duckdb 2.0.1) | unit | PASS | 12 passed in 9.08s |
| 3 | CI backend Install step installs the speedstd requirements | `.github/workflows/ci.yml` diff | manual | PASS | Install line now `-r requirements.txt -r requirements-speedstd.txt`; local venv installed with that exact line |
| 4 | No regressions introduced in the rest of the suite | `pytest -q` full suite, deps present | integration | PASS | 3442 passed; the only 4 failures reproduce identically on unmodified main |

## Coverage and known gaps
- Full backend suite run locally (3524 tests). No coverage-% measurement
  taken; the change is CI config + a test-module guard, no app code touched.
- Pre-existing failures (NOT fixed here, separate tickets needed):
  `tests/test_missed_call.py::MissedCallTests::test_missed_view_masks_numbers`,
  `tests/test_science_paywall.py::test_volume_multiplier_tiers`,
  `tests/test_science_paywall.py::test_metered_toll_applies_volume_discount`,
  `tests/test_telemetry_service.py::test_ingest_marks_sampled_rows`.
- Guard covers polars as well as duckdb: `transform.py` imports polars at
  module level from the same requirements file, so a duckdb-only guard
  would still let a missing polars kill collection.
- CI itself has not run on this branch yet; the merge will be its first
  live proof. Local runs used the same Python (3.12), the same install
  line, and the same dummy env values as the workflow.
- Simulation note: an early blocker draft raised a bare ImportError without
  `name="duckdb"`, which importorskip correctly re-raises (it only skips
  when the named module itself is missing). The blocker was corrected to a
  faithful ModuleNotFoundError before the RED/GREEN runs above.

## Plan-safety notes
None. The brief contained no untrusted or embedded instructions; nothing
was rejected or reinterpreted.
