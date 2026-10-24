"""The durable VM machine, its journal, and its hosts (TW-685).

Clean-room implant of the CinderVM pattern (keegan0521/CinderVM, MIT)
into the Twistor Core durable-agent runtime, beside the fx0 lane
(TW-665). The three upstream invariants hold here verbatim:

  1. The interpreter performs no I/O. `Vm.step` is a pure function of
     (image, state, answer). When it needs an effect it returns a Trap
     and stops; the host answers. Replay is exact because the replay
     host serves answers from the journal and the interpreter cannot
     tell the difference.
  2. Every value is tagged, copyable, and pointer-free (see isa.py), so
     the whole machine state flattens to a byte string: frames, operand
     stack, pending effects, and the budget ledger, sealed with a
     content hash. Restore validates the seal and the image binding
     before it reconstructs anything.
  3. Verification is a precondition, not a mode (see verify.py): an
     Image exists only through the verifier, so this interpreter
     carries no stack-depth or operand-type checks of its own.

What this lane adds over the fx0 durable-turns lane, in one paragraph:
the fx0 lane journals per-step rows in SQLite and resumes a turn by
reading them back, but the suspended turn itself (a half-run step, its
locals, its place in the program) is not a value and cannot move; a
crash inside a non-idempotent step resolves to
interrupted_unknown_outcome. Here the suspended turn IS a value: it
snapshots to bytes, resumes in another process mid-program, and the
only thing the host must guarantee is the fx0 contract it already
keeps, that an effect lands exactly once per effect key.

Budget is two-phase, upstream's RESERVE/SPEND discipline: a
reservation is refused at reserve time if it would exceed the
allowance, never after the spend; SPEND settles against a reservation;
the ledger is part of the snapshot. A hard fuel allowance (host
policy, reset on restore) backstops execution the way the fx0 lane's
step cap does.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from app.durable_vm import isa
from app.durable_vm.asm import Image

DEFAULT_FUEL = 100_000


class SimulatedCrash(BaseException):
    """Process death, simulated: a BaseException, like the fx0 lane's
    crash tests, so no Exception handler anywhere swallows it."""


class SnapshotError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class DivergenceError(Exception):
    """Replay asked for something the journal does not have next."""


@dataclass
class Trap:
    kind: str  # "tool" | "checkpoint"
    pending: int | None = None
    tool: str | None = None
    args: list | None = None  # host-form values
    label: str | None = None


@dataclass
class Halt:
    code: int


@dataclass
class Fault:
    code: str
    message: str


# ---------------------------------------------------------------------
# Journal: append-only, hash-chained
# ---------------------------------------------------------------------


class Journal:
    """Each record commits to the previous record's hash, so a
    truncated or edited journal is detectable, upstream journal.rs."""

    def __init__(self, records: list[dict] | None = None):
        self.records: list[dict] = records or []

    @property
    def head_hash(self) -> str:
        return self.records[-1]["hash"] if self.records else "0" * 64

    def append(self, kind: str, payload: dict) -> dict:
        record = {
            "seq": len(self.records),
            "kind": kind,
            "payload": payload,
            "prev": self.head_hash,
        }
        record["hash"] = hashlib.sha256(isa.canonical(record)).hexdigest()
        self.records.append(record)
        return record

    def verify_chain(self) -> bool:
        prev = "0" * 64
        for i, record in enumerate(self.records):
            body = {k: record[k] for k in ("seq", "kind", "payload", "prev")}
            if record["seq"] != i or record["prev"] != prev:
                return False
            if hashlib.sha256(isa.canonical(body)).hexdigest() != record["hash"]:
                return False
            prev = record["hash"]
        return True

    def to_bytes(self) -> bytes:
        return isa.canonical({"records": self.records})

    @classmethod
    def from_bytes(cls, data: bytes) -> "Journal":
        return cls(json.loads(data)["records"])


# ---------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------


@dataclass
class _Frame:
    fn: str
    pc: int
    base: int  # index into the flat operand stack where this frame starts


class Vm:
    def __init__(
        self, image: Image, *, budget: dict | None = None, fuel: int = DEFAULT_FUEL
    ):
        self.image = image
        entry = image.obj["entry"]
        self.frames: list[_Frame] = [_Frame(entry, 0, 0)]
        self.stack: list[tuple] = []  # the entry function takes no args
        self.pendings: dict[int, dict] = {}
        self.next_pid = 1
        self.awaiting: int | None = None
        self.allowance: dict[str, int] = dict(budget or {})
        self.ledger: dict[str, dict[str, int]] = {}
        self.fuel = fuel
        self.halted: int | None = None
        self.fault: Fault | None = None

    # -- budget ---------------------------------------------------------
    def _ledger(self, dim: str) -> dict[str, int]:
        return self.ledger.setdefault(dim, {"reserved": 0, "spent": 0})

    # -- snapshot / restore ------------------------------------------------
    def snapshot(self) -> bytes:
        state = {
            "v": 1,
            "image": self.image.hash,
            "frames": [{"fn": f.fn, "pc": f.pc, "base": f.base} for f in self.frames],
            "stack": [isa.encode_value(v) for v in self.stack],
            "pendings": {
                str(pid): {
                    "tool": p["tool"],
                    "args": [isa.encode_value(a) for a in p["args"]],
                }
                for pid, p in self.pendings.items()
            },
            "next_pid": self.next_pid,
            "awaiting": self.awaiting,
            "allowance": self.allowance,
            "ledger": self.ledger,
        }
        seal = hashlib.sha256(isa.canonical(state)).hexdigest()
        return isa.canonical({"state": state, "seal": seal})

    @classmethod
    def restore(cls, image: Image, data: bytes) -> "Vm":
        try:
            raw = json.loads(data)
            state, seal = raw["state"], raw["seal"]
        except Exception as exc:
            raise SnapshotError("E_SNAPSHOT_HASH", f"unparseable snapshot: {exc}")
        if hashlib.sha256(isa.canonical(state)).hexdigest() != seal:
            raise SnapshotError("E_SNAPSHOT_HASH", "snapshot seal does not match")
        if state["image"] != image.hash:
            raise SnapshotError(
                "E_IMAGE_MISMATCH",
                "snapshot was taken under a different image; refusing to"
                " restore rather than jumping into the wrong program",
            )
        vm = cls.__new__(cls)
        vm.image = image
        vm.frames = [_Frame(f["fn"], f["pc"], f["base"]) for f in state["frames"]]
        vm.stack = [isa.decode_value(v) for v in state["stack"]]
        vm.pendings = {
            int(pid): {
                "tool": p["tool"],
                "args": [isa.decode_value(a) for a in p["args"]],
            }
            for pid, p in state["pendings"].items()
        }
        vm.next_pid = state["next_pid"]
        vm.awaiting = state["awaiting"]
        vm.allowance = state["allowance"]
        vm.ledger = state["ledger"]
        vm.fuel = DEFAULT_FUEL
        vm.halted = None
        vm.fault = None
        return vm

    # -- stepping ---------------------------------------------------------
    def step(self, answer: tuple | None = None):
        """Execute one instruction, or complete a suspended await.

        Returns None to continue, or a Trap / Halt / Fault. Pure: no
        I/O happens here, ever."""
        if self.fault is not None:
            return self.fault
        if self.halted is not None:
            return Halt(self.halted)
        if self.awaiting is not None:
            if answer is None:
                pid = self.awaiting
                p = self.pendings[pid]
                return Trap(
                    "tool",
                    pending=pid,
                    tool=p["tool"],
                    args=[isa.from_value(a) for a in p["args"]],
                )
            pid = self.awaiting
            tool = self.pendings[pid]["tool"]
            want = self.image.tool_answer_type(tool)
            if answer[0] != want:
                self.fault = Fault(
                    "E_ANSWER_TYPE",
                    f"tool {tool} declared answer {want}, host answered"
                    f" with a {answer[0]} value",
                )
                return self.fault
            self.stack.append(answer)
            self.awaiting = None
            del self.pendings[pid]
            return None
        if self.fuel <= 0:
            self.fault = Fault("E_FUEL", "instruction fuel exhausted")
            return self.fault
        self.fuel -= 1
        frame = self.frames[-1]
        code = self.image.function(frame.fn)["code"]
        op, operand = code[frame.pc]
        return self._exec(frame, op, operand)

    def _pop(self) -> tuple:
        return self.stack.pop()

    def _exec(self, frame: _Frame, op: str, operand):
        img = self.image
        if op == "ldc":
            self.stack.append(img.const(operand))
            frame.pc += 1
        elif op == "ldi":
            self.stack.append(("int", isa.wrap_i64(int(operand))))
            frame.pc += 1
        elif op == "dup":
            self.stack.append(self.stack[-1])
            frame.pc += 1
        elif op == "drop":
            self.stack.pop()
            frame.pc += 1
        elif op == "swap":
            self.stack[-1], self.stack[-2] = self.stack[-2], self.stack[-1]
            frame.pc += 1
        elif op == "rot":
            x1, x2, x3 = self.stack[-3], self.stack[-2], self.stack[-1]
            self.stack[-3], self.stack[-2], self.stack[-1] = x2, x3, x1
            frame.pc += 1
        elif op == "pack":
            n = int(operand)
            items = self.stack[len(self.stack) - n :]
            del self.stack[len(self.stack) - n :]
            self.stack.append(("list", tuple(items)))
            frame.pc += 1
        elif op == "len":
            v = self._pop()
            n = len(v[1]) if v[0] in ("str", "bytes", "list") else 0
            self.stack.append(("int", n))
            frame.pc += 1
        elif op == "idx":
            idx = self._pop()
            lst = self._pop()
            i = idx[1]
            if lst[0] != "list" or not (0 <= i < len(lst[1])):
                self.fault = Fault("E_INDEX", f"idx {i} out of range")
                return self.fault
            self.stack.append(lst[1][i])
            frame.pc += 1
        elif op == "cat":
            b = self._pop()
            a = self._pop()
            if a[0] == "str":
                self.stack.append(("str", a[1] + b[1]))
            else:
                self.stack.append(("list", a[1] + b[1]))
            frame.pc += 1
        elif op in ("add", "sub", "mul"):
            b = self._pop()[1]
            a = self._pop()[1]
            r = a + b if op == "add" else a - b if op == "sub" else a * b
            self.stack.append(("int", isa.wrap_i64(r)))
            frame.pc += 1
        elif op == "eq":
            b = self._pop()
            a = self._pop()
            self.stack.append(("int", 1 if a == b else 0))
            frame.pc += 1
        elif op == "lt":
            b = self._pop()[1]
            a = self._pop()[1]
            self.stack.append(("int", 1 if a < b else 0))
            frame.pc += 1
        elif op == "not":
            a = self._pop()[1]
            self.stack.append(("int", 0 if a else 1))
            frame.pc += 1
        elif op == "br":
            frame.pc = int(operand)
        elif op == "brz":
            frame.pc = int(operand) if self._pop()[1] == 0 else frame.pc + 1
        elif op == "brnz":
            frame.pc = int(operand) if self._pop()[1] != 0 else frame.pc + 1
        elif op == "call":
            callee = img.function(operand)
            nargs = len(callee["args"])
            base = len(self.stack) - nargs
            frame.pc += 1
            self.frames.append(_Frame(operand, 0, base))
        elif op == "ret":
            result = self._pop()
            done = self.frames.pop()
            del self.stack[done.base :]
            if not self.frames:
                self.halted = result[1] if result[0] == "int" else 0
                return Halt(self.halted)
            self.stack.append(result)
        elif op == "halt":
            self.halted = self._pop()[1]
            return Halt(self.halted)
        elif op == "calltool":
            args = self._pop()
            pid = self.next_pid
            self.next_pid += 1
            self.pendings[pid] = {"tool": operand, "args": list(args[1])}
            self.stack.append(("pending", pid))
            frame.pc += 1
        elif op == "await":
            pid = self._pop()[1]
            self.awaiting = pid
            frame.pc += 1
            p = self.pendings[pid]
            return Trap(
                "tool",
                pending=pid,
                tool=p["tool"],
                args=[isa.from_value(a) for a in p["args"]],
            )
        elif op == "checkpoint":
            label = img.const(operand)[1]
            frame.pc += 1
            return Trap("checkpoint", label=label)
        elif op in ("reserve", "spend", "release"):
            n = self._pop()[1]
            led = self._ledger(operand)
            allowance = self.allowance.get(operand, 0)
            if op == "reserve":
                if n < 0 or led["reserved"] + led["spent"] + n > allowance:
                    self.fault = Fault(
                        "E_BUDGET",
                        f"reserve {n} on {operand} refused: reserved"
                        f" {led['reserved']} + spent {led['spent']} + {n}"
                        f" exceeds allowance {allowance}",
                    )
                    return self.fault
                led["reserved"] += n
            elif op == "spend":
                if n < 0 or n > led["reserved"]:
                    self.fault = Fault(
                        "E_BUDGET",
                        f"spend {n} on {operand} exceeds reserved {led['reserved']}",
                    )
                    return self.fault
                led["reserved"] -= n
                led["spent"] += n
            else:
                if n < 0 or n > led["reserved"]:
                    self.fault = Fault(
                        "E_BUDGET",
                        f"release {n} on {operand} exceeds reserved {led['reserved']}",
                    )
                    return self.fault
                led["reserved"] -= n
            frame.pc += 1
        else:  # pragma: no cover - the verifier admits only known ops
            self.fault = Fault("E_STRUCTURE", f"unknown opcode {op!r}")
            return self.fault
        return None


# ---------------------------------------------------------------------
# Runner + hosts
# ---------------------------------------------------------------------


@dataclass
class Outcome:
    status: str  # "halted" | "fault"
    halt_code: int | None
    fault_code: str | None
    journal: Journal
    vm: Vm
    snapshots: dict = field(default_factory=dict)  # checkpoint label -> bytes


class _Host:
    def call_tool(self, tool: str, args: list) -> Any:
        raise NotImplementedError


def _answer_record(trap: Trap, answer) -> dict:
    return {
        "pending": trap.pending,
        "tool": trap.tool,
        "args": [isa.encode_value(isa.to_value(a)) for a in trap.args],
        "answer": isa.encode_value(isa.to_value(answer)),
    }


def run_turn(
    image: Image,
    host: _Host,
    *,
    vm: Vm | None = None,
    journal: Journal | None = None,
    budget: dict | None = None,
    fuel: int = DEFAULT_FUEL,
    on_checkpoint: Callable[[str, Vm], None] | None = None,
) -> Outcome:
    """Drive a VM to halt/fault, journaling every effect boundary.

    The journal records an effect BEFORE the host dispatches it
    (tool_issued), then its answer (tool_answered). A crash between the
    two leaves the journal exactly where the process died; resuming
    with the same journal and a restored VM re-dispatches only that one
    outstanding effect, and the host's exactly-once effect ledger (the
    fx0 contract) keeps the real-world effect single.

    `budget` applies when the Vm is constructed here; a restored Vm
    carries its allowance in its snapshot.
    """
    journal = journal if journal is not None else Journal()
    vm = vm if vm is not None else Vm(image, budget=budget, fuel=fuel)
    if not journal.records:
        journal.append("image", {"image": image.hash, "name": image.name})
    outcome = Outcome("fault", None, None, journal, vm)

    def finish(ev) -> Outcome:
        if isinstance(ev, Halt):
            journal.append("halted", {"code": ev.code})
            outcome.status, outcome.halt_code = "halted", ev.code
        else:
            journal.append("fault", {"code": ev.code, "message": ev.message})
            outcome.status, outcome.fault_code = "fault", ev.code
        return outcome

    # Resume path: a restored VM may be suspended at an await whose
    # effect was issued (and journaled) before the crash.
    if vm.awaiting is not None:
        ev = vm.step()  # re-surfaces the outstanding Trap
        assert isinstance(ev, Trap) and ev.kind == "tool"
        answered = next(
            (
                r
                for r in journal.records
                if r["kind"] == "tool_answered"
                and r["payload"]["pending"] == ev.pending
            ),
            None,
        )
        if answered is not None:
            vm.step(isa.decode_value(answered["payload"]["answer"]))
        else:
            answer = host.call_tool(ev.tool, ev.args)
            journal.append("tool_answered", _answer_record(ev, answer))
            ev2 = vm.step(isa.to_value(answer))
            if isinstance(ev2, (Halt, Fault)):
                return finish(ev2)

    while True:
        ev = vm.step()
        if ev is None:
            continue
        if isinstance(ev, (Halt, Fault)):
            return finish(ev)
        if ev.kind == "checkpoint":
            journal.append("checkpoint", {"label": ev.label})
            outcome.snapshots[ev.label] = vm.snapshot()
            if on_checkpoint is not None:
                on_checkpoint(ev.label, vm)
            continue
        # Tool trap: journal the issue BEFORE dispatch (upstream rule).
        journal.append(
            "tool_issued",
            {
                "pending": ev.pending,
                "tool": ev.tool,
                "args": [isa.encode_value(isa.to_value(a)) for a in ev.args],
            },
        )
        answer = host.call_tool(ev.tool, ev.args)
        journal.append("tool_answered", _answer_record(ev, answer))
        ev2 = vm.step(isa.to_value(answer))
        if isinstance(ev2, (Halt, Fault)):
            return finish(ev2)


class ReplayHost(_Host):
    """Serves answers from a recorded journal instead of a live host.

    If the interpreter asks for something the journal does not have
    next, that is a divergence, named here, not a silent substitution.
    """

    def __init__(self, journal: Journal):
        self._answers = [
            r["payload"] for r in journal.records if r["kind"] == "tool_answered"
        ]
        self._pos = 0

    def call_tool(self, tool: str, args: list) -> Any:
        if self._pos >= len(self._answers):
            raise DivergenceError(
                f"journal exhausted: no recorded answer for {tool}({args})"
            )
        record = self._answers[self._pos]
        recorded_args = [isa.from_value(isa.decode_value(a)) for a in record["args"]]
        if record["tool"] != tool or recorded_args != list(args):
            raise DivergenceError(
                f"replay divergence at answer {self._pos}: journal has"
                f" {record['tool']}({recorded_args}), run asked for"
                f" {tool}({list(args)})"
            )
        self._pos += 1
        return isa.from_value(isa.decode_value(record["answer"]))
