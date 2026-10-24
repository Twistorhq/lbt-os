"""TW-685: CinderVM-pattern durable VM lane.

Pattern-mined from keegan0521/CinderVM (MIT, LICENSE read live at the
upstream repo root 2026-10-24; the upstream README's "Apache-2.0" line
contradicts its own LICENSE file, which is MIT and governs). Clean-room
pattern implant, not a port: no CinderVM code is copied.

What this lane adds over the fx0 durable-turns lane (TW-665), and what
these tests pin down:
  1. The whole machine state is a byte string. A suspended turn snapshots
     to bytes, moves to another process, and resumes mid-program with
     zero ambiguous outcomes. The fx0 lane journals per-step rows; a
     crash inside a non-idempotent step leaves that step
     interrupted_unknown_outcome (see the before/after test at the end).
  2. Programs are verified before they run: stack depth and operand
     types are proven by abstract interpretation, branch targets are
     validated, every loop must carry a metering instruction, and no
     Pending (unanswered effect) may be live at a checkpoint.
  3. Effects are two-phase metered: budget is RESERVED before it is
     SPENT, and a reservation that would exceed the allowance is refused
     at reserve time, never after the spend.
  4. The journal is hash-chained and replayable: replaying a run from
     its journal reproduces the identical journal, and a truncated or
     edited journal is detected, not silently trusted. The chain is
     verified where a journal crosses a trust boundary (the
     resume_cli load path; run_turn's resume branch), so an edited
     journal is refused before any recorded answer is fed (TW-685
     fix round, Rosa M1).
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.durable_vm.asm import ProgramBuilder
from app.durable_vm.engine import (
    DivergenceError,
    Journal,
    SimulatedCrash,
    SnapshotError,
    Vm,
    run_turn,
)
from app.durable_vm.programs import TriageHost, missed_call_triage_image
from app.durable_vm.verify import VerifyError


def build_simple(body, *, maxstack=4):
    b = ProgramBuilder("t").function("main", returns="int", maxstack=maxstack)
    body(b)
    return b.build()


# ---------------------------------------------------------------------
# Verifier: bad programs never start
# ---------------------------------------------------------------------


def test_verifier_rejects_stack_underflow():
    def body(b):
        b.add().halt()

    with pytest.raises(VerifyError) as exc:
        build_simple(body)
    assert exc.value.code == "E_UNDERFLOW"


def test_verifier_rejects_type_error():
    def body(b):
        b.ldc("shop").ldi(1).add().halt()

    with pytest.raises(VerifyError) as exc:
        build_simple(body)
    assert exc.value.code == "E_TYPE"


def test_verifier_rejects_forged_branch_target():
    b = ProgramBuilder("t").function("main", returns="int", maxstack=2)
    b.label("nowhere").ldi(0).br("nowhere")
    obj = b.to_object()
    # Forge a target past the end of the code: the builder would never
    # emit this, a hand-assembled or corrupted image could.
    obj["functions"]["main"]["code"][1][1] = 999
    from app.durable_vm.verify import admit_object

    with pytest.raises(VerifyError) as exc:
        admit_object(obj)
    assert exc.value.code == "E_BAD_TARGET"


def test_verifier_rejects_unmetered_loop():
    def body(b):
        b.label("loop").ldi(1).drop().br("loop")

    with pytest.raises(VerifyError) as exc:
        build_simple(body)
    assert exc.value.code == "E_UNMETERED_LOOP"


def test_verifier_rejects_pending_live_at_checkpoint():
    def body(b):
        b.tool("lookup", answer="int")
        b.ldc("shop-417").pack(1).calltool("lookup")
        b.checkpoint("mid").drop().ldi(0).halt()

    with pytest.raises(VerifyError) as exc:
        build_simple(body)
    assert exc.value.code == "E_PENDING_ESCAPE"


def test_verifier_rejects_stack_above_maxstack():
    def body(b):
        b.ldi(1).ldi(2).add().halt()

    with pytest.raises(VerifyError) as exc:
        build_simple(body, maxstack=1)
    assert exc.value.code == "E_MAXSTACK"


def test_verifier_rejects_divergent_stack_shape_at_merge():
    def body(b):
        # The branch path reaches "mid" with an empty stack, the
        # fall-through path with one Int: a merge the verifier must refuse.
        b.ldi(1).brz("mid").ldi(2).label("mid").ldi(5).drop().drop().ldi(0).halt()

    with pytest.raises(VerifyError) as exc:
        build_simple(body)
    assert exc.value.code == "E_STACK_SHAPE"


# ---------------------------------------------------------------------
# The machine: deterministic, metered, journaled
# ---------------------------------------------------------------------


def test_echo_program_halts_with_length():
    # Mirrors the upstream examples/echo.cdx: len("hello from cinder").
    image = build_simple(lambda b: b.ldc("hello from cinder").len().halt(), maxstack=2)
    out = run_turn(image, TriageHost())
    assert out.status == "halted"
    assert out.halt_code == 17


def test_budget_reservation_refused_before_any_effect():
    def body(b):
        b.ldi(999).reserve("units").ldi(0).halt()

    image = build_simple(body, maxstack=2)
    host = TriageHost()
    out = run_turn(image, host, budget={"units": 100})
    assert out.status == "fault"
    assert out.fault_code == "E_BUDGET"
    assert host.calls == []


def test_runaway_loop_burns_budget_instead_of_spinning():
    def body(b):
        b.label("loop").ldi(10).reserve("units").ldi(10).spend("units").br("loop")

    image = build_simple(body, maxstack=2)
    out = run_turn(image, TriageHost(), budget={"units": 25})
    assert out.status == "fault"
    assert out.fault_code == "E_BUDGET"


def test_triage_run_completes_with_exactly_one_sms():
    image = missed_call_triage_image()
    host = TriageHost()
    out = run_turn(image, host, budget={"units": 100})
    assert out.status == "halted"
    assert out.halt_code == 105  # 30 + 35 + 40 from the three lookups
    sms = [c for c in host.calls if c[0] == "send_callback_sms"]
    assert len(sms) == 1
    assert out.journal.verify_chain()


def test_crash_after_money_effect_resumes_with_zero_ambiguity():
    image = missed_call_triage_image()
    effects: dict = {}
    host = TriageHost(effects, crash_after_sms=True)
    journal = Journal()
    vm = Vm(image, budget={"units": 100})
    with pytest.raises(SimulatedCrash):
        run_turn(image, host, vm=vm, journal=journal)
    # The effect landed exactly once; its answer never reached the VM.
    assert list(effects) == ["sms:shop-417"]
    snapshot = vm.snapshot()
    journal_bytes = journal.to_bytes()

    # A brand-new machine and journal, restored from bytes only.
    vm2 = Vm.restore(image, snapshot)
    journal2 = Journal.from_bytes(journal_bytes)
    host2 = TriageHost(effects)  # same effect ledger, no crash this time
    out = run_turn(image, host2, vm=vm2, journal=journal2, budget={"units": 100})
    assert out.status == "halted"
    assert out.halt_code == 105
    # No second send: the resumed host deduped on its effect ledger, and
    # the journal shows exactly one issued send across both processes.
    assert list(effects) == ["sms:shop-417"]
    issued = [r for r in journal2.records if r["kind"] == "tool_issued"]
    assert len([r for r in issued if r["payload"]["tool"] == "send_callback_sms"]) == 1
    lookups = [r for r in issued if r["payload"]["tool"] == "lookup_caller"]
    assert len(lookups) == 3  # completed steps of work never re-issued
    assert journal2.verify_chain()


def test_snapshot_rejects_a_different_image():
    image = missed_call_triage_image()
    other = build_simple(lambda b: b.ldi(0).halt(), maxstack=1)
    vm = Vm(image, budget={"units": 100})
    with pytest.raises(SnapshotError) as exc:
        Vm.restore(other, vm.snapshot())
    assert exc.value.code == "E_IMAGE_MISMATCH"


def test_snapshot_rejects_tampered_bytes():
    image = missed_call_triage_image()
    vm = Vm(image, budget={"units": 100})
    snap = bytearray(vm.snapshot())
    snap[-8] ^= 0xFF
    with pytest.raises(SnapshotError) as exc:
        Vm.restore(image, bytes(snap))
    assert exc.value.code == "E_SNAPSHOT_HASH"


def test_replay_reproduces_the_identical_journal():
    image = missed_call_triage_image()
    out = run_turn(image, TriageHost(), budget={"units": 100})
    from app.durable_vm.engine import ReplayHost

    replayed = run_turn(image, ReplayHost(out.journal), budget={"units": 100})
    assert replayed.status == "halted"
    assert replayed.halt_code == out.halt_code
    assert replayed.journal.head_hash == out.journal.head_hash


def test_tampered_journal_fails_chain_verification():
    image = missed_call_triage_image()
    out = run_turn(image, TriageHost(), budget={"units": 100})
    raw = json.loads(out.journal.to_bytes())
    raw["records"][2]["payload"]["args"] = ["tampered"]
    forged = Journal.from_bytes(json.dumps(raw).encode())
    assert not forged.verify_chain()


def test_truncated_journal_diverges_on_replay():
    image = missed_call_triage_image()
    out = run_turn(image, TriageHost(), budget={"units": 100})
    raw = json.loads(out.journal.to_bytes())
    raw["records"] = [
        r
        for r in raw["records"]
        if not (
            r["kind"] == "tool_answered"
            and r["payload"].get("tool") == "send_callback_sms"
        )
    ]
    from app.durable_vm.engine import ReplayHost

    truncated = Journal.from_bytes(json.dumps(raw).encode())
    with pytest.raises(DivergenceError):
        run_turn(image, ReplayHost(truncated), budget={"units": 100})


def test_runs_are_deterministic():
    image = missed_call_triage_image()
    a = run_turn(image, TriageHost(), budget={"units": 100})
    b = run_turn(image, TriageHost(), budget={"units": 100})
    assert a.journal.head_hash == b.journal.head_hash


def test_checkpoint_produces_a_restorable_snapshot():
    image = missed_call_triage_image()
    seen = {}

    def on_checkpoint(label, vm):
        seen[label] = vm.snapshot()

    out = run_turn(
        image, TriageHost(), budget={"units": 100}, on_checkpoint=on_checkpoint
    )
    assert out.status == "halted"
    assert "after-lookups" in seen
    restored = Vm.restore(image, seen["after-lookups"])
    assert restored is not None


def test_snapshot_moves_to_a_fresh_process_and_resumes(tmp_path):
    """The immediate-use proof, cross-process: process 1 runs the triage
    turn and dies after the SMS effect lands; process 2 (a separate
    interpreter, state moved only as files) resumes and finishes."""
    image = missed_call_triage_image()
    effects: dict = {}
    host = TriageHost(effects, crash_after_sms=True)
    journal = Journal()
    vm = Vm(image, budget={"units": 100})
    with pytest.raises(SimulatedCrash):
        run_turn(image, host, vm=vm, journal=journal)

    d = tmp_path
    (d / "image.bin").write_bytes(image.to_bytes())
    (d / "snapshot.bin").write_bytes(vm.snapshot())
    (d / "journal.json").write_bytes(journal.to_bytes())
    (d / "effects.json").write_text(json.dumps(effects))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.durable_vm.resume_cli",
            str(d / "image.bin"),
            str(d / "snapshot.bin"),
            str(d / "journal.json"),
            str(d / "effects.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["status"] == "halted"
    assert result["halt_code"] == 105
    assert result["sms_effects"] == 1


# ---------------------------------------------------------------------
# Fix round (Rosa TW-685 review): call/ret coverage (m1), and the
# journal chain verified where state crosses a trust boundary (M1)
# ---------------------------------------------------------------------


def test_call_ret_roundtrip_halts_with_the_return_value():
    b = ProgramBuilder("t")
    b.function("main", returns="int", maxstack=2)
    b.ldi(40).ldi(2).call("add2").halt()
    b.function("add2", args=("int", "int"), returns="int", maxstack=2)
    b.add().ret()
    image = b.build()
    out = run_turn(image, TriageHost())
    assert out.status == "halted"
    assert out.halt_code == 42


def test_verifier_rejects_call_with_mismatched_argument_type():
    b = ProgramBuilder("t")
    b.function("main", returns="int", maxstack=2)
    b.ldc("shop").call("takes_int").halt()
    b.function("takes_int", args=("int",), returns="int", maxstack=1)
    b.ret()
    with pytest.raises(VerifyError) as exc:
        b.build()
    assert exc.value.code == "E_TYPE"


def _stage_crashed_triage(tmp_path):
    """Run the triage turn to the SMS crash and stage the four
    cross-process files exactly as the resume proof does."""
    image = missed_call_triage_image()
    effects: dict = {}
    host = TriageHost(effects, crash_after_sms=True)
    journal = Journal()
    vm = Vm(image, budget={"units": 100})
    with pytest.raises(SimulatedCrash):
        run_turn(image, host, vm=vm, journal=journal)
    d = tmp_path
    (d / "image.bin").write_bytes(image.to_bytes())
    (d / "snapshot.bin").write_bytes(vm.snapshot())
    (d / "journal.json").write_bytes(journal.to_bytes())
    (d / "effects.json").write_text(json.dumps(effects))
    return d


def _forge_first_answer(journal_bytes: bytes) -> bytes:
    """Edit the first recorded answer in a journal; the records no
    longer match their hashes, so the chain does not verify."""
    raw = json.loads(journal_bytes)
    answered = next(r for r in raw["records"] if r["kind"] == "tool_answered")
    answered["payload"]["answer"] = ["i", 777]
    return json.dumps(raw).encode()


def test_resume_cli_refuses_a_chain_broken_journal(tmp_path):
    d = _stage_crashed_triage(tmp_path)
    forged = _forge_first_answer((d / "journal.json").read_bytes())
    (d / "journal.json").write_bytes(forged)
    assert not Journal.from_bytes(forged).verify_chain()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.durable_vm.resume_cli",
            str(d / "image.bin"),
            str(d / "snapshot.bin"),
            str(d / "journal.json"),
            str(d / "effects.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "chain" in proc.stderr.lower()


def test_resume_branch_refuses_a_chain_broken_journal():
    image = missed_call_triage_image()
    effects: dict = {}
    host = TriageHost(effects, crash_after_sms=True)
    journal = Journal()
    vm = Vm(image, budget={"units": 100})
    with pytest.raises(SimulatedCrash):
        run_turn(image, host, vm=vm, journal=journal)
    forged = Journal.from_bytes(_forge_first_answer(journal.to_bytes()))
    assert not forged.verify_chain()
    vm2 = Vm.restore(image, vm.snapshot())
    with pytest.raises(DivergenceError):
        run_turn(image, TriageHost(effects), vm=vm2, journal=forged)
    # Nothing was fed and nothing re-sent: the effect ledger is untouched.
    assert list(effects) == ["sms:shop-417"]


# ---------------------------------------------------------------------
# Before / after: the same crash on the fx0 lane (TW-665)
# ---------------------------------------------------------------------


def test_fx0_lane_marks_crashed_money_step_ambiguous(tmp_path):
    """Before: on the fx0 durable-turns lane, a process death inside the
    non-idempotent money step leaves the turn completed but the step's
    outcome UNKNOWN, and the lane exposes no snapshot of the suspended
    turn to move elsewhere. The VM lane's answer to the same crash is
    test_crash_after_money_effect_resumes_with_zero_ambiguity above."""
    from app.durable_turns import DurableTurnEngine, StepDef

    class Death(BaseException):
        pass

    sent: list = []

    def lookup(ctx):
        return 30

    def sms(ctx):
        sent.append(1)
        raise Death()  # effect landed, process died before journaling

    def report(ctx):
        return "done"

    steps = [
        StepDef("lookup", lookup, idempotent=True),
        StepDef("send_sms", sms, idempotent=False),
        StepDef("report", report, idempotent=True),
    ]
    db = str(tmp_path / "fx0.db")
    engine = DurableTurnEngine(db, lease_seconds=30)
    turn_id = engine.submit(session_id="s1", message_id="m1", prompt="triage")
    with pytest.raises(Death):
        engine.run_turn(turn_id, steps, owner="w1")
    # Expire the dead worker's lease directly in the store: exactly what
    # the passage of time does, with no wall-clock race. (A short lease
    # plus sleep made this test flaky under load: the fence revalidation
    # fired mid-run on whichever worker happened to be slow that run.)
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE durable_turns SET lease_expires_at=0 WHERE turn_id=?",
            (turn_id,),
        )
    # The resumer is a different worker (the multi-process story this
    # lane exists for), on the same store.
    resumer = DurableTurnEngine(db, lease_seconds=30)
    out = resumer.run_turn(turn_id, steps, owner="w2")
    assert out["status"] == "completed"
    assert out["results"][1] == {"interrupted_unknown_outcome": True}
    assert len(sent) == 1
    # The lane has no serializable suspended-turn state to hand over.
    assert not hasattr(engine, "snapshot")
    assert not hasattr(engine, "restore")
