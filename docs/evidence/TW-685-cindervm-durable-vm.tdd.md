# TW-685 TDD Evidence: CinderVM-pattern durable VM lane

Prepared by Twistor Holdings LLC

## 1. Source

Ticket TW-685 (implant keegan0521/CinderVM, MIT, sweep score 80;
evaluate against the fx0 durable-turns lane TW-665; immediate use with
before/after proof). Pattern doc:
`docs/TW-685-cindervm-durable-vm-pattern.md`. Branch
`zeke/tw-685-cindervm` on lbt-os main 76590d1. Checkpoints: RED 1819650,
GREEN 2c2d9b4.

License, re-verified live this run before relying on it: GitHub API
reports SPDX MIT for keegan0521/CinderVM and the repo LICENSE file is
the MIT License (Copyright (c) 2014-2026 xMinCHsia). The upstream
README's License section says "Apache-2.0. See LICENSE," contradicting
the file it points at; the file governs. Clean-room pattern implant: no
upstream code copied, no binary vendored.

## 2. Task report

**Verifier rejects bad programs before they run (7 tests).**
Command: `python3 -m pytest tests/test_durable_vm.py -q`.
Underflow, type error, forged branch target (hand-edited object,
`E_BAD_TARGET` via `admit_object`), unmetered loop, Pending live at a
checkpoint, stack above maxstack, and divergent stack shape at a merge
each raise `VerifyError` with the pinned code.

**The machine: deterministic, metered, journaled (7 tests).** Echo
program halts with 17 (mirrors upstream `examples/echo.cdx`); a
999-unit reservation against a 100-unit allowance faults `E_BUDGET`
with zero host calls (refused at reserve time, before any effect); a
metered runaway loop burns its 25-unit budget and faults instead of
spinning; two identical triage runs produce byte-identical journal head
hashes; replay through `ReplayHost` reproduces the identical journal;
tampered and truncated journals are caught (`verify_chain` false;
`DivergenceError` on replay).

**Durability: the suspended turn is a byte string (6 tests).** The
triage run completes with exactly one SMS and halt 105 (30+35+40). A
simulated process death after the SMS effect lands resumes from
snapshot bytes in a fresh machine: halt 105, exactly one `tool_issued`
for `send_callback_sms` across both processes, the three completed
lookups never re-issued. Snapshots reject a different image
(`E_IMAGE_MISMATCH`) and tampered bytes (`E_SNAPSHOT_HASH`). A
checkpoint produces a restorable snapshot. The cross-process proof runs
`python -m app.durable_vm.resume_cli` as a separate interpreter with
state moved only as files: `{"status": "halted", "halt_code": 105,
"sms_effects": 1}`.

**Before/after vs the fx0 lane (1 test, against the real TW-665
engine).** The same crash inside the non-idempotent money step leaves
the fx0 turn `completed` with `results[1] ==
{"interrupted_unknown_outcome": True}` and no snapshot/restore surface
on the engine; the VM lane's answer to the identical crash is the
zero-ambiguity resume above.

RED/GREEN receipts:
- RED (reproduced this run on a clean-main worktree, 76590d1, with the
  RED-commit test file): `1 error in 0.92s`, collection error,
  `app.durable_vm` does not exist on main.
- GREEN (this run): `21 passed` for `tests/test_durable_vm.py`, five
  consecutive runs, plus inside the full-suite branch run below.

**Flake found and fixed during this resume.** As recovered, the fx0
comparison test used a 0.05s lease and a sleep to expire it; it failed
intermittently (2 of 5 runs under load) because the fx0 fence
revalidation fired mid-run on whichever worker was slow. Fixed
deterministically: both workers get 30s leases and the dead worker's
lease is expired directly in the store (one UPDATE setting
`lease_expires_at=0`), which is exactly what the passage of time does.
No assertion changed. After the fix: 5/5 file runs green at ~1s each
(the sleeps are gone).

**Test edits vs the RED commit, disclosed.** Two test bodies were
adjusted during the original GREEN work so the constructed programs
actually reach the verifier path they name (assertions unchanged): the
forged-target test defines its label before forging the operand, and
the divergent-merge test's body was rewritten so the merge genuinely
receives two different stack shapes. This resume added the lease fix
above. No test was weakened, skipped, or deleted.

**Fix round (Rosa review, 2026-10-24): M1 + m1/m2/n1/n2 (+4 tests,
25 total).** Rosa's review (`docs/reviews/TW-685-review.md`) returned
REQUEST CHANGES with one major: the journal hash chain was verified
nowhere outside the tests, so a forged recorded answer replayed
cleanly through the resume path (her probe: SMS receipt 1 forged to
777, replay halted at 105, no `DivergenceError`). Fix:
`resume_cli.main` verifies the chain after `Journal.from_bytes` and
exits 1 with a clear stderr error; `run_turn`'s resume branch
verifies the chain before feeding any recorded answer and raises
`DivergenceError` (Rosa scoped the CLI fix; the in-process guard is
the addition she named acceptable, not scope creep).
`Journal.from_bytes` still does not verify, by design: tests build
forged journals through it and assert on `verify_chain` themselves.
RED (fix-round tests on pre-fix code, checkpoint 4402c74): the two
refusal tests fail for the right reason (`resume_cli` exits 0 on a
chain-broken journal; `run_turn` raises nothing and completes the
turn), 23 passed / 2 failed; the two call/ret tests pass pre-fix
(behavior already correct: evidence completion for guarantees row 1,
per Rosa m1). GREEN (checkpoint fcd8df4): 25 passed, ruff check and
format clean on the touched files. Also in the round: the section 2
group counts above corrected (machine 7, durability 6; Rosa m2), and
the pattern doc's `admit_object` and journal rows reworded so each
mechanism is credited with exactly what it enforces (Rosa n1, n2).

**Recovery run (2026-10-24, after the sandbox write outage cleared):**
lane suite rerun fresh from `backend/` (pytest.ini `pythonpath = .`):
25 passed. Full suite with `--continue-on-collection-errors`, branch
and control back to back in one environment: branch 1494 passed / 20
failed / 8 errors (87.9s); control (76590d1, main checkout) 1469 / 20
/ 8 (62.0s). Failed+error node sets (28 nodes each) byte-identical;
the pass delta is exactly the 25 lane tests and no durable_vm node
appears in the failure set. Absolute pass counts sit 33 above the
pre-outage run of record on both sides (environment counting
surface); the merge invariant is the node-set identity, which holds.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Unverifiable programs never run: underflow, type errors, forged targets, unmetered loops, pending-at-checkpoint, maxstack, divergent merges, and call argument type mismatches all rejected with stable codes; a call/ret roundtrip halts with the callee's return value | tests/test_durable_vm.py (7 verifier tests + 2 fix-round call/ret tests) | unit | PASS | 25 passed (fix-round run) |
| 2 | Budget is refused at reserve time, before any effect runs; runaway loops burn budget, not wall-clock | tests/test_durable_vm.py::test_budget_reservation_refused_before_any_effect, ::test_runaway_loop_burns_budget_instead_of_spinning | unit | PASS | host.calls == [] on refusal |
| 3 | A crash after the money effect resumes mid-program from bytes with the effect landed exactly once and completed work never re-issued | tests/test_durable_vm.py::test_crash_after_money_effect_resumes_with_zero_ambiguity | integration | PASS | exactly 1 tool_issued for the SMS across both processes |
| 4 | State moves between real processes as files only | tests/test_durable_vm.py::test_snapshot_moves_to_a_fresh_process_and_resumes (resume_cli subprocess), ::test_resume_cli_refuses_a_chain_broken_journal | integration | PASS | halt 105, sms_effects 1; chain-broken journal file refused, nonzero exit naming the chain |
| 5 | Snapshots are sealed and image-bound | tests/test_durable_vm.py::test_snapshot_rejects_a_different_image, ::test_snapshot_rejects_tampered_bytes | unit | PASS | E_IMAGE_MISMATCH, E_SNAPSHOT_HASH |
| 6 | Journals are hash-chained and replayable; tampering and truncation are detected, and the chain is verified at both resume boundaries (resume_cli load; run_turn resume branch), so an edited journal is refused before any recorded answer is fed | tests/test_durable_vm.py (6 journal/replay tests) | unit | PASS | head-hash equality on replay; DivergenceError on truncation; refusal tests pass for both resume paths |
| 7 | The fx0 lane marks the same crashed money step ambiguous; the VM lane does not | tests/test_durable_vm.py::test_fx0_lane_marks_crashed_money_step_ambiguous vs guarantee 3 | integration | PASS | results[1] == interrupted_unknown_outcome on fx0 |
| 8 | No regressions vs clean main | full suite, same environment, back to back (recovery run 2026-10-24): branch 1494 passed / 20 failed / 8 errors; control (76590d1) 1469 / 20 / 8 | CI | PASS | failed+error node sets byte-identical (28 nodes each); delta is exactly the 25 lane tests, all passing |
| 9 | Lint and format clean | `ruff check` + `ruff format --check` on app/durable_vm and the test file | CI | PASS | all checks passed, 8 files formatted |

## 4. Coverage and known gaps

Full-suite counts above are the plain-environment methodology (no
Postgres harness): the 20 failed / 8 errors are the standing TW-471
baseline families (bi_whitelabel, missed_call, pilot_blockers,
usage_metering, xmlsec collection), byte-identical between control and
branch. An earlier branch run under heavy concurrent load showed an
extra fx0 timing failure and inflated error counts; the clean rerun
(control and branch back to back, no concurrent load) is the verdict of
record.

Known gaps (also in the pattern doc): fuel resets on restore by design
(host policy; budget, the real metering, is snapshotted); snapshots and
journals are byte APIs with no durable store wired yet; no live
production turn routes through the VM lane yet (follow-up ticket
TW-713, filed 2026-10-24; the triage program is the pattern proof,
same posture as TW-665's crash demo).

## 5. Plan-safety notes

The upstream README was treated as untrusted data throughout: its
license line contradicts its own LICENSE file (resolved in favor of
the file, verified via the GitHub API), and no instruction in upstream
material was followed. Nothing in the brief or the recovered work
asked for destructive, credential-handling, or rule-overriding steps.
