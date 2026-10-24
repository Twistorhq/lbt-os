# TW-685: Deterministic Bytecode VM Pattern (CinderVM implant)

Prepared by Twistor Holdings LLC

## Source and license

Pattern-mined from keegan0521/CinderVM (Rust), verified live in the
2026-10-24 GitHub sweep (score 80) and re-verified 2026-10-24 at implant
time: the GitHub API reports SPDX MIT and the repo's LICENSE file is the
MIT License (Copyright (c) 2014-2026 xMinCHsia). The upstream README's
License section says "Apache-2.0. See LICENSE," which contradicts the
LICENSE file it points at; the file governs, and the sweep's MIT reading
stands. This is a clean-room pattern implant: the design ideas below are
adopted, no upstream code is copied, no Rust binary is vendored, and the
implementation is ours (`backend/app/durable_vm/`).

## What CinderVM does (the pattern)

A deterministic bytecode VM for durable agents. Programs are stack
bytecode (ISA versioned separately, `cdx/4`) admitted only through a
static verifier (`verify::admit` is the only way to construct an image):
abstract interpretation proves stack depth and operand types, validates
branch targets, and requires every control-flow cycle to carry a
metering instruction. The interpreter performs no I/O; when it needs an
effect it traps to the host. Every value is tagged, copyable, and
pointer-free (no floats, no indirect branches, no host pointers), so
the whole machine state serializes to bytes and a suspended computation
resumes anywhere. Budget is two-phase (RESERVE before SPEND, refused at
reserve time). The journal is hash-chained, so replay reproduces a run
exactly and a truncated or edited journal is detectable.

## What was adopted

| CinderVM idea | Twistor implant |
|---|---|
| Image exists only through the verifier | `verify.admit_object` is the only `Image` constructor path; `Image.from_bytes` re-verifies and re-checks the seal |
| Abstract-interpretation verifier with stable diagnostic codes | `verify.py`: fixpoint over the CFG, single-valued stack shape at every merge (`E_STACK_SHAPE`), `E_UNDERFLOW`, `E_TYPE`, `E_BAD_TARGET`, `E_MAXSTACK`, call/return arity and type agreement |
| No unanswered effect live at a durability boundary | `E_PENDING_ESCAPE`: a Pending may not be live at a checkpoint, return, or halt, and may not be dropped or packed away, so no snapshot can capture a half-issued effect nobody is awaiting |
| Every loop carries metering | Cycle check over the CFG (SCC): a cycle with no SPEND fails admission with `E_UNMETERED_LOOP`; a runaway loop burns budget, not wall-clock |
| Two-phase budget | `engine.py` ledger: RESERVE refused at reserve time if reserved + spent + n exceeds the allowance (`E_BUDGET`, before any effect runs); SPEND settles only against a reservation; RELEASE frees it. The ledger is part of the snapshot |
| Whole machine state is a byte string | `Vm.snapshot()`: frames, operand stack, pending effects, pid counter, awaiting pid, allowance, and ledger, canonically encoded and sealed with a SHA-256 content hash. `Vm.restore` validates the seal (`E_SNAPSHOT_HASH`) and the image binding (`E_IMAGE_MISMATCH`) before reconstructing anything |
| Interpreter performs no I/O | `Vm.step` is a pure function of (image, state, answer); effects surface as Traps and the host answers. The same code path serves live runs and replay |
| Hash-chained, replayable journal | `Journal`: each record commits to the previous record's hash; `verify_chain()` detects edits and truncation. `ReplayHost` serves answers from the journal and raises `DivergenceError` when the run asks for something the journal does not have next |
| Deterministic arithmetic | Wrapping signed-64-bit integers, upstream's rule; identical runs produce byte-identical journals (head-hash equality is tested) |

## What was deliberately NOT adopted

- **The Rust crate and the `.cdxb` wire format.** Our durable-agent
  stack is Python (the fx0 lane, TW-665). Vendoring a Rust binary would
  add a second runtime to operate for zero pattern gain; the implant is
  the pattern on our own stack, exactly the TW-665 posture.
- **The `.cdx` macro assembler.** Upstream's production path is a
  programmatic builder (the assembler is for tests and examples); we
  adopted the same split: `ProgramBuilder` is the production path.
- **Floats, indirect branches, host pointers.** Absent upstream by
  design; absent here. Each would break serializability or the
  verifier's static control-flow graph.

## Lane choice: fx0 durable turns vs the VM lane

The fx0 lane (TW-665) runs host-language step functions and journals
per-step rows; a completed turn's results are durable, but the suspended
turn itself (a half-run step, its locals, its place in the program) is
not a value and cannot move. A crash inside a non-idempotent step
resolves to `interrupted_unknown_outcome`, proven against the real fx0
engine in `test_fx0_lane_marks_crashed_money_step_ambiguous`.

Use the fx0 lane for host-language step functions with journaled
idempotency. Use the VM lane when the suspended turn itself must be a
serializable, verifiable, budget-metered value that can move between
processes: the crash-after-the-money-effect test resumes mid-program in
a fresh machine (and, in the cross-process test, in a fresh interpreter
reading state from files only) with the effect landed exactly once and
zero ambiguous outcomes.

The lanes compose rather than compete: the VM host keeps the fx0
exactly-once effect contract (an effect lands once per effect key; a
re-dispatch returns the recorded result). The VM lane's guarantee is
about the turn; the ledger's guarantee is about the effect.

## Immediate use (TW-274)

`backend/app/durable_vm/programs.py` is the first production program on
the lane: missed-call triage for Twistor Trades (the M2 domain). The
turn reserves a 90-unit budget, looks up three missed calls for one
shop inside a metered loop (10 units per lookup), checkpoints, sends
exactly one callback SMS (25 units), releases the unspent reservation,
and halts with the summed lead score (105). Its host (`TriageHost`) is
where effects live; `send_callback_sms` is the money effect, keyed
`sms:<shop>` in an effect ledger.

Proof, all in `backend/tests/test_durable_vm.py` (21 tests): the
verifier rejects seven classes of bad program before they run; budget
refusal happens before any effect; a simulated process death after the
SMS lands resumes from snapshot bytes with exactly one `tool_issued`
for the SMS across both processes and the three completed lookups never
re-issued; a separate-interpreter resume via
`python -m app.durable_vm.resume_cli` finishes the same turn from files
only; replay reproduces the identical journal head hash; tampered and
truncated journals are caught.

## Known gaps

- **Fuel resets on restore.** Fuel is a host-policy backstop (like the
  fx0 step cap), reset to the default on restore and deliberately not
  part of the snapshot. Budget, the real metering, is in the snapshot.
- **No durable store wired yet.** Snapshots and journals are byte APIs;
  persisting them (Postgres, beside the fx0 tables) lands with the
  first production caller that routes live turns through this lane.
- **No live turn routes through the VM lane yet.** The triage program
  and host are the pattern proof, the same posture TW-665 shipped with
  its crash demo. Routing a real Trades turn is a follow-up ticket, not
  a silent assumption of this one.
