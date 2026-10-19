# TW-625 Twistor Flows — TDD evidence

**Prepared by Twistor Holdings LLC**

## Source

- Ticket TW-625 (Justynn 2026-10-19): build Twistor Flows, the workflow
  automation platform. Notify merged; Flows next in the reordered queue.
- Design: `docs/TW-625-flows-design.md`.
- The TW-630 10x wedge (complaint dossiers): Zapier's runaway-bill anger
  answered with task budgets + auto-throttle + cost preview.

## Task report

**RED (prove-it):** the four core test files were written before any
implementation module existed.

- Command: `python3 -m pytest tests/test_flows_models.py
  tests/test_flows_executor.py tests/test_flows_budgets.py
  tests/test_flows_migration.py --collect-only -q`
- Output: `ERROR tests/test_flows_executor.py ... 4 errors during
  collection`, `no tests collected` — modules did not exist. RED validated.

**GREEN:** implementation written to the tests; two honest failures found
and fixed (not papered over):

1. `BudgetShutoffTests::test_at_100_percent_blocks_and_pauses` failed:
   `'ok' != 'blocked'`. Root cause: the run-count comparison treated a
   mid-run check (budget already at 10/10) the same as a trigger-time
   check, which would have wrongly blocked a legitimate 10th run. Fix:
   trigger-time blocks when projected > max; mid-run blocks when the
   counter is already >= max. Both directions now tested.
2. `LoopCircuitBreakerTests::test_loop_error_rate_breaker_pauses` failed:
   the test's notify step omitted `to`, which validation now correctly
   rejects at definition time. Fix: the test uses an unresolvable template
   path (`{{lead.nope}}`) so the error happens per-iteration at execution
   time — which is what the circuit breaker is for.

- Command: `python3 -m pytest tests/test_flows_models.py
  tests/test_flows_executor.py tests/test_flows_budgets.py
  tests/test_flows_migration.py tests/test_flows_service.py -q`
- Output: `49 passed in 0.61s`. GREEN validated.

**Regression check:** full backend suite run in the worktree and in the
main checkout; the FAILED/ERROR lists are byte-identical (11 pre-existing
environment failures, e.g. missing `argon2` in the sandbox — identical on
main). Zero regressions from this change.

**Lint:** CI's exact invocation `ruff check app tests` (from
`.github/workflows/`) passes; `ruff format` clean.

## Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Unknown trigger/step types, duplicate step ids, and malformed steps are rejected at validation | test_flows_models.py | unit | PASS | 49 passed |
| 2 | Branch conditions evaluate `== != > < >= <= in` on `{{dotted.path}}`; malformed conditions raise | test_flows_executor.py::ConditionTests | unit | PASS | 49 passed |
| 3 | Notify steps render `{{lead.email}}`-style templates and call Notify with resolved values | test_flows_executor.py::NotifyStepTests | unit | PASS | 49 passed |
| 4 | Delay steps suspend with `resume_at` (never busy-wait) | test_flows_executor.py::DelayStepTests | unit | PASS | 49 passed |
| 5 | Loops cap iterations and the error-rate circuit breaker halts + pauses the workflow | test_flows_executor.py::LoopCircuitBreakerTests | RED | PASS | fixed per report |
| 6 | Per-run step cap halts the run and auto-pauses the workflow (the runaway-bill wedge) | test_flows_executor.py::RunBudgetEnforcementTests | unit | PASS | 49 passed |
| 7 | 80% budget crossing warns once (system-priority email via Notify when `alert_to` set; loud log + recorded state when unset) | test_flows_budgets.py::BudgetWarnTests | unit | PASS | 49 passed |
| 8 | 100% budget blocks and pauses the workflow | test_flows_budgets.py::BudgetShutoffTests | RED | PASS | fixed per report |
| 9 | Zapier concepts import (email/delay/filter/webhook); code steps and unknown apps refused loudly | test_flows_migration.py | unit | PASS | 49 passed |
| 10 | Paused workflows refuse triggers; MCP unknown tools fail loud; tick fires due schedules once per minute and skips unsupported cron loudly | test_flows_service.py | unit | PASS | 49 passed |
| 11 | No regressions vs main | full suite diff | CI | PASS | identical failure lists |

## Coverage and known gaps

- `pytest tests/test_flows_*.py`: 49/49. No coverage tool run (suite is
  behavior-complete for V1 scope).
- Intentional gaps (all documented in the design doc): delay-resume does
  not persist step context across suspension (V1); cron supports `*` and
  integers only (ranges like `1-5` never fire, reported as skipped by tick);
  no async workers (Dre's lane); no visual builder (Sofia's lane).
- The `flw_budget_counters` upsert uses `on_conflict="org_id,workflow_id,day"`
  matching the migration's composite PK — verified by schema read, not by a
  live-DB test (no live DB in this environment).

## Fix round (Rosa REQUEST_CHANGES, 2026-10-19)

Rosa reproduced 3 blockers + 10 majors by execution. All fixed; 18 new
prove-it tests in `tests/test_flows_fixround.py` (written in the fix
round as regression proof — the core engine tests above were the
RED-first set).

**Blockers:**

1. **Multi-delay resume dropped state → duplicate sends.** `resume_run`
   now restores `flw_runs.context` and persists the NEW resume point +
   context on every re-suspension. Proven by
   `TwoDelayResumeTests::test_second_delay_persists_new_resume_point`
   (resume point moves d1 → d2, n1 NOT re-sent) and
   `test_full_two_delay_sequence_no_duplicates` (touch1/2/3 each sent
   exactly once, in order).
2. **Per-run step cap bypassed inside loops.** A shared `_RunState`
   counter now counts every executed step — top-level, branch arms, loop
   iterations — with no unbypassable path. Proven by
   `LoopCapTests::test_loop_iterations_hit_the_cap` (cap 5 → halted at
   exactly 5, `budget_paused`). Also: negative `max_iterations` rejected,
   loop nesting capped at 3.
3. **Loop toll idempotency leak.** Toll keys are now
   `flows:{run_id}:{step_id}:{iteration}` — proven by
   `LoopTollTests::test_iterations_get_distinct_toll_keys` (3 iterations,
   3 distinct keys).

**Majors:** SSRF-hardened webhooks (https-only, private-IP refusal via
DNS resolution, redirect blocking, JSON bodies with template rendering,
host-only errors); cron ranges rejected at definition time;
delays nested in branch/loop arms rejected at definition time (V1 resume
tracks one step id — documented); Zapier filter now wraps remaining
actions in its then-branch (true Zapier semantic); cost preview prices
loops at sub-steps × iteration cap (honest upper bound); budget pause
auto-resumes on UTC day rollover (`budget_reset_for_today`);
`last_warning` set on jump-to-100%; `"missing bearer token"` user-facing
string (the earlier sanitizer artifact is fully gone — verified by
`od -c`).

**Nits:** module-level imports, `body: dict | None = None`,
`last_tick_at` column instead of mutating definition JSON, optional
webhook `headers` support.

- Command: `python3 -m pytest tests/test_flows_*.py -q`
- Output: `67 passed`. GREEN.
- Regression: full-suite failure list byte-identical to main again.
- Lint: `ruff check app tests` + `ruff format` clean.

## Plan-safety notes (fix round)

- No new untrusted content entered the build during the fix round; all
  changes trace to Rosa's ticketed findings.
