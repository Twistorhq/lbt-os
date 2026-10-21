# TW-665 review: fx0 durable-turn pattern implant (Dre)

Prepared by Twistor Holdings LLC
Reviewer: Rosa Delgado, Chief Quality Guardian
Branch: dre/tw-665-fx0-durable @ 093f9e9, off main @ 0beb8e2
Verdict: **REQUEST CHANGES**

## Fresh-context reconstruction (before reading Dre's narrative)

From the diff alone: a new self-contained module adds a SQLite-journaled
turn runner (submit-first, per-step journal, lease, effect log, event
stream) plus a crash demo and 12 tests. The dangerous surfaces are the
ones the docstring asserts as guarantees: exactly-once effects, never
re-run a non-idempotent step, single-worker fencing, and the 64-step cap.
Those assertions are where I attacked. My reconstruction matches Dre's
narrative; the divergences are in how far the guarantees actually hold
under adversarial resumption and concurrency, below.

## What I verified by my own execution (all reproduced)

- Durable-turn tests: **12/12 pass**. Combined durable_turns +
  self_healing + flows_executor: **51/51 pass**. Ruff check clean,
  `ruff format --check` clean (4 files), run by me.
- Crash demo (`scripts/tw665_crash_demo.py`, fresh workdir): exit 0,
  VERDICT PASS. Worker SIGKILLed (-9) mid-turn in the worst window;
  journal after the kill shows 3 steps completed + 4 effects recorded;
  a fresh process waits out the dead lease, resumes, completes **6/6**
  with **0 duplicate side effects**. Dre's quoted output is accurate.
- Probes that PASS: persist-before-execute; duplicate submit by
  message_id reaches the same turn and keeps the original prompt;
  interrupted non-idempotent step with the SAME declaration on resume is
  marked interrupted_unknown_outcome and never re-run; effect landed
  before the crash is not duplicated on resume; expired-lease takeover
  after the original worker is done cannot double-apply (completed steps
  plus effect dedup hold); event cursor reads deliver each event once
  (incremental cursor probe: no repeats, no gaps); 64-step default cap.
- Clean-room claim HOLDS: the diff (6 new files, 966 insertions, purely
  additive, no existing file touched) contains no upstream fx0 code and
  no vendor path. The only Vercel strings are attribution comments and
  explicit not-adopted statements. Engine imports are stdlib plus
  `app.self_healing`. No secrets, credentials, or PII in the diff.
- Zero-new-failures discipline: the branch only ADDS files; nothing
  existing is modified, so no standing failure set can move.
- Prototype honesty: the design note (docs/TW-665-...) labels the SQLite
  store a prototype, names the 1:1 Postgres mapping, and lists the owed
  production hardening (Postgres journal, row-level fencing in one
  transaction, lease heartbeat) BEFORE any fleet rollout. With the
  exceptions in M3 and nit n1, nothing overclaims production readiness.

## Majors

**M1. The journaled idempotency declaration is not authoritative on
resume; a flipped declaration silently re-runs a crashed non-idempotent
step.** `engine.py` `_run()`: an interrupted step's fate is decided by
the CALLER-SUPPLIED `step.idempotent` of the resuming process. The
journal stores the original declaration (`durable_steps.idempotent`)
and never reads it back. My probe: step "send" declared
idempotent=False crashes mid-step; resume with the same step declared
idempotent=True and the handler RE-RUNS ("send-RERUN", turn completes
as if nothing happened). Same hole by index: a completed step's stored
result is returned under a completely different supplied step name
with no mismatch raised. This defeats the module's headline guarantee
("an interrupted step that declared itself non-idempotent is NOT
re-run") whenever the resuming code's step list differs from the
original, which is exactly the deploy-while-crashed scenario durability
exists for. Fix: on resume, validate each supplied StepDef against the
journaled row (name and idempotent); the journaled declaration governs
the ambiguous check, and a mismatch raises instead of silently
proceeding. Add regression tests for the flip and the rename.

**M2. Non-idempotent steps are retried in-run.** `_run()` applies
`sh.retry_with_backoff` (default 3 attempts) to every step regardless
of its declaration. My probe: a step declared idempotent=False whose
handler performs a raw side effect and then raises ConnectionError ran
its handler **3 times** inside one run_turn. `apply_effect` protects
only effects routed through it; the declaration's plain meaning is
"not safe to repeat," and the engine repeats it automatically on a
transient classification. Fix: non-idempotent steps get exactly one
attempt (attempts=1), or retry of a non-idempotent step requires an
explicit per-step opt-in distinct from the idempotency flag. Add a
test pinning the behavior either way; today the suite tests retries
only on idempotent steps, so this behavior is unpinned.

**M3. Lease acquisition is not atomic: two live workers can both hold
the turn.** `acquire_lease()` is SELECT-then-UPDATE inside a deferred
SQLite transaction with no compare-and-swap and no rowcount check. My
race probe (two threads, barrier-synchronized acquire, 30 trials):
**both workers acquired the lease in 13 of 30 trials.** Compounding:
`_run()` and `_apply_effect()` never revalidate ownership, and
`_apply_effect`'s check-then-insert has the same window, so two live
workers (or a zombie whose lease expired mid-run while it kept
executing) can both run a step's effect function before either
journals it. The docstring's point 5 asserts single-worker fencing in
the present tense and the suite's fencing test only covers the
sequential case. The design note already owes "row-level fencing in
one transaction" for the Postgres port; that debt does not license the
SQLite prototype claiming the guarantee now. Fix in this branch: make
acquire a single conditional UPDATE (owner IS NULL OR owner = caller
OR lease expired) and raise TurnFenced unless exactly one row changed;
add a concurrent-acquire race test. If the fencing token / ownership
revalidation during a run is deferred to the Postgres work, the
docstring and the pattern doc must say so in as many words.

## Minors

- **m1. `durable_steps.attempts` is never incremented.** It stays 0
  while a handler runs 3 times (my probe: handler ran 3, journal says
  attempts=0). A journal whose audit column misstates reality
  undermines the evidence the whole pattern sells. Increment on each
  execution attempt.
- **m2. Re-running a completed turn is not a no-op.** It flips the
  turn through status running and back to completed and appends a
  second turn_started / turn_completed pair to the event journal (my
  probe: 5 events before, 7 after, handlers correctly not re-run).
  Consumers replaying the stream would see the turn start and complete
  twice. Return the stored result without journaling a phantom run.
- **m3. Duplicate submit journals a duplicate `prompt_saved` event.**
  The turn correctly dedupes, but every retried submit appends another
  prompt_saved (my probe: 2 events for one logical prompt). Journal
  prompt_saved only when the INSERT actually lands.

## Nits

- **n1.** The demo's BEFORE is a constructed in-memory stand-in written
  for the demo, not a run of an actual pre-existing Twistor system.
  The contrast is fair, but the design note's "(volatile in-memory
  turn, today's behavior)" phrasing should read "representative
  in-memory turn" so nobody cites it as a measured legacy baseline.
- **n2.** `StepDef.__post_init__` rejects only `idempotent=None`; any
  truthy non-bool passes as a declaration. Validate `isinstance(bool)`.
- **n3.** Process: 966 lines in a single commit is over the 400-line
  review-unit guideline. It is one self-contained module and I
  reviewed it in full, but stack smaller next time (engine, then demo,
  then docs).

## Bottom line

The core pattern is real and the headline demo is honest: persist-first,
resume, effect dedup, and the ambiguous-outcome rule all work as
claimed in the single-worker, same-declaration world. The three majors
are all the same shape: the guarantees are asserted absolutely but
enforced only on the happy path (same declarations, one attempt class,
sequential lease). Fix M1 to M3 with regression tests, close m1 to m3,
and this comes back for a verification pass. No merge without
Justynn's word regardless.
