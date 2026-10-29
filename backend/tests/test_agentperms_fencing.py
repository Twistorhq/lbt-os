"""TW-768 — fencing: revocation stops in-flight and queued work.

Exercises the REAL durable-turns engine (app.durable_turns) with
steps wrapped by gate.guarded_step:

- a turn mid-run whose next step's class is revoked during the run
  does not execute that step's handler — the turn fails at the
  guarded step and the journal shows it;
- a turn submitted after a revoke-all never executes its first
  step, and intake refuses the work outright.

The ledger runs against the same stateful fake as the service
tests (duplicated here per house style: test files own their fakes).
"""

import pytest

from app.agentperms import gate, models, service
from app.durable_turns.engine import DurableTurnEngine, StepDef

ORG = "org-1"
OWNER = "owner-1"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table, mode, payload=None):
        self._db = db
        self._table = table
        self._mode = mode
        self._payload = payload
        self._filters = []
        self._order = None
        self._limit = None

    def eq(self, col, value):
        self._filters.append((col, value))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matching(self):
        rows = self._db.tables.get(self._table, [])
        return [
            r for r in rows if all(r.get(col) == value for col, value in self._filters)
        ]

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", self._db.next_id(self._table))
            self._db.tables.setdefault(self._table, []).append(row)
            return _Result([dict(row)])
        if self._mode == "update":
            updated = []
            for row in self._matching():
                row.update(self._payload)
                updated.append(dict(row))
            return _Result(updated)
        rows = [dict(r) for r in self._matching()]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class _Table:
    def __init__(self, db, name):
        self._db = db
        self._name = name

    def select(self, _cols="*"):
        return _Query(self._db, self._name, "select")

    def insert(self, row):
        return _Query(self._db, self._name, "insert", row)

    def update(self, payload):
        return _Query(self._db, self._name, "update", payload)


class FakeDb:
    def __init__(self):
        self.tables = {}
        self._counters = {}

    def next_id(self, table):
        self._counters[table] = self._counters.get(table, 0) + 1
        return f"{table}-{self._counters[table]}"

    def table(self, name):
        return _Table(self, name)


@pytest.fixture
def db():
    fake = FakeDb()
    service.grant_permission(fake, ORG, "email.send", actor=OWNER)
    service.grant_permission(fake, ORG, "sms.send", actor=OWNER)
    return fake


@pytest.fixture
def engine(tmp_path):
    return DurableTurnEngine(str(tmp_path / "turns.db"))


class TestMidRunRevocation:
    def test_revoked_class_stops_next_step(self, db, engine):
        executed = []

        def first(ctx):
            executed.append("first")
            # The owner revokes sms.send while this turn is mid-run.
            service.revoke(
                db,
                ORG,
                models.RevokeStatement(strength="class", target="sms.send"),
                actor=OWNER,
            )
            return "email sent"

        def second(ctx):
            executed.append("second")
            return "text sent"

        steps = [
            StepDef("send_email", first, idempotent=False),
            gate.guarded_step(
                db, ORG, "sms.send", StepDef("send_text", second, idempotent=True)
            ),
        ]
        turn_id = engine.submit(session_id="s1", message_id="m1", prompt="follow up")
        result = engine.run_turn(turn_id, steps)

        assert executed == ["first"]
        assert result["status"] == "failed"
        assert "revoked" in result["error"]
        events, _ = engine.read_events(turn_id)
        kinds = [e["kind"] for e in events]
        assert "step_completed" in kinds
        failed = [e for e in events if e["kind"] == "turn_failed"]
        assert failed and failed[0]["payload"]["step"] == "send_text"

    def test_granted_class_runs_both_steps(self, db, engine):
        executed = []

        def make(name):
            def handler(ctx):
                executed.append(name)
                return name

            return handler

        steps = [
            gate.guarded_step(
                db, ORG, "email.send", StepDef("send_email", make("a"), idempotent=True)
            ),
            gate.guarded_step(
                db, ORG, "sms.send", StepDef("send_text", make("b"), idempotent=True)
            ),
        ]
        turn_id = engine.submit(session_id="s1", message_id="m2", prompt="follow up")
        result = engine.run_turn(turn_id, steps)
        assert result["status"] == "completed"
        assert executed == ["a", "b"]

    def test_cap_exceeded_step_never_executes(self, db, engine):
        service.grant_permission(db, ORG, "purchase", actor=OWNER, cap_cents=1000)
        executed = []

        def buy(ctx):
            executed.append("buy")
            return "bought"

        steps = [
            gate.guarded_step(
                db,
                ORG,
                "purchase",
                StepDef("buy_part", buy, idempotent=False),
                amount_cents=1001,
            )
        ]
        turn_id = engine.submit(session_id="s1", message_id="m3", prompt="buy")
        result = engine.run_turn(turn_id, steps)
        assert executed == []
        assert result["status"] == "failed"
        assert "cap_exceeded" in result["error"]


class TestQueuedHalt:
    def test_turn_submitted_after_revoke_all_never_starts(self, db, engine):
        service.revoke(db, ORG, models.RevokeStatement(strength="all"), actor=OWNER)
        with pytest.raises(service.AgentWorkRefused):
            service.assert_accepting_work(db, ORG)

        executed = []

        def handler(ctx):
            executed.append("ran")
            return "ran"

        steps = [
            gate.guarded_step(
                db, ORG, "email.send", StepDef("send_email", handler, idempotent=True)
            )
        ]
        turn_id = engine.submit(session_id="s1", message_id="m4", prompt="queued")
        result = engine.run_turn(turn_id, steps)
        assert executed == []
        assert result["status"] == "failed"
