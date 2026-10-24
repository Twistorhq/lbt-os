"""Verifier for durable VM images (TW-685).

Abstract interpretation over each function's control-flow graph, iterated
to a fixpoint, in the shape of upstream CinderVM's JVM-style verifier.
An image is admitted only if, for every function:

  - the operand stack shape (depth + types) is single-valued at every
    instruction and never exceeds the declared maxstack;
  - every operand has a type the instruction accepts (the `Top` type
    produced by `idx` is illegal at every use site except drop/pack/eq);
  - branch targets land on real instructions in the same function;
  - no Pending value is live at a checkpoint, a return, or a halt, so
    no snapshot can capture a half-issued effect nobody is awaiting;
  - every cycle in the CFG contains a SPEND, so a runaway loop burns
    budget rather than wall-clock;
  - call/return arity and types agree with the callee's signature.

Diagnostics carry stable codes (E_UNDERFLOW, E_TYPE, ...) so a verify
failure reads like a compiler error, upstream's `diag.rs` discipline.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from app.durable_vm import isa
from app.durable_vm.asm import Image, image_hash_of


class VerifyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, msg: str):
    raise VerifyError(code, msg)


def _pop(stack: tuple, n: int, op: str, fn: str, pc: int) -> tuple:
    if len(stack) < n:
        _fail("E_UNDERFLOW", f"{fn}@{pc}: {op} underflows")
    return stack[: len(stack) - n], stack[len(stack) - n :]


def _need(types: tuple, expected: str, op: str, fn: str, pc: int):
    t = types[-1]
    if t != expected:
        _fail("E_TYPE", f"{fn}@{pc}: {op} wants {expected}, stack has {t}")


def _check_structure(obj: dict[str, Any]):
    if not isinstance(obj, dict) or obj.get("isa") != "cdx-py/1":
        _fail("E_STRUCTURE", "not a cdx-py/1 program object")
    for field in ("name", "consts", "tools", "functions", "entry"):
        if field not in obj:
            _fail("E_STRUCTURE", f"program object missing {field!r}")
    if obj["entry"] not in obj["functions"]:
        _fail("E_STRUCTURE", f"entry function {obj['entry']!r} not defined")
    for t in obj["tools"]:
        if t.get("answer") not in isa.ABSTRACT_TYPES:
            _fail("E_STRUCTURE", f"tool {t.get('name')!r} has bad answer type")
    for c in obj["consts"]:
        if c.get("t") not in ("str", "bytes"):
            _fail("E_STRUCTURE", "constant pool holds only str/bytes")
    for name, fn in obj["functions"].items():
        if fn.get("returns") not in isa.ABSTRACT_TYPES:
            _fail("E_STRUCTURE", f"function {name} has bad return type")
        for a in fn.get("args", []):
            if a not in isa.ABSTRACT_TYPES:
                _fail("E_STRUCTURE", f"function {name} has bad arg type")
        if not fn.get("code"):
            _fail("E_STRUCTURE", f"function {name} has no code")


def _transfer(obj, fn_name, fn, pc, op, operand, stack):
    """One abstract step: returns [(next_pc, new_stack)] or [] if terminal."""
    name = fn_name

    def succ(next_pc, new_stack):
        return [(next_pc, new_stack)]

    if op == "ldc":
        if not isinstance(operand, int) or not (0 <= operand < len(obj["consts"])):
            _fail("E_STRUCTURE", f"{name}@{pc}: ldc operand out of range")
        t = "Str" if obj["consts"][operand]["t"] == "str" else "Bytes"
        return succ(pc + 1, stack + (t,))
    if op == "ldi":
        return succ(pc + 1, stack + ("Int",))
    if op == "dup":
        _, top = _pop(stack, 1, op, name, pc)
        return succ(pc + 1, stack + top)
    if op == "drop":
        rest, top = _pop(stack, 1, op, name, pc)
        if top[0].startswith("Pending"):
            _fail(
                "E_PENDING_ESCAPE",
                f"{name}@{pc}: an unanswered effect may not be dropped",
            )
        return succ(pc + 1, rest)
    if op == "swap":
        rest, top = _pop(stack, 2, op, name, pc)
        return succ(pc + 1, rest + (top[1], top[0]))
    if op == "rot":
        rest, top = _pop(stack, 3, op, name, pc)
        return succ(pc + 1, rest + (top[1], top[2], top[0]))
    if op == "pack":
        rest, items = _pop(stack, int(operand), op, name, pc)
        if any(t.startswith("Pending") for t in items):
            _fail(
                "E_PENDING_ESCAPE",
                f"{name}@{pc}: an unanswered effect may not be packed away",
            )
        return succ(pc + 1, rest + ("List",))
    if op == "len":
        rest, top = _pop(stack, 1, op, name, pc)
        if top[0] not in ("List", "Str", "Bytes"):
            _fail("E_TYPE", f"{name}@{pc}: len wants List/Str/Bytes, has {top[0]}")
        return succ(pc + 1, rest + ("Int",))
    if op == "idx":
        rest, top = _pop(stack, 2, op, name, pc)
        if top[0] != "List" or top[1] != "Int":
            _fail("E_TYPE", f"{name}@{pc}: idx wants (List, Int), has {top}")
        return succ(pc + 1, rest + ("Top",))
    if op == "cat":
        rest, top = _pop(stack, 2, op, name, pc)
        if top[0] != top[1] or top[0] not in ("Str", "List"):
            _fail("E_TYPE", f"{name}@{pc}: cat wants two Str or two List, has {top}")
        return succ(pc + 1, rest + (top[0],))
    if op in ("add", "sub", "mul", "lt"):
        rest, top = _pop(stack, 2, op, name, pc)
        if top != ("Int", "Int"):
            _fail("E_TYPE", f"{name}@{pc}: {op} wants (Int, Int), has {top}")
        return succ(pc + 1, rest + ("Int",))
    if op == "eq":
        rest, _ = _pop(stack, 2, op, name, pc)
        return succ(pc + 1, rest + ("Int",))
    if op == "not":
        rest, top = _pop(stack, 1, op, name, pc)
        _need(top, "Int", op, name, pc)
        return succ(pc + 1, rest + ("Int",))
    if op in ("br", "brz", "brnz"):
        if not isinstance(operand, int) or not (0 <= operand < len(fn["code"])):
            _fail(
                "E_BAD_TARGET",
                f"{name}@{pc}: branch target {operand!r} is not an instruction",
            )
        if op == "br":
            return [(operand, stack)]
        rest, top = _pop(stack, 1, op, name, pc)
        _need(top, "Int", op, name, pc)
        return [(pc + 1, rest), (operand, rest)]
    if op == "call":
        callee = obj["functions"].get(operand)
        if callee is None:
            _fail("E_STRUCTURE", f"{name}@{pc}: call to unknown function {operand!r}")
        arg_types = tuple(isa.ABSTRACT_TYPES[a] for a in callee["args"])
        rest, top = _pop(stack, len(arg_types), op, name, pc)
        if top != arg_types:
            _fail(
                "E_TYPE",
                f"{name}@{pc}: call {operand} wants {arg_types}, has {top}",
            )
        return succ(pc + 1, rest + (isa.ABSTRACT_TYPES[callee["returns"]],))
    if op == "ret":
        want = (isa.ABSTRACT_TYPES[fn["returns"]],)
        if any(t.startswith("Pending") for t in stack):
            _fail("E_PENDING_ESCAPE", f"{name}@{pc}: pending live at return")
        if stack != want:
            _fail("E_TYPE", f"{name}@{pc}: ret wants stack {want}, has {stack}")
        return []
    if op == "halt":
        if any(t.startswith("Pending") for t in stack):
            _fail("E_PENDING_ESCAPE", f"{name}@{pc}: pending live at halt")
        if stack != ("Int",):
            _fail("E_TYPE", f"{name}@{pc}: halt wants stack ('Int',), has {stack}")
        return []
    if op == "calltool":
        if not any(t["name"] == operand for t in obj["tools"]):
            _fail("E_STRUCTURE", f"{name}@{pc}: unknown tool {operand!r}")
        rest, top = _pop(stack, 1, op, name, pc)
        _need(top, "List", op, name, pc)
        return succ(pc + 1, rest + (f"Pending:{operand}",))
    if op == "await":
        rest, top = _pop(stack, 1, op, name, pc)
        if not top[0].startswith("Pending:"):
            _fail("E_TYPE", f"{name}@{pc}: await wants a Pending, has {top[0]}")
        tool = top[0].split(":", 1)[1]
        answer = next(t["answer"] for t in obj["tools"] if t["name"] == tool)
        return succ(pc + 1, rest + (isa.ABSTRACT_TYPES[answer],))
    if op == "checkpoint":
        if any(t.startswith("Pending") for t in stack):
            _fail(
                "E_PENDING_ESCAPE",
                f"{name}@{pc}: pending live at checkpoint;"
                " a snapshot cannot capture a half-issued effect",
            )
        return succ(pc + 1, stack)
    if op in ("reserve", "spend", "release"):
        rest, top = _pop(stack, 1, op, name, pc)
        _need(top, "Int", op, name, pc)
        return succ(pc + 1, rest)
    _fail("E_STRUCTURE", f"{name}@{pc}: unknown opcode {op!r}")


def _verify_function(obj, fn_name, fn):
    code = fn["code"]
    entry_stack = tuple(isa.ABSTRACT_TYPES[a] for a in fn["args"])
    states: dict[int, tuple] = {0: entry_stack}
    # Breadth-first: every predecessor of a merge point is processed
    # before the merge's successors, so a divergent stack shape is
    # reported as E_STACK_SHAPE at the merge, not as a downstream
    # underflow on whichever path happened to be explored first.
    worklist = deque([0])
    edges: dict[int, set[int]] = {}
    while worklist:
        pc = worklist.popleft()
        stack = states[pc]
        if pc >= len(code):
            _fail("E_NO_RETURN", f"{fn_name}: falls off the end without ret/halt")
        op, operand = code[pc]
        for next_pc, new_stack in _transfer(obj, fn_name, fn, pc, op, operand, stack):
            if len(new_stack) > fn["maxstack"]:
                _fail(
                    "E_MAXSTACK",
                    f"{fn_name}@{next_pc}: stack depth {len(new_stack)}"
                    f" exceeds maxstack {fn['maxstack']}",
                )
            edges.setdefault(pc, set()).add(next_pc)
            if next_pc in states:
                if states[next_pc] != new_stack:
                    _fail(
                        "E_STACK_SHAPE",
                        f"{fn_name}@{next_pc}: merged paths disagree on the"
                        f" stack: {states[next_pc]} vs {new_stack}",
                    )
            else:
                states[next_pc] = new_stack
                worklist.append(next_pc)
    _check_cycles_metered(fn_name, fn, edges)


def _check_cycles_metered(fn_name, fn, edges):
    code = fn["code"]
    nodes = set(edges) | {t for targets in edges.values() for t in targets}
    reach: dict[int, set[int]] = {}
    for n in nodes:
        seen, stack = set(), [n]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(edges.get(cur, ()))
        reach[n] = seen
    checked: set[frozenset] = set()
    for n in nodes:
        scc = frozenset(m for m in nodes if m in reach[n] and n in reach[m])
        if not scc or scc in checked:
            continue
        checked.add(scc)
        cyclic = len(scc) > 1 or any(m in edges.get(m, ()) for m in scc)
        if not cyclic:
            continue
        if not any(code[m][0] in isa.METERING_OPS for m in scc):
            _fail(
                "E_UNMETERED_LOOP",
                f"{fn_name}: cycle through pcs {sorted(scc)} contains no"
                " metering instruction (spend); a runaway loop must burn"
                " budget, not wall-clock",
            )


def verify_program(obj: dict[str, Any]) -> None:
    """Raise VerifyError unless the program object is provably safe."""
    _check_structure(obj)
    for fn_name, fn in obj["functions"].items():
        _verify_function(obj, fn_name, fn)


def admit_object(obj: dict[str, Any]) -> Image:
    """The only way to construct an Image: verify, then seal."""
    verify_program(obj)
    return Image(obj, image_hash_of(obj))
