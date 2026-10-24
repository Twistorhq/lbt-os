"""Durable VM lane (TW-685): the CinderVM pattern on our own stack.

Pattern source: keegan0521/CinderVM, MIT (LICENSE read live at the
upstream repo root 2026-10-24). No upstream code is copied and no Rust
binary is vendored: the stack here is Python, so the pattern is
implanted clean-room, the same way fx0 was in TW-665. The upstream
repo was cloned and read at source level for the evaluation (isa.rs,
verify.rs, journal.rs, budget.rs, interp.rs); see
docs/TW-685-cindervm-durable-vm-pattern.md.

Lane choice, in one line: use the fx0 durable-turns lane for
host-language step functions with journaled idempotency; use this lane
when the suspended turn itself must be a serializable, verifiable,
budget-metered value that can move between processes.
"""

from app.durable_vm.asm import Image, ProgramBuilder
from app.durable_vm.engine import (
    DivergenceError,
    Journal,
    Outcome,
    ReplayHost,
    SimulatedCrash,
    SnapshotError,
    Vm,
    run_turn,
)
from app.durable_vm.verify import VerifyError, admit_object, verify_program

__all__ = [
    "DivergenceError",
    "Image",
    "Journal",
    "Outcome",
    "ProgramBuilder",
    "ReplayHost",
    "SimulatedCrash",
    "SnapshotError",
    "VerifyError",
    "Vm",
    "admit_object",
    "run_turn",
    "verify_program",
]
