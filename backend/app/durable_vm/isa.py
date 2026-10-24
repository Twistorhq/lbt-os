"""Instruction set and value model for the durable VM lane (TW-685).

The opcode classes mirror the upstream CinderVM `cdx/4` table
(keegan0521/CinderVM, MIT): stack, data, arithmetic, control, effects,
durability, metering. This subset is what a durable agent turn needs;
floats, indirect branches, and host pointers are deliberately absent,
exactly as upstream: each would break serializability or the verifier's
static control-flow graph.

Values are tagged tuples, pointer-free and copyable, so a machine state
serializes as plain data:

    ("int", n)            n wrapped to signed 64-bit
    ("str", s)
    ("bytes", b)
    ("list", (v, ...))
    ("pending", pid)      an issued but unanswered effect

Canonical encoding (used by snapshots and the journal) is JSON-safe:
["i", n] / ["s", s] / ["b", base64] / ["l", [...]] / ["p", pid].
"""

from __future__ import annotations

import base64
import json
from typing import Any

# op name -> (pops, pushes, effect class). Types are the verifier's
# abstract lattice names; Pending carries its tool as "Pending:<tool>".
# Variadic and control ops (pack, call, branches, ret, halt) are spelled
# out in verify.py's transfer function; this table is the reference the
# transfers are checked against in review.
ISA: dict[str, tuple[str, str, str]] = {
    "ldc": ("", "T", "pure"),  # push pooled constant (type from the pool)
    "ldi": ("", "Int", "pure"),
    "dup": ("T", "T T", "pure"),
    "drop": ("T", "", "pure"),
    "swap": ("T T", "T T", "pure"),
    "rot": ("T T T", "T T T", "pure"),
    "pack": ("T*n", "List", "alloc"),
    "len": ("List|Str|Bytes", "Int", "pure"),
    "idx": ("List Int", "Top", "pure"),
    "cat": ("T T", "T", "alloc"),
    "add": ("Int Int", "Int", "pure"),
    "sub": ("Int Int", "Int", "pure"),
    "mul": ("Int Int", "Int", "pure"),
    "eq": ("Top Top", "Int", "pure"),
    "lt": ("Int Int", "Int", "pure"),
    "not": ("Int", "Int", "pure"),
    "br": ("", "", "control"),
    "brz": ("Int", "", "control"),
    "brnz": ("Int", "", "control"),
    "call": ("args", "ret", "control"),
    "ret": ("ret", "", "control"),
    "halt": ("Int", "", "control"),
    "calltool": ("List", "Pending", "suspend"),
    "await": ("Pending", "answer", "suspend"),
    "checkpoint": ("", "", "durable"),
    "reserve": ("Int", "", "meter"),
    "spend": ("Int", "", "meter"),
    "release": ("Int", "", "meter"),
}

# Ops that settle real budget; a CFG cycle must contain at least one.
METERING_OPS = frozenset({"spend"})

_I64_MIN = -(2**63)
_I64_SPAN = 2**64


def wrap_i64(n: int) -> int:
    """Wrap to signed 64-bit, upstream's wrapping-i64 arithmetic rule."""
    return (n - _I64_MIN) % _I64_SPAN + _I64_MIN


def to_value(py: Any) -> tuple:
    """Convert a host (Python) value to a tagged VM value."""
    if isinstance(py, bool):
        return ("int", int(py))
    if isinstance(py, int):
        return ("int", wrap_i64(py))
    if isinstance(py, str):
        return ("str", py)
    if isinstance(py, bytes):
        return ("bytes", py)
    if isinstance(py, (list, tuple)):
        return ("list", tuple(to_value(v) for v in py))
    raise TypeError(f"host value of type {type(py).__name__} has no VM value form")


def from_value(v: tuple) -> Any:
    """Convert a tagged VM value back to a host (Python) value."""
    tag, payload = v
    if tag == "list":
        return [from_value(x) for x in payload]
    if tag == "pending":
        return {"pending": payload}
    return payload


def encode_value(v: tuple) -> list:
    tag, payload = v
    if tag == "int":
        return ["i", payload]
    if tag == "str":
        return ["s", payload]
    if tag == "bytes":
        return ["b", base64.b64encode(payload).decode("ascii")]
    if tag == "list":
        return ["l", [encode_value(x) for x in payload]]
    if tag == "pending":
        return ["p", payload]
    raise ValueError(f"unknown value tag {tag!r}")


def decode_value(enc: list) -> tuple:
    tag, payload = enc
    if tag == "i":
        return ("int", payload)
    if tag == "s":
        return ("str", payload)
    if tag == "b":
        return ("bytes", base64.b64decode(payload.encode("ascii")))
    if tag == "l":
        return ("list", tuple(decode_value(x) for x in payload))
    if tag == "p":
        return ("pending", payload)
    raise ValueError(f"unknown encoded tag {tag!r}")


def canonical(obj: Any) -> bytes:
    """Deterministic bytes for hashing: sorted keys, tight separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


# Program-level type names (builder / function signatures) mapped to the
# verifier's abstract lattice.
ABSTRACT_TYPES = {"int": "Int", "str": "Str", "bytes": "Bytes", "list": "List"}
