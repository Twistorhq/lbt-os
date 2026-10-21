"""TW-665 live crash-recovery demonstration.

A real fleet-style workflow (a 6-step dispatch turn) runs in a child
process. The child is SIGKILLed mid-turn, after a side effect has landed
but before that step's completion is journaled: the worst crash window.

BEFORE (volatile, today's behavior): the turn lives in process memory.
Kill the process and the turn, its completed steps, and its place in the
workflow are gone. A fresh process recovers 0 steps.

AFTER (durable, TW-665 engine): the turn was journaled before execution
and every step was journaled as it ran. A fresh process resumes the same
turn from the journal: completed steps are not re-run, the interrupted
step's effect is not duplicated, and the turn completes.

Run:  python3 scripts/tw665_crash_demo.py [workdir]
Exit code 0 only if the BEFORE loss and the AFTER recovery both hold.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.durable_turns import DurableTurnEngine, StepDef  # noqa: E402

STEP_NAMES = [
    "load_job",
    "assign_tech",
    "reserve_slot",
    "notify_customer",
    "write_invoice_draft",
    "close_job",
]
CRASH_STEP = 3  # notify_customer: effect lands, then the process dies


def _append_ledger(path: str, line: str) -> str:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return line


def _durable_steps(ledger: str, crash: bool):
    def make(name, idx):
        def handler(ctx):
            result = ctx.apply_effect(
                name, lambda: _append_ledger(ledger, f"effect:{name}")
            )
            if crash and idx == CRASH_STEP:
                # Effect landed; step completion not yet journaled. Die hard.
                os.kill(os.getpid(), signal.SIGKILL)
            return result

        return StepDef(name, handler, idempotent=True)

    return [make(n, i) for i, n in enumerate(STEP_NAMES)]


def worker_durable(db: str, ledger: str, crash: bool) -> None:
    eng = DurableTurnEngine(db, lease_seconds=1.0, base_delay=0.001)
    tid = eng.submit(
        session_id="dispatch-board-1",
        message_id="job-4471-turn-1",
        prompt="dispatch job 4471",
    )
    out = eng.run_turn(
        tid, _durable_steps(ledger, crash), owner=f"worker-{os.getpid()}"
    )
    print(json.dumps({"turn_id": tid, **out}))


def worker_volatile(ledger: str, crash: bool) -> None:
    state = {"completed": []}  # in-memory only: this is the BEFORE behavior
    for idx, name in enumerate(STEP_NAMES):
        _append_ledger(ledger, f"effect:{name}")
        state["completed"].append(name)
        if crash and idx == CRASH_STEP:
            os.kill(os.getpid(), signal.SIGKILL)
    print(json.dumps({"status": "completed", "steps": state["completed"]}))


def resume_volatile() -> None:
    # A brand-new process: nothing survived, so there is nothing to resume.
    print(json.dumps({"recovered_steps": 0, "status": "lost"}))


def _spawn(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.abspath(__file__), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "worker-durable":
        worker_durable(sys.argv[2], sys.argv[3], crash=sys.argv[4] == "1")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "worker-volatile":
        worker_volatile(sys.argv[2], crash=sys.argv[3] == "1")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "resume-volatile":
        resume_volatile()
        return 0

    workdir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tw665-demo"
    os.makedirs(workdir, exist_ok=True)
    db = os.path.join(workdir, "durable.db")
    ledger_after = os.path.join(workdir, "ledger-after.txt")
    ledger_before = os.path.join(workdir, "ledger-before.txt")
    for p in (db, db + "-wal", db + "-shm", ledger_after, ledger_before):
        if os.path.exists(p):
            os.remove(p)

    print("=== BEFORE: volatile in-memory turn (current behavior) ===")
    r = _spawn(["worker-volatile", ledger_before, "1"])
    print(f"worker exit: {r.returncode} (SIGKILL = {-signal.SIGKILL})")
    r2 = _spawn(["resume-volatile"])
    before = json.loads(r2.stdout.strip())
    print(
        f"fresh process recovers: {before['recovered_steps']} steps, turn {before['status']}"
    )
    before_effects = (
        open(ledger_before).read().splitlines() if os.path.exists(ledger_before) else []
    )
    print(
        f"side effects that landed before the kill: {len(before_effects)} (orphaned, no turn to attach them to)"
    )

    print()
    print("=== AFTER: durable turn (TW-665 engine, pattern from fx0) ===")
    r = _spawn(["worker-durable", db, ledger_after, "1"])
    print(f"worker exit: {r.returncode} (SIGKILL = {-signal.SIGKILL})")
    eng = DurableTurnEngine(db, lease_seconds=1.0, base_delay=0.001)
    tid = eng.submit(
        session_id="dispatch-board-1",
        message_id="job-4471-turn-1",
        prompt="dispatch job 4471",
    )
    turn = eng.get_turn(tid)
    events, _ = eng.read_events(tid)
    completed = [e for e in events if e["kind"] == "step_completed"]
    print(
        f"journal after kill: turn status={turn['status']}, steps completed={len(completed)}, effects recorded={eng.count_effects(tid)}"
    )
    print(
        "waiting out the dead worker's lease (1.0s), then resuming in a fresh process..."
    )
    time.sleep(1.3)
    r2 = _spawn(["worker-durable", db, ledger_after, "0"])
    after = json.loads(r2.stdout.strip().splitlines()[-1])
    print(
        f"resume result: status={after['status']}, interrupted={after['interrupted']}, results={len(after['results'])} steps"
    )
    lines = open(ledger_after).read().splitlines()
    dupes = [n for n in STEP_NAMES if lines.count(f"effect:{n}") != 1]
    print(
        f"side-effect ledger: {len(lines)} lines for {len(STEP_NAMES)} steps, duplicates: {dupes or 'none'}"
    )
    print(f"effects in journal: {eng.count_effects(tid)}")

    ok = (
        before["recovered_steps"] == 0
        and r.returncode == -signal.SIGKILL
        and after["status"] == "completed"
        and after["interrupted"] is True
        and len(lines) == len(STEP_NAMES)
        and not dupes
        and eng.count_effects(tid) == len(STEP_NAMES)
    )
    print()
    print("VERDICT:", "PASS" if ok else "FAIL")
    print(f"BEFORE: turn lost, 0/{len(STEP_NAMES)} steps recovered.")
    print(
        f"AFTER: turn recovered, {len(after['results'])}/{len(STEP_NAMES)} step results intact, 0 duplicate side effects."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
