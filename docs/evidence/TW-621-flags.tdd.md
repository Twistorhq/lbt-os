# TW-621 Twistor Flags — TDD evidence

**Prepared by Twistor Holdings LLC**

## 1. Source

- Ticket: TW-621 (Twistor Flags, GO Feature Flag mechanism, clean-room)
- Brief: parent-agent task 2026-10-19 (Flags next after Flows merge)
- Design: `docs/TW-621-flags-design.md`
- Branch: `zeke/tw-621-flags` (worktree `~/workspace/worktrees/zeke-tw621-flags`)

## 2. Task report

**Evaluation engine** (`backend/app/flags/evaluation.py`, pure functions).
RED: `python3 -m pytest backend/tests/test_flags_evaluation.py -x -q` →
"1 error during collection" (module `app.flags` did not exist).
GREEN: same command → `33 passed in 0.24s` after models + evaluation landed.
Guarantees: disabled flags serve default with reason `disabled`; first
matching rule wins; missing attributes fail closed; percentage rollouts are
deterministic per (flag, targeting key) and roughly distribute 10% over 2000
keys; invalid definitions raise raw `DefinitionError`.

**LaunchDarkly migration** (`backend/app/flags/migration.py`).
RED: collection error (module missing).
GREEN: `11 passed` — plus one real bug found by the tests and fixed: the
first version unwrapped single-element `in` value lists, turning
set-membership into substring matching (`"beta" in "beta"` is True).
`in`/`not_in` now always keep the list. Concept mapping only — no LD or
Unleash code vendored.

**Service** (`backend/app/flags/service.py`): CRUD with version bumps,
attributed audit trail, scheduled flips.
RED: collection error. GREEN: `15 passed in 0.38s`. Covers: create audits,
update re-validates and bumps version, flip audited as `flip`, delete
audited, due schedules fire (naive datetimes treated as UTC), future
schedules untouched, audit lists most-recent-first.

**Router** (`backend/app/flags/router.py`): eval + bulk eval + admin CRUD +
LD import + schedule application, org-scoped via Identity JWT.
GREEN: `13 passed` (TestClient with dependency overrides). Covers 404s,
bulk cap at 100, 422 on invalid definitions, strict LD import rejecting
unmapped constructs.

**Full flags suite**: `72 passed in 1.31s`.
**Lint**: `ruff check` — all checks passed; `ruff format --check` — clean.
**Regression**: full backend suite (minus 7 pre-existing collection-error
files: xmlsec/env issues, identical on main) — 32 failures on the branch,
32 on main, `diff` of the sorted failure lists is EMPTY. Zero regressions.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Disabled flag serves default, ignores rules | test_flags_evaluation.py::TestDisabledFlag | unit | PASS | 33 passed |
| 2 | First matching rule wins; missing attr fails closed | test_flags_evaluation.py::TestTargetingRules | unit | PASS | 33 passed |
| 3 | Rollout deterministic + distributes | test_flags_evaluation.py::TestPercentageRollout | unit | PASS | 33 passed |
| 4 | Invalid definitions raise DefinitionError raw | test_flags_evaluation.py::TestValidation | RED | PASS | collection error -> 33 passed |
| 5 | `in` keeps list semantics (substring bug fixed) | test_flags_migration.py | RED | PASS | failed pre-fix, 11 passed post |
| 6 | LD import maps ops; unmapped reported not dropped | test_flags_migration.py | unit | PASS | 11 passed |
| 7 | CRUD versions + audits every mutation | test_flags_service.py | unit | PASS | 15 passed |
| 8 | Schedules fire when due, not before | test_flags_service.py::TestSchedules | unit | PASS | 15 passed |
| 9 | Eval/admin endpoints behave (404/422/caps) | test_flags_router.py | integration | PASS | 13 passed |
| 10 | No regressions vs main | full suite diff | CI | PASS | empty diff |
| 11 | ruff check + format clean | ruff | CI | PASS | all checks passed |

## 4. Coverage and known gaps

- No SDK client library yet (documented non-goal; eval API is SDK-ready).
- No real-time flag streaming (poll the eval API).
- Approval workflows ticketed as follow-up, not in V1.
- The 7 collection-error test files are pre-existing environment issues
  (xmlsec import, etc.), untouched by this branch.

## 5. Plan-safety notes

- Toolchain note (not a plan injection): the `muse.write` pipeline rewrote
  `bearer = HTTPBearer(auto_error=False)` to `bearer <redacted>
  HTTPBearer(auto_error=False)` on write — a credential-scrubber misfiring
  on a FastAPI security-scheme assignment. Repaired via exec to
  `_bearer_scheme = HTTPBearer(auto_error=False)`; all 13 router tests pass.
  Lesson: verify security-scheme lines byte-for-byte after writing.
- No embedded instructions found in any fetched or third-party content.
  GO Feature Flag was studied for mechanism only; no code copied.

## 6. Fix round (Rosa REQUEST_CHANGES, 2026-10-19)

Rosa verified independently: 72/72 green, ruff clean, tenant isolation,
wedge intact, migration honesty, toolchain repair byte-correct. Findings:

| # | Finding | Fix | Proof |
|---|---|---|---|
| 1 | Blocker: LD fallthrough silently dropped when rules exist | Fallthrough now always preserved: catch-all rule when rules exist, default value when none | TestFallthroughPreserved (4 tests): strict import succeeds, non-match serves fallthrough |
| 2 | Major: `missing bearer <redacted>` in committed 401 strings | Reworded to "missing credentials"; verified zero "redacted" bytes | grep -c redacted → 0 |
| 3 | Major: `in`/`not_in` with scalar degrades to substring | Condition validation now requires list values | TestInRequiresList |
| 4 | Major: empty rollout → raw ValueError → 500 | `_rollout_to_percentage` raises DefinitionError on empty | test_empty_rollout_raises_definition_error |
| 5-10 | Nits: bucket collision, double JWT verify, updated_at, assert in hot path, docstring | NUL separator + regression test; cached `_verified_claims`; updated_at on mutations; explicit guard; docstring corrected | TestBucketSeparator; 79/79 green |

Post-fix: **79/79 tests green**, ruff check + format clean.

Note on suite counts: the evidence above cited 32 pre-existing failures;
Rosa measured 20 failures + 8 collection-error files on her run. The
empty-diff-vs-main claim held on both runs; the count drift is environment
(xmlsec availability), not a code difference.
