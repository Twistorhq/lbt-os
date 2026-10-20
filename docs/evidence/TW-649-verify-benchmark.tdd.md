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

## 6. Fix round: Rosa REQUEST CHANGES (review of 47c37d4)

Rosa's verdict: 1 major (M1), 3 minors (m1-m3), 3 nits (n1-n3; n3 is
a real-run wiring item, explicitly out of this round's scope).
Checkpoints on this branch: RED `6822404`, GREEN `c4e964a`, n2
coverage `aa87e32`, docs `ef451af`, format `442ede6`.

**M1: expected-group coverage.** `Dataset` gains `expected_groups`;
`evaluate_gate` and `run_benchmark` accept the set and report every
expected group with no attempts as INSUFFICIENT_DATA by name, in the
empty-run path as well. A run missing an entire expected demographic
group can no longer PASS on the groups present; a measured failure
in a present group still outranks the insufficiency (FAIL wins, the
absent group is still named). FairFace declares the race labels it
itself publishes. RED: 5 tests failed at `6822404` (gate coverage x3,
runner, registry contract). GREEN: all pass at `c4e964a`. One
judgment call, flagged for Rosa: the consented-volunteer entry's
labels are self-described and its collection protocol does not exist
yet (blocked on TW-654 D1-D3), so its `expected_groups` is recorded
from the protocol's strata before the first gate run rather than
invented now; the registry notes say so in writing, and the gate
machinery enforces whatever set the run declares.

**m1: agreement_rate evidence semantics.** A pair counts toward a
field only when at least one side carries it; agreement requires
equal present values (one-side-only is a disagreement); a field with
no evidenced pairs is None, never 1.0 on no evidence. RED: 2 tests
failed at `6822404` (both-missing field rated 1.0; partially missing
field counted both-missing pairs as agreements). GREEN at `c4e964a`.

**m2: README doctrine.** The struck spec 5.5 vendor-fallback sentence
is replaced with the decided doctrine (2026-10-20, Section 12 Q3):
NO VENDOR FALLBACK, no paid check, no bridge; if the gate fails, T2
waits until the FOSS engine passes on its own. A grep over the
harness docs found no other phrasing reviving the fallback.

**m3: criteria/floor interaction documented**, in the `GateCriteria`
docstring and the README gate section: at the 5,000-impostor floor,
one false match is FMR 2e-4, above the 1e-4 ceiling, and 5,000
attempts cannot statistically resolve 1e-4 (zero events bounds the
true rate only to roughly 6e-4 at 95 percent confidence). Criteria
values unchanged; the Rosa/Justynn session owns the choice between
roughly 30,000+ impostor floors and a resolvable ceiling.

**n1: format.** `ruff format` applied to the five files Rosa named
plus runner.py, where the M1 edit itself introduced a one-line
reformat; the whole harness directory and both test files now pass
`ruff format --check`, and `ruff check` is clean (local ruff 0.16.7;
CI pins 0.16.8).

**n2: None/raising path coverage.** Four tests added: `max_gap`
skips an unmeasured (None) group, `max_gap` over a single measured
group is None, `evaluate_gate` on zero attempts is
INSUFFICIENT_DATA, `liveness_rates` raises ValueError on an unknown
presentation kind. These pin behavior Rosa verified by reading, so
they passed on first run; they are coverage, not RED/GREEN.

**Fix-round suite results (fresh).** Harness:
`python3 -m pytest tests/test_verify_benchmark.py tests/test_verify_benchmark_stages.py -q`
**37 passed** (was 25). Full backend:
`python3 -m pytest -q --continue-on-collection-errors` **844 passed,
20 failed, 7 collection errors**: 844 is exactly the prior 832 plus
the 12 new harness tests, and the 20 failures are the identical
pre-existing set (test_bi_whitelabel 8, test_missed_call 11,
test_pilot_blockers 1) from the base-commit control in Section 2.
The 7 collection errors remain the xmlsec/lxml environment defect.
Smoke report: regenerated with `--seed 7` after all changes and
diffed against the committed example: byte-exact (the default run
declares no expected groups, so the report shape is unchanged).
Zero em/en dashes in the fix-round diff (grep over added lines).

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 13 | Absent expected group is INSUFFICIENT_DATA by name, never PASS | tests/test_verify_benchmark.py | RED | PASS (37/37 suite) | RED 6822404 -> GREEN c4e964a |
| 14 | Missing expected group does not mask a measured FAIL | tests/test_verify_benchmark.py | RED | PASS | same |
| 15 | All expected groups present: gate can PASS; runner reports the gap | tests/test_verify_benchmark.py | RED | PASS | same |
| 16 | Gate-bearing registry declarations: FairFace labels declared, LFW none | tests/test_verify_benchmark.py | RED | PASS | same |
| 17 | agreement_rate: both-missing is None; only evidenced pairs count | tests/test_verify_benchmark.py | RED | PASS | same |
| 18 | max_gap None-skip, zero-attempts gate, liveness unknown kind raise | tests/test_verify_benchmark.py | unit | PASS | commit aa87e32 |
| 19 | No new full-suite failures from the fix round | full suite vs Section 2 control | integration | PASS | 844/20/7, identical failure files |
| 20 | Harness branch is ruff format-clean and check-clean | ruff format --check; ruff check | CI | PASS | commit 442ede6 |
