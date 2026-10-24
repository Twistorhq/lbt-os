"""The first production program on the durable VM lane (TW-685):
missed-call triage for Twistor Trades, plus its host.

The turn, as bytecode: reserve a 90-unit budget, look up each of three
missed calls for one shop (spending 10 units per lookup, inside a
metered loop), checkpoint, send exactly one callback SMS (spending 25),
release the unspent reservation, and halt with the summed lead score.

The host is where effects live. `lookup_caller` is a pure read.
`send_callback_sms` is the money effect: it lands in an effect ledger
keyed `sms:<shop>`, and a second dispatch with the same key returns the
recorded result WITHOUT sending again. That is the fx0 exactly-once
contract (TW-665); the VM lane composes with it rather than replacing
it. What the VM lane adds is that the turn suspended at the SMS await
is a byte string that can move processes with zero ambiguous outcomes.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.durable_vm.asm import Image, ProgramBuilder
from app.durable_vm.engine import SimulatedCrash

SHOP = "shop-417"
SCORES = {0: 30, 1: 35, 2: 40}  # lead score per missed call, by index


def missed_call_triage_image() -> Image:
    b = ProgramBuilder("missed-call-triage")
    b.tool("lookup_caller", answer="int").tool("send_callback_sms", answer="int")
    b.function("main", returns="int", maxstack=4)
    # Reserve the whole turn's budget up front (refused here, never later).
    b.ldi(90).reserve("units")
    # Loop state on the stack: [total, i]
    b.ldi(0).ldi(0)
    b.label("loop")
    b.dup().ldi(len(SCORES)).lt().brz("end")
    b.dup().ldc(SHOP).pack(2)  # args (i, shop), first-pushed first
    b.calltool("lookup_caller").await_()
    b.ldi(10).spend("units")
    b.rot().add()  # total += score
    b.swap().ldi(1).add()  # i += 1
    b.br("loop")
    b.label("end")
    b.drop()  # drop i, leaving [total]
    b.checkpoint("after-lookups")
    b.dup().ldc(SHOP).pack(2)  # args (total, shop)
    b.calltool("send_callback_sms").await_()
    b.drop()  # drop the sent receipt, leaving [total]
    b.ldi(25).spend("units")
    b.ldi(35).release("units")
    b.halt()
    return b.build()


class FileEffects:
    """An effect ledger persisted as one JSON file, so two processes
    share it (the cross-process resume proof)."""

    def __init__(self, path):
        self.path = Path(path)
        self._data: dict = (
            json.loads(self.path.read_text()) if self.path.exists() else {}
        )

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value
        self.path.write_text(json.dumps(self._data))

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


class TriageHost:
    """Answers the triage program's tool traps with real local effects."""

    def __init__(self, effects=None, *, crash_after_sms: bool = False):
        self.effects = effects if effects is not None else {}
        self.crash_after_sms = crash_after_sms
        self.calls: list = []  # every dispatch attempt, (tool, args)

    def call_tool(self, tool: str, args: list):
        self.calls.append((tool, list(args)))
        if tool == "lookup_caller":
            idx, _shop = args
            return SCORES[idx]
        if tool == "send_callback_sms":
            _total, shop = args
            key = f"sms:{shop}"
            if key in self.effects:
                return self.effects[key]  # landed already: never re-send
            self.effects[key] = 1
            if self.crash_after_sms:
                self.crash_after_sms = False
                raise SimulatedCrash("process died after the SMS landed")
            return 1
        raise KeyError(f"unknown tool {tool!r}")
