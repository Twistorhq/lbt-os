"""Cross-process resume entry for the durable VM lane (TW-685).

Usage:
    python -m app.durable_vm.resume_cli IMAGE SNAPSHOT JOURNAL EFFECTS

Loads a sealed image, a snapshot taken by a process that has since
died, its journal, and the shared effect-ledger file; resumes the turn
to completion; prints one JSON line with the outcome. State crosses
the process boundary only as files: that is the lane's whole point.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.durable_vm.asm import Image
from app.durable_vm.engine import Journal, Vm, run_turn
from app.durable_vm.programs import FileEffects, TriageHost


def main(argv: list[str]) -> int:
    image_path, snapshot_path, journal_path, effects_path = argv
    image = Image.from_bytes(Path(image_path).read_bytes())
    vm = Vm.restore(image, Path(snapshot_path).read_bytes())
    journal = Journal.from_bytes(Path(journal_path).read_bytes())
    if not journal.verify_chain():
        print(
            "error: journal hash chain verification failed; refusing"
            " to resume from a tampered or corrupted journal",
            file=sys.stderr,
        )
        return 1
    effects = FileEffects(effects_path)
    out = run_turn(image, TriageHost(effects), vm=vm, journal=journal)
    result = {
        "status": out.status,
        "halt_code": out.halt_code,
        "sms_effects": len([k for k in effects if k.startswith("sms:")]),
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
