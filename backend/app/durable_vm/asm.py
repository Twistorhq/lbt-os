"""Program builder and sealed image for the durable VM lane (TW-685).

Upstream's production path is a programmatic builder driving a
higher-level frontend (its `.cdx` macro assembler is for tests and
examples); this builder is ours, same shape: declare tools, constants,
and functions, emit instructions, and `build()` seals the result
through the verifier. An Image cannot be constructed except through
`verify.admit_object`, so verification is a precondition, not a mode.

Instruction operands in the object form:
  ldc / checkpoint   -> constant-pool index (int)
  ldi / pack         -> immediate int
  br / brz / brnz    -> instruction index within the function
  call / calltool    -> function / tool name (str)
  reserve/spend/release -> budget dimension name (str)
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from app.durable_vm import isa


class Image:
    """A verified, sealed program. Construct via verify.admit_object."""

    def __init__(self, obj: dict[str, Any], image_hash: str):
        self.obj = obj
        self.hash = image_hash
        self.name = obj["name"]

    def function(self, name: str) -> dict[str, Any]:
        return self.obj["functions"][name]

    def const(self, idx: int) -> tuple:
        c = self.obj["consts"][idx]
        if c["t"] == "str":
            return ("str", c["v"])
        return ("bytes", base64.b64decode(c["v"].encode("ascii")))

    def tool_answer_type(self, tool: str) -> str:
        for t in self.obj["tools"]:
            if t["name"] == tool:
                return t["answer"]
        raise KeyError(f"unknown tool {tool!r}")

    def to_bytes(self) -> bytes:
        return isa.canonical({"object": self.obj, "hash": self.hash})

    @classmethod
    def from_bytes(cls, data: bytes) -> "Image":
        import json

        from app.durable_vm.verify import VerifyError, admit_object

        try:
            raw = json.loads(data)
            obj, claimed = raw["object"], raw["hash"]
        except Exception as exc:
            raise VerifyError("E_IMAGE_HASH", f"unsealed image: {exc}") from exc
        image = admit_object(obj)
        if image.hash != claimed:
            raise VerifyError("E_IMAGE_HASH", "sealed hash does not match content")
        return image


def image_hash_of(obj: dict[str, Any]) -> str:
    return hashlib.sha256(isa.canonical(obj)).hexdigest()


class _FunctionBuilder:
    def __init__(self, prog: "ProgramBuilder", name: str, returns: str, args, maxstack):
        self._prog = prog
        self.name = name
        self.returns = returns
        self.args = list(args)
        self.maxstack = maxstack
        self.code: list[list] = []
        self.labels: dict[str, int] = {}


class ProgramBuilder:
    """Fluent builder; instruction methods append to the current function."""

    def __init__(self, name: str):
        self.name = name
        self._consts: list[dict[str, Any]] = []
        self._const_index: dict[Any, int] = {}
        self._tools: list[dict[str, str]] = []
        self._functions: dict[str, _FunctionBuilder] = {}
        self._fn: _FunctionBuilder | None = None

    # -- declarations ----------------------------------------------------
    def tool(self, name: str, *, answer: str = "int") -> "ProgramBuilder":
        if answer not in isa.ABSTRACT_TYPES:
            raise ValueError(f"tool answer type {answer!r} is not a VM type")
        if not any(t["name"] == name for t in self._tools):
            self._tools.append({"name": name, "answer": answer})
        return self

    def function(
        self, name: str, *, returns: str = "int", args=(), maxstack: int = 8
    ) -> "ProgramBuilder":
        fn = _FunctionBuilder(self, name, returns, args, maxstack)
        self._functions[name] = fn
        self._fn = fn
        return self

    def label(self, name: str) -> "ProgramBuilder":
        self._fn.labels[name] = len(self._fn.code)
        return self

    # -- constants ---------------------------------------------------------
    def _pool(self, value) -> int:
        if isinstance(value, str):
            entry = {"t": "str", "v": value}
            key = ("str", value)
        elif isinstance(value, bytes):
            entry = {"t": "bytes", "v": base64.b64encode(value).decode("ascii")}
            key = ("bytes", value)
        else:
            raise TypeError("only str/bytes constants are pooled; ints use ldi")
        if key not in self._const_index:
            self._const_index[key] = len(self._consts)
            self._consts.append(entry)
        return self._const_index[key]

    # -- instruction emission ---------------------------------------------
    def _emit(self, op: str, operand=None) -> "ProgramBuilder":
        self._fn.code.append([op, operand])
        return self

    def ldc(self, value) -> "ProgramBuilder":
        return self._emit("ldc", self._pool(value))

    def ldi(self, n: int) -> "ProgramBuilder":
        return self._emit("ldi", int(n))

    def dup(self):
        return self._emit("dup")

    def drop(self):
        return self._emit("drop")

    def swap(self):
        return self._emit("swap")

    def rot(self):
        return self._emit("rot")

    def pack(self, n: int):
        return self._emit("pack", int(n))

    def len(self):
        return self._emit("len")

    def idx(self):
        return self._emit("idx")

    def cat(self):
        return self._emit("cat")

    def add(self):
        return self._emit("add")

    def sub(self):
        return self._emit("sub")

    def mul(self):
        return self._emit("mul")

    def eq(self):
        return self._emit("eq")

    def lt(self):
        return self._emit("lt")

    def not_(self):
        return self._emit("not")

    def br(self, label: str):
        return self._emit("br", label)

    def brz(self, label: str):
        return self._emit("brz", label)

    def brnz(self, label: str):
        return self._emit("brnz", label)

    def call(self, function: str):
        return self._emit("call", function)

    def ret(self):
        return self._emit("ret")

    def halt(self):
        return self._emit("halt")

    def calltool(self, tool: str):
        return self._emit("calltool", tool)

    def await_(self):
        return self._emit("await")

    def checkpoint(self, label: str):
        return self._emit("checkpoint", self._pool(label))

    def reserve(self, dim: str):
        return self._emit("reserve", dim)

    def spend(self, dim: str):
        return self._emit("spend", dim)

    def release(self, dim: str):
        return self._emit("release", dim)

    # -- sealing -------------------------------------------------------------
    def to_object(self) -> dict[str, Any]:
        functions = {}
        for name, fn in self._functions.items():
            code = []
            for op, operand in fn.code:
                if op in ("br", "brz", "brnz"):
                    if operand not in fn.labels:
                        from app.durable_vm.verify import VerifyError

                        raise VerifyError(
                            "E_BAD_TARGET", f"unknown label {operand!r} in {name}"
                        )
                    operand = fn.labels[operand]
                code.append([op, operand])
            functions[name] = {
                "args": fn.args,
                "returns": fn.returns,
                "maxstack": fn.maxstack,
                "code": code,
            }
        return {
            "name": self.name,
            "isa": "cdx-py/1",
            "consts": self._consts,
            "tools": self._tools,
            "functions": functions,
            "entry": "main",
        }

    def build(self) -> Image:
        from app.durable_vm.verify import admit_object

        return admit_object(self.to_object())
