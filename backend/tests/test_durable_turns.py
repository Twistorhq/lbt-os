"""TW-665: durable agent turn engine, pattern-mined from vercel-labs/fx0.

Pattern source: fx0 / libfx (Apache-2.0). Adopted: persist-before-execute,
per-step journal, resume-after-crash, idempotency declaration per tool,
single-worker fencing, 64-step turn cap, retries via TW-208 self-healing.
NOT adopted: Vercel Workflow World, Vercel Queues, AI Gateway (vendor paths).

A SimulatedCrash is a BaseException on purpose: a real process death is not
an Exception the engine can catch, so recovery must work from the journal
alone, with a brand-new engine instance on the same store file.
"""

import time

import pytest

from app.durable_turns import (
    DurableTurnEngine,
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
