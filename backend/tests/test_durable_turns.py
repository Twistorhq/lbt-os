"""TW-665: durable agent turn engine, pattern-mined from vercel-labs/fx0.

Pattern source: fx0 / libfx (Apache-2.0). Adopted: persist-before-execute,
per-step journal, resume-after-crash, idempotency declaration per tool,
single-worker fencing, 64-step turn cap, retries via TW-208 self-healing.
NOT adopted: Vercel Workflow World, Vercel Queues, AI Gateway (vendor paths).

A SimulatedCrash is a BaseException on purpose: a real process death is not
an Exception the engine can catch, so recovery must work from the journal
alone, with a brand-new engine instance on the same store file.
"""

import sqlite3
import threading
import time

import pytest

from app.durable_turns import (
    DurableTurnEngine,
    JournalMismatch,
    StepDef,
    TurnFenced,
)


class SimulatedCrash(BaseException):
    pass


def make_engine(tmp_path, **kw):
    kw.setdefault("lease_seconds", 0.1)
    kw.setdefault("base_delay", 0.001)
    return DurableTurnEngine(str(tmp_path / "turns.db"), **kw)


def test_prompt_is_persisted_before_execution_and_deduped(tmp_path):
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="do the thing")
    # Nothing has run yet, but the turn already exists in the store.
    turn = eng.get_turn(tid)
    assert turn["status"] == "pending"
    assert turn["prompt"] == "do the thing"
    # A retried submission with the same message id reaches the same turn.
    assert eng.submit(session_id="s1", message_id="m1", prompt="do the thing") == tid


def test_completed_turn_runs_each_step_once(tmp_path):
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    calls = []
    steps = [
        StepDef("a", lambda ctx: calls.append("a") or "ra", idempotent=True),
        StepDef("b", lambda ctx: calls.append("b") or "rb", idempotent=False),
    ]
    out = eng.run_turn(tid, steps, owner="w1")
    assert out["status"] == "completed"
    assert out["results"] == ["ra", "rb"]
    assert calls == ["a", "b"]


def test_crash_mid_turn_resumes_from_last_completed_step(tmp_path):
    db = str(tmp_path / "turns.db")
    eng = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    calls = []

    def boom(ctx):
        calls.append("c")
        raise SimulatedCrash()

    steps = [
        StepDef("a", lambda ctx: calls.append("a") or "ra", idempotent=True),
        StepDef("b", lambda ctx: calls.append("b") or "rb", idempotent=True),
        StepDef("c", boom, idempotent=True),
        StepDef("d", lambda ctx: calls.append("d") or "rd", idempotent=True),
    ]
    with pytest.raises(SimulatedCrash):
        eng.run_turn(tid, steps, owner="w1")
    assert calls == ["a", "b", "c"]

    time.sleep(0.15)  # lease from the dead worker expires
    eng2 = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    steps2 = [
        StepDef("a", lambda ctx: calls.append("a2") or "ra", idempotent=True),
        StepDef("b", lambda ctx: calls.append("b2") or "rb", idempotent=True),
        StepDef("c", lambda ctx: calls.append("c2") or "rc", idempotent=True),
        StepDef("d", lambda ctx: calls.append("d2") or "rd", idempotent=True),
    ]
    out = eng2.run_turn(tid, steps2, owner="w2")
    assert out["status"] == "completed"
    # Completed steps are NOT re-run; the interrupted idempotent step is.
    assert calls == ["a", "b", "c", "c2", "d2"]
    assert out["results"] == ["ra", "rb", "rc", "rd"]
    assert out["interrupted"] is True


def test_interrupted_non_idempotent_step_is_not_rerun(tmp_path):
    db = str(tmp_path / "turns.db")
    eng = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    calls = []

    def send_then_crash(ctx):
        calls.append("send")
        raise SimulatedCrash()

    steps = [
        StepDef("send", send_then_crash, idempotent=False),
        StepDef("after", lambda ctx: calls.append("after") or "ok", idempotent=True),
    ]
    with pytest.raises(SimulatedCrash):
        eng.run_turn(tid, steps, owner="w1")
    time.sleep(0.15)
    eng2 = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    out = eng2.run_turn(tid, steps, owner="w2")
    assert calls == ["send", "after"]  # send never re-ran
    assert out["results"][0] == {"interrupted_unknown_outcome": True}
    assert out["status"] == "completed"


def test_effect_applied_before_crash_is_not_duplicated_on_resume(tmp_path):
    db = str(tmp_path / "turns.db")
    eng = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    effects = []

    def step(ctx):
        ctx.apply_effect("charge", lambda: effects.append(1) or "charged")
        raise SimulatedCrash()  # effect landed, step completion never journaled

    with pytest.raises(SimulatedCrash):
        eng.run_turn(tid, [StepDef("pay", step, idempotent=True)], owner="w1")
    time.sleep(0.15)
    eng2 = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)

    def step2(ctx):
        return ctx.apply_effect("charge", lambda: effects.append(1) or "charged")

    out = eng2.run_turn(tid, [StepDef("pay", step2, idempotent=True)], owner="w2")
    assert out["status"] == "completed"
    assert effects == [1]  # exactly one side effect


def test_step_must_declare_idempotency(tmp_path):
    with pytest.raises(ValueError):
        StepDef("x", lambda ctx: None, idempotent=None)


def test_step_cap_stops_runaway_turn(tmp_path):
    eng = make_engine(tmp_path, step_cap=3)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    steps = [StepDef(f"s{i}", lambda ctx: "ok", idempotent=True) for i in range(5)]
    out = eng.run_turn(tid, steps, owner="w1")
    assert out["status"] == "step_cap"
    assert len(out["results"]) == 3
    assert eng.get_turn(tid)["status"] == "step_cap"


def test_default_step_cap_is_64(tmp_path):
    eng = make_engine(tmp_path)
    assert eng.step_cap == 64


def test_transient_failure_retries_with_backoff(tmp_path):
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    attempts = {"n": 0}

    def flaky(ctx):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("connection reset")
        return "healed"

    out = eng.run_turn(tid, [StepDef("flaky", flaky, idempotent=True)], owner="w1")
    assert out["status"] == "completed"
    assert attempts["n"] == 3


def test_permanent_failure_fails_turn_without_retry(tmp_path):
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    attempts = {"n": 0}

    def bad(ctx):
        attempts["n"] += 1
        raise ValueError("bad input")

    out = eng.run_turn(tid, [StepDef("bad", bad, idempotent=True)], owner="w1")
    assert out["status"] == "failed"
    assert attempts["n"] == 1


def test_second_worker_is_fenced_out_while_lease_live(tmp_path):
    eng = make_engine(tmp_path, lease_seconds=60)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    eng.acquire_lease(tid, owner="w1")
    with pytest.raises(TurnFenced):
        eng.run_turn(tid, [StepDef("a", lambda ctx: 1, idempotent=True)], owner="w2")


def test_events_cursor_reads_each_event_once(tmp_path):
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    eng.run_turn(tid, [StepDef("a", lambda ctx: "ra", idempotent=True)], owner="w1")
    first, cursor = eng.read_events(tid, cursor=0)
    assert first and cursor > 0
    second, cursor2 = eng.read_events(tid, cursor=cursor)
    assert second == [] and cursor2 == cursor


# --- TW-665 fix round: Rosa REQUEST CHANGES regression pins -----------------


def _step_row(db, tid, idx):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return dict(
            c.execute(
                "SELECT * FROM durable_steps WHERE turn_id=? AND step_index=?",
                (tid, idx),
            ).fetchone()
        )


def test_flipped_declaration_on_resume_raises_and_never_reruns(tmp_path):
    """M1: the journaled declaration governs. A resumer who flips a
    crashed non-idempotent step to idempotent=True is refused, and the
    handler does NOT silently re-run (Rosa's probe)."""
    db = str(tmp_path / "turns.db")
    eng = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    calls = []

    def send_then_crash(ctx):
        calls.append("send")
        raise SimulatedCrash()

    steps = [StepDef("send", send_then_crash, idempotent=False)]
    with pytest.raises(SimulatedCrash):
        eng.run_turn(tid, steps, owner="w1")
    time.sleep(0.15)  # dead worker's lease expires
    eng2 = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    flipped = [
        StepDef("send", lambda ctx: calls.append("send-RERUN") or "ok", idempotent=True)
    ]
    with pytest.raises(JournalMismatch):
        eng2.run_turn(tid, flipped, owner="w2")
    assert calls == ["send"]


def test_renamed_step_on_resume_raises(tmp_path):
    """M1: a completed step's stored result must never be returned under
    a different supplied name; a rename on resume raises."""
    db = str(tmp_path / "turns.db")
    eng = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    calls = []

    def boom(ctx):
        calls.append("b")
        raise SimulatedCrash()

    steps = [
        StepDef("a", lambda ctx: calls.append("a") or "ra", idempotent=True),
        StepDef("b", boom, idempotent=True),
    ]
    with pytest.raises(SimulatedCrash):
        eng.run_turn(tid, steps, owner="w1")
    time.sleep(0.15)
    eng2 = DurableTurnEngine(db, lease_seconds=0.1, base_delay=0.001)
    renamed = [
        StepDef("a-renamed", lambda ctx: "ra", idempotent=True),
        StepDef("b", lambda ctx: calls.append("b2") or "rb", idempotent=True),
    ]
    with pytest.raises(JournalMismatch):
        eng2.run_turn(tid, renamed, owner="w2")
    assert calls == ["a", "b"]  # nothing re-ran, nothing proceeded


def test_non_idempotent_step_is_not_retried_in_run(tmp_path):
    """M2: a declared non-idempotent step gets exactly one attempt in a
    run, even when the failure classifies as transient (Rosa's probe ran
    the handler 3 times after its raw side effect)."""
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    calls = []

    def send(ctx):
        calls.append("send")
        raise ConnectionError("connection reset")  # transient class

    out = eng.run_turn(tid, [StepDef("send", send, idempotent=False)], owner="w1")
    assert out["status"] == "failed"
    assert calls == ["send"]


def test_concurrent_lease_acquire_has_exactly_one_winner(tmp_path):
    """M3: barrier-synchronized racing acquires (Rosa's probe: both
    workers won 13 of 30 trials before the CAS fix)."""
    db = str(tmp_path / "turns.db")
    for trial in range(30):
        eng = DurableTurnEngine(db, lease_seconds=60)
        tid = eng.submit(session_id="s", message_id=f"race-{trial}", prompt="x")
        barrier = threading.Barrier(2)
        outcomes = []

        def contender(owner):
            barrier.wait()
            try:
                eng.acquire_lease(tid, owner=owner)
                outcomes.append(owner)
            except TurnFenced:
                outcomes.append(None)

        threads = [
            threading.Thread(target=contender, args=("w1",)),
            threading.Thread(target=contender, args=("w2",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [o for o in outcomes if o]
        assert len(winners) == 1, f"trial {trial}: winners={outcomes}"


def test_worker_whose_lease_was_taken_over_stops_before_next_step(tmp_path):
    """M3: ownership is revalidated during a run; a zombie whose lease
    was taken over mid-run is fenced before it executes another step."""
    db = str(tmp_path / "turns.db")
    eng = DurableTurnEngine(db, lease_seconds=60, base_delay=0.001)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    calls = []

    def steal_lease(ctx):
        calls.append("a")
        with sqlite3.connect(db) as c:
            c.execute(
                "UPDATE durable_turns SET owner='w2', lease_expires_at=?"
                " WHERE turn_id=?",
                (time.time() + 60, tid),
            )
        return "ra"

    steps = [
        StepDef("a", steal_lease, idempotent=True),
        StepDef("b", lambda ctx: calls.append("b") or "rb", idempotent=True),
    ]
    with pytest.raises(TurnFenced):
        eng.run_turn(tid, steps, owner="w1")
    assert calls == ["a"]  # step b never executed under the stolen lease


def test_attempts_counter_counts_every_handler_run(tmp_path):
    """m1: durable_steps.attempts must state reality (it stayed 0 while
    a handler ran 3 times before the fix)."""
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    attempts = {"n": 0}

    def flaky(ctx):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("connection reset")
        return "healed"

    out = eng.run_turn(tid, [StepDef("flaky", flaky, idempotent=True)], owner="w1")
    assert out["status"] == "completed"
    row = _step_row(str(tmp_path / "turns.db"), tid, 0)
    assert row["attempts"] == 3


def test_rerun_of_completed_turn_is_a_noop(tmp_path):
    """m2: re-running a completed turn returns the stored result without
    journaling a phantom second turn_started / turn_completed pair."""
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    calls = []
    steps = [StepDef("a", lambda ctx: calls.append("a") or "ra", idempotent=True)]
    out1 = eng.run_turn(tid, steps, owner="w1")
    events_before, _ = eng.read_events(tid)
    out2 = eng.run_turn(tid, steps, owner="w1")
    events_after, _ = eng.read_events(tid)
    assert out2["status"] == "completed"
    assert out2["results"] == out1["results"] == ["ra"]
    assert len(events_after) == len(events_before)
    assert calls == ["a"]  # handler not re-run


def test_duplicate_submit_journals_one_prompt_saved(tmp_path):
    """m3: a retried submit reaches the same turn but does not append a
    duplicate prompt_saved event."""
    eng = make_engine(tmp_path)
    tid = eng.submit(session_id="s1", message_id="m1", prompt="x")
    assert eng.submit(session_id="s1", message_id="m1", prompt="x") == tid
    assert eng.submit(session_id="s1", message_id="m1", prompt="x") == tid
    events, _ = eng.read_events(tid)
    assert [e["kind"] for e in events].count("prompt_saved") == 1


def test_step_declaration_must_be_a_real_bool():
    """n2: truthy non-bool declarations are not declarations."""
    for bad in (1, 0, "yes"):
        with pytest.raises(ValueError):
            StepDef("x", lambda ctx: None, idempotent=bad)
