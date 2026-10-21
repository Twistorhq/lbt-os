# TW-665 evidence: fx0 durable-turn pattern implant and use

Prepared by Twistor Holdings LLC

Branch: `dre/tw-665-fx0-durable` (worktree `~/workspace/worktrees/dre-tw665-fx0`),
off lbt-os main @ 0beb8e2. Unmerged: Rosa review + Justynn's word gate any merge.

## TDD

- RED: `backend/tests/test_durable_turns.py` written first; collection failed
  with `ImportError: cannot import name 'DurableTurnEngine'` (module absent).
- GREEN: `backend/app/durable_turns/` (`__init__.py`, `engine.py`) built on
  the TW-208 self-healing retry primitives plus a SQLite journal.
- Tests: 12/12 pass (`tests/test_durable_turns.py`): persist-before-execute
  and message dedupe, happy path, crash-mid-turn resume with a brand-new
  engine instance, interrupted non-idempotent step never re-run, effect
  landed before crash not duplicated on resume, mandatory idempotency
  declaration, step cap (and the default cap is 64), transient retry,
  permanent failure without retry, lease fencing, event cursor reads each
  event once.

## Live crash-recovery demonstration (real processes, real SIGKILL)

Command: `python3 scripts/tw665_crash_demo.py /tmp/tw665-demo`

```
=== BEFORE: volatile in-memory turn (current behavior) ===
worker exit: -9 (SIGKILL = -9)
fresh process recovers: 0 steps, turn lost
side effects that landed before the kill: 4 (orphaned, no turn to attach them to)

=== AFTER: durable turn (TW-665 engine, pattern from fx0) ===
worker exit: -9 (SIGKILL = -9)
journal after kill: turn status=running, steps completed=3, effects recorded=4
waiting out the dead worker's lease (1.0s), then resuming in a fresh process...
resume result: status=completed, interrupted=True, results=6 steps
side-effect ledger: 6 lines for 6 steps, duplicates: none
effects in journal: 6

VERDICT: PASS
BEFORE: turn lost, 0/6 steps recovered.
AFTER: turn recovered, 6/6 step results intact, 0 duplicate side effects.
```

The kill lands in the worst window: the 4th step's side effect (notify
customer) has been applied, but its step-completion journal write has not.
On resume the step re-runs (it declared `idempotent=True`), `apply_effect`
returns the stored effect instead of applying a second one, and the turn
completes.

## Design note

`docs/TW-665-fx0-durable-turn-pattern.md`: adopted pattern table, the vendor
paths deliberately not adopted (Vercel Workflow World, Vercel Queues, AI
Gateway), and next-use lanes (Flows executions, Notify sends, ingestors).

## Fix round: Rosa REQUEST CHANGES (2026-10-21)

Rosa's review (`docs/reviews/TW-665-review.md`, REQUEST CHANGES, 3 majors /
3 minors / 3 nits) verified the core by her own execution, then proved by
probe that the guarantees were enforced only on the happy path. Fixes, each
pinned by a new regression test in `backend/tests/test_durable_turns.py`:

- **M1, journaled declaration governs on resume.** `_run()` validates the
  supplied step list against the journaled rows before any state change
  (`_validate_against_journal`): a renamed step or a flipped idempotency
  declaration raises the new `JournalMismatch`, and the run loop reads the
  journaled declaration for the ambiguous-outcome decision. Tests:
  `test_flipped_declaration_on_resume_raises_and_never_reruns` (Rosa's
  flip probe: no "send-RERUN"), `test_renamed_step_on_resume_raises`.
- **M2, non-idempotent steps are never retried in-run.** The retry wrapper
  now gets `attempts=1` for a step whose (journaled) declaration is
  non-idempotent. Test: `test_non_idempotent_step_is_not_retried_in_run`
  (transient ConnectionError after the side effect: handler runs once).
- **M3, lease acquire is an atomic compare-and-swap.** `acquire_lease()`
  is one conditional UPDATE (owner IS NULL OR owner = caller OR lease
  expired) and raises `TurnFenced` unless exactly one row changed.
  Ownership is revalidated before each step execution and before the
  completion write (`_assert_lease`), and `_apply_effect` now runs its
  check + effect + insert inside one `BEGIN IMMEDIATE` transaction with
  ownership revalidated inside it. Tests:
  `test_concurrent_lease_acquire_has_exactly_one_winner` (barrier race,
  30 trials, exactly one winner each; Rosa's pre-fix probe had both
  workers winning 13 of 30), `test_worker_whose_lease_was_taken_over_
  stops_before_next_step`.
- **m1, attempts counter is real.** Each handler execution increments
  `durable_steps.attempts`. Test: `test_attempts_counter_counts_every_
  handler_run` (flaky handler: 3 runs, journal says 3).
- **m2, completed turns are final.** Re-running a completed turn returns
  the stored results without status flips or phantom events. Test:
  `test_rerun_of_completed_turn_is_a_noop` (event count unchanged).
- **m3, one prompt_saved per prompt.** `submit()` journals the event only
  when the INSERT lands (rowcount). Test:
  `test_duplicate_submit_journals_one_prompt_saved`.
- **n1, honest BEFORE label.** The demo's BEFORE is a constructed stand-in
  and now says "representative" in the demo docstring, its printed
  header, and the pattern doc; it is not a measured legacy baseline.
- **n2, strict declaration.** `StepDef` rejects any non-bool
  `idempotent` (was: only None). Test:
  `test_step_declaration_must_be_a_real_bool`.

Verification after the fix round: durable suite 21/21 (12 original + 9
new); combined durable_turns + self_healing + flows_executor 60/60; crash
demo re-run in a fresh workdir, exit 0, VERDICT PASS (SIGKILL mid-turn,
resume 6/6, 0 duplicate side effects); ruff check clean and
`ruff format --check` clean on all four touched Python files. The diff
stays additive in shape: only files created by this branch are modified
(engine, package exports, tests, demo script, docs); no existing file
outside the TW-665 module is touched.
