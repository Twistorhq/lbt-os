# TW-952 Evidence: Persistent Job Service on the Agent Runtime (TW-948-B4)

Prepared by Twistor Holdings LLC

Author: Zeke Okafor, Lead Build Engineer
Date: 2026-11-05
Ticket: TW-952 (TW-948-B4, from the TW-948 build map Sections 1.3, 2.3, and 4)
Spec source: tori-product-models.md Section 8.1 (scheduled jobs paragraph)

## 1. Source

Ticket TW-952 and the TW-948 build map B4 entry. Base branch:
dre/tw-949-b1 at tip 7df0bfb (B1, gate-complete, NOT merged; merge
is Justynn's separate word). This branch stacks on B1 and must be
rebased onto main when B1 merges. No merge, no deploy, and no
migration application happened in this ticket.

## 2. What landed

- `backend/app/agent_jobs/cron.py`: full five-field cron semantics
  ported as a pattern from Core workforce/schedules.py (lists,
  ranges, steps, Vixie day-of-month/day-of-week OR, 0 and 7 both
  Sunday), matched per job in its IANA timezone, plus next-slot
  computation for the B1 next_fire_at column. No Core code imported.
- `backend/app/agent_jobs/models.py`: JobSpec and JobEdit. A
  definition carries a self-contained prompt, an ordered skill
  list, a per-job IANA timezone, exactly one schedule (cron or
  one-shot, never both), and a catch-up window (default 60
  minutes, bounded at 7 days).
- `backend/app/agent_jobs/service.py`: management operations
  create, list, pause, resume, edit, trigger now, remove
  (archive; history stays as data), and cancel run; the firing
  path shared by ticker and trigger; the one fire writer.
- `backend/app/agent_jobs/ticker.py`: the tick. Scans active
  jobs, fires due slots inside the catch-up window, skips and
  advances older slots, and fails loud (JobTickerError) when the
  scan itself fails. A single job's failure is recorded masked
  on its fire row and counted; it never wedges the tick.
- `backend/app/agent_jobs/execution.py`: the execution context
  (construction property below), the step runner with per-step
  fencing and exactly-once tolling, RunControl, and the
  RuntimeTaskExecutor adapter that enqueues a fire as an
  agent_runtime AgentTask.
- `backend/app/agent_jobs/router.py`: endpoints beside flows
  under /agent-jobs (registered in main.py), org from the
  Identity JWT like flows, tick guarded by the
  agent_jobs_tick_key setting, fail-closed when unset. Fire and
  tick need a deployment-wired runtime factory
  (app.state.agent_runtime_factory); without one they refuse
  503 instead of faking a run.
- `backend/app/config.py`: agent_jobs_enabled_orgs (per-org
  enable flag, comma-separated org ids, default empty, which
  means OFF for every org) and agent_jobs_tick_key.
- `supabase/migration_agent_jobs_tw952.sql`: additive columns
  only (catch_up_window_minutes, one_shot_at on agt_jobs).
  Files only, never applied, gated on Justynn's word like B1.

## 3. Carried requirements from the B1 gates (binding, both closed)

(a) Namespaced fire keys per job. fire_key stays globally
UNIQUE per the toll-ledger precedent, so mint_fire_key prefixes
every key with the job id: f"{job_id}:{slot}". Proven by
test_fire_keys_namespaced_per_job (two jobs, same slot, distinct
keys, each prefixed with its own job id).

(b) Masked fire writer. B1's record_fire wrote only empty
summary/error values. Every later write goes through
service.update_fire, which runs summary and error through the
B1 persistence masking pipeline (mask_text) on write. This
closes the TW-958 fire-writer item at the source. Proven by
test_fire_writer_masks_summary_and_error (an email address in
a run summary and in a failure error is stored as [email]).

## 4. Task report (RED first, per house TDD)

Command: `python3 -m pytest tests/test_agent_jobs_tw952.py -q`

- RED (commit 8c83987, tests only): collection error,
  ModuleNotFoundError: No module named 'app.agent_jobs'.
- First GREEN run: 26 passed, 2 failed. Both failures were
  test-side mistakes, fixed in the tests, not the code: the
  continuity test expected the second fire to surface 1 item
  when the correct dedupe outcome is 0 new (the first fire
  surfaces all 3 candidates), and the cancel retry step
  re-invoked the cancelling handler against an already
  cancelled fire.
- Final: 29 passed in 0.32s (28 behavior tests plus the
  migration shape test).

Neighboring suites, same run: persistence, agent_runtime, and
durable_turns subsets plus the B4 suite: 692 passed. Flows and
flags suites: 154 passed. Full backend suite with
--continue-on-collection-errors: 2750 passed, 21 failed, 9
collection errors. The 21 failures are the pre-existing baseline
set, identical in count to main (bi_whitelabel 8, missed_call
11, pilot_blockers 1, telemetry_service 1); none are in files
this diff touches, and the 9 collection errors are the
environment xmlsec mismatch that also blocks importing app.main
in this sandbox. Baseline failures were not chased, per brief.

Ruff: `python3 -m ruff check` clean on app/agent_jobs/,
tests/test_agent_jobs_tw952.py, app/main.py, and app/config.py;
`ruff format --check` clean on app/agent_jobs/,
tests/test_agent_jobs_tw952.py, and app/main.py. app/config.py
fails format --check on the B1 base too (verified against
dre/tw-949-b1: identical drift in pre-existing alignment
comments); the two settings this ticket adds are format-clean
and the file was left unreformatted to keep the diff scoped.

## 5. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | A job fires at 9:00 in its own timezone regardless of server timezone | test_agent_jobs_tw952.py::TestTicker::test_fires_at_9_in_own_timezone_regardless_of_server | RED | PASS | Section 4 counts |
| 2 | Overlapping ticks fire once | TestTicker::test_overlapping_ticks_fire_once | RED | PASS | Section 4 counts |
| 3 | A missed fire inside the catch-up window fires once on recovery; outside it does not, and the schedule advances | TestTicker::test_missed_fire_inside_catchup_fires_once_on_recovery, test_missed_fire_outside_catchup_does_not_fire | RED | PASS | Section 4 counts |
| 4 | Cancel of an in-flight run leaves completed steps completed and tolled exactly once, and runs nothing further | TestExecution::test_cancel_in_flight_fences_and_tolls_once | RED | PASS | Section 4 counts |
| 5 | A job whose prompt attempts to create a job fails by construction: the execution context has no job-management capability, no db, no service, and build_execution_context takes neither | TestExecution::test_no_job_creates_job_by_construction | RED | PASS | Section 4 counts |
| 6 | A second fire sees the first fire's surfaced ids in continuity and does not resurface them; continuity merges with run_count | TestExecution::test_continuity_dedupe_across_fires | RED | PASS | Section 4 counts |
| 7 | Paused jobs never fire; resume recomputes the next slot | TestTicker::test_paused_jobs_never_fire, test_resume_recomputes_next_fire | RED | PASS | Section 4 counts |
| 8 | Editing a job does not rewrite its history (fire and continuity rows byte-identical after edit) | TestExecution::test_edit_does_not_rewrite_history | RED | PASS | Section 4 counts |
| 9 | Fire keys are namespaced per job (carried requirement a) | TestTicker::test_fire_keys_namespaced_per_job | RED | PASS | Section 3 |
| 10 | Fire summary and error are masked on write (carried requirement b, closes TW-958 at the source) | TestExecution::test_fire_writer_masks_summary_and_error | RED | PASS | Section 3 |
| 11 | Per-org enable flag defaults OFF: an unlisted org never fires and is counted skipped_flag_off | TestTicker::test_flag_off_org_never_fires | RED | PASS | Section 4 counts |
| 12 | One-shot jobs fire once at their instant and never again | TestTicker::test_one_shot_fires_once_at_its_instant | RED | PASS | Section 4 counts |
| 13 | A wedged ticker fails loud instead of silently stopping | TestTicker::test_scan_failure_fails_loud | unit | PASS | Section 4 counts |
| 14 | Full cron field syntax, Vixie OR day semantics, Sunday as 0 and 7, invalid expressions refused | TestCron (5 tests) | unit | PASS | Section 4 counts |
| 15 | A fire becomes an agent_runtime task carrying prompt, ordered skills, and prior surfaced ids | TestRuntimeAdapter::test_adapter_enqueues_agent_runtime_task | unit | PASS | Section 4 counts |
| 16 | The TW-952 migration is additive columns only, attributed, and marked never applied by this ticket | TestMigrationFile | unit | PASS | Section 4 counts |

## 6. B3 dependency note (binding for merge order)

B3 (TW-950, branch zeke/tw-950-b3 tip eb61b32, Rosa-approved,
unmerged) is NOT on this base, so its agent_tolls helpers and
its TOLL_RATES entries (agent.task_completed at 25 cents and
the rest of the Section 11 vocabulary) do not exist here. This
build asserts toll behavior against the toll service seam as it
exists on the B1 base (services/toll_service.record_toll_event's
contract: idempotency key required, duplicate key returns the
existing row, unknown event raises). The tests inject a fake
sink implementing exactly that contract with the post-B3
vocabulary. No B3 code was copied in. Merge order consequence:
B3 should land before or with B4; until B3 lands, a production
fire whose steps name agent toll events would fail loud at the
toll sink (unknown event), never silently untolled. That is the
safe direction and is accepted here.

## 7. Coverage and known gaps

- Covered: definitions and validation, all management
  operations, ticker due/catch-up/pause/flag/one-shot behavior,
  cancel fencing with exactly-once tolls, continuity dedupe,
  masking at the fire writer, construction property, cron
  semantics, runtime adapter, migration shape.
- Not covered by tests: the HTTP router end to end (it needs
  the Identity JWT stack and a deployment-wired runtime; the
  router module imports cleanly and its route table was printed
  and checked, and app.main registration is a two-line include
  that the sandbox cannot import due to the pre-existing xmlsec
  environment failure, verified present on the base as well).
- Out of scope per ticket and not built: preflight, admission,
  and reconciliation (B5, TW-953); event triggers (B11);
  operator console (B15); archetype loop templates (B9).
- Cancel reaches a run executing in this process (RunControl)
  and any runner that consults the durable fire row through the
  fence check fire_job installs. A runner in another process
  that never re-reads the fire row cannot be fenced by B4
  alone; that is the B5/B7 composition surface.
- One-shot jobs that fire stay status active with next_fire_at
  NULL (the B1 status vocabulary has no completed state for
  jobs); they never fire again because the ticker only fires a
  stored slot.
- Remove is an archive, not a hard delete, by design: removal
  must not erase fire history or continuity.

## 8. Plan-safety notes

No untrusted plan content was found in the ticket, the build
map, or the spec sections used. Nothing in the sources asked
for a destructive, credential-handling, or gate-skipping step.
The TW-951 lane (agent_memory and staged writes) was left
untouched; Dre's worktree and the TW-950/TW-951 worktrees were
not modified.
