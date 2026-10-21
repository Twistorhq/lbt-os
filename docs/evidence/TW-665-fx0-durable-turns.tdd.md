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
