# TW-649 TDD Evidence: Verification Engine Benchmark Harness

## 1. Source

Ticket TW-649 (parent TW-646). Brief: build the benchmark harness and
launch gate for the Twistor Verification Engine, per TW-646 spec
Section 5.3 (gate math, FOSS stack, demographic breakout). Branch
`zeke/tw-649-verifybench`, own worktree. Checkpoint commits: RED
`4782654`, GREEN `0aba7f7`, modules `a45af4a`, requirements/README
`d98878e`, smoke `0f3661a`.

## 2. Task report

**Launch-gate metrics (metrics.py).** Validation command:
`python -m pytest tests/test_verify_benchmark.py tests/test_verify_benchmark_stages.py -q`
RED (commit 4782654): collection error, `app.verify.benchmark` did not
exist; 20 tests failing. GREEN (commit 0aba7f7 onward): **25 passed**
(20 metric/document/registry/runner tests + 5 stage tests). The tests
guarantee exact FMR/FNMR counting, threshold-equality is an accept,
unmeasured rates are None (never zero), per-group breakout, max-gap
over measured groups only, APCER/BPCER per ISO 30107-3, gate verdicts
PASS / FAIL / INSUFFICIENT_DATA with measured failure outranking
insufficient data, and default criteria carrying the spec planning
values (FMR ceiling 1e-4).

**Document math (documents.py).** Same run. Guarantees: ICAO 9303
check digit against hand-computed vectors ("520727" -> 3, "AB2134"
-> 5), field-valid round trip and corruption detection, exact-match
per-field OCR accuracy, positional front-to-barcode agreement rates.

**Dataset registry (registry.py).** Same run. Guarantees: registry
non-empty, ids unique, every entry carries a non-empty license and
consent basis, status in {available, registered, planned}.

**Stage adapters (stages.py).** Tests cover the pure-stdlib pieces:
AAMVA payload parsing, TD3 MRZ parse with all five check digits
validating and a corrupted DOB digit failing, dlib distance to
similarity normalization with clamping, stage probes reporting
without raising, and an unavailable stage raising StageUnavailable
instead of degrading silently.

**Runner + smoke (runner.py).** Determinism test (same seed, same
attempts) and a serializable-report test. Smoke run committed at
`backend/app/verify/benchmark/examples/smoke-report.json`: 240
synthetic attempts (seed 7, 120 genuine / 120 impostor), overall FMR
0.0083, FNMR 0.0, gate FAIL with INSUFFICIENT_DATA also flagged
(sample floors unmet; one synthetic impostor crossed, and a measured
failure outranks insufficient data). Smoke scale: not gate evidence.

**Full suite comparison.** Command:
`python -m pytest -q --continue-on-collection-errors` (system python,
the only environment on this box with the full backend dependency
set). Result: **832 passed, 20 failed, 7 collection errors**. The 7
collection errors are one environment defect (xmlsec/lxml library
version conflict) in files this branch never touches. Control run:
the three failing files (test_bi_whitelabel, test_missed_call,
test_pilot_blockers) executed at the unmodified base commit 3f20c73
produce the **identical 20 failures**; none import or touch
app.verify. Zero new failures attributable to TW-649. The brief's
earlier baseline (673/21/12) was measured in a venv state that no
longer exists on this box (the TTS venv lacks backend deps entirely:
41 collection errors), so exact-count reconciliation is not
possible; the base-commit control is the attribution evidence.
Ruff (CI invocation, ruff==0.16.8): all TW-649 files clean; one
pre-existing I001 in app/main.py, untouched by this branch.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | FMR/FNMR counted exactly; threshold equality accepts | tests/test_verify_benchmark.py | RED | PASS (25/25 suite) | RED 4782654 -> GREEN 0aba7f7 |
| 2 | Unmeasured rate is None, never 0; gap needs two measured groups | tests/test_verify_benchmark.py | RED | PASS | same |
| 3 | Gate: PASS/FAIL/INSUFFICIENT_DATA; failure outranks insufficient | tests/test_verify_benchmark.py | RED | PASS | same |
| 4 | Default criteria carry spec S5.3 planning values (FMR 1e-4) | tests/test_verify_benchmark.py | RED | PASS | same |
| 5 | APCER/BPCER per ISO 30107-3 | tests/test_verify_benchmark.py | RED | PASS | same |
| 6 | MRZ check digit matches ICAO 9303 hand-computed vectors | tests/test_verify_benchmark.py | RED | PASS | same |
| 7 | Registry entries all carry license + consent basis, unique ids | tests/test_verify_benchmark.py | RED | PASS | same |
| 8 | TD3 MRZ parse validates all check digits; corruption fails | tests/test_verify_benchmark_stages.py | unit | PASS | commit a45af4a |
| 9 | Unavailable stage raises StageUnavailable, never degrades | tests/test_verify_benchmark_stages.py | unit | PASS | commit a45af4a |
| 10 | Synthetic attempts deterministic per seed; report serializable | tests/test_verify_benchmark.py | RED | PASS | same |
| 11 | No new full-suite failures from TW-649 code | full suite + base-commit control at 3f20c73 | integration | PASS | 832/20/7; identical 20 at base |
| 12 | Ruff clean on all TW-649 files (CI invocation) | ruff check app tests | CI | PASS | 1 pre-existing I001 in app/main.py only |

## 4. Coverage and known gaps

Coverage tooling is installed in neither environment on this box
(`pytest --cov` unrecognized, `coverage` module absent), so no
coverage percentage is claimed. Named gaps instead:

- Stage adapters are unit-tested only on their pure-stdlib pieces.
  The model-backed paths (PaddleOCR, zxing-cpp, YuNet, MediaPipe,
  dlib, MiniFASNet ONNX) have NOT executed: the libraries and model
  weights are not installed on this sandbox. They run first on the
  benchmark machine after `pip install -r requirements-benchmark.txt`.
- No real dataset has been acquired or run. Blockers, stated plainly:
  (a) the consented-volunteer set, the only set that can clear the
  real gate, cannot be collected until Angela's TW-654 consent
  package is approved and Justynn signs D1-D3; (b) LFW and FairFace
  are registered as planned, downloads not attempted this run.
- The gate's FNMR ceiling, gap ceiling, and sample floors are the
  spec's planning values; final values are set with the measured
  report in front of Rosa and Justynn before T2 opens.

## 5. Plan-safety notes

No embedded instructions, override phrases, or untrusted plan content
were encountered in the brief, the spec excerpts, or the repo files
used. Nothing was rejected; the checklist was run.
