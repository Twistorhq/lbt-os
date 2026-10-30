"""TW-800 Twistor Trades agent tool gateway - service behavior.

Runs against a stateful in-memory Supabase stand-in shared with the
TW-768 ledger, so gateway + ledger composition is exercised as real
state transitions: grants written through agentperms' own service,
revocations landing mid-approval, caps enforced by the ledger and
only the ledger.

The behaviors pinned here:

- every call writes its audit row before it can execute (no audit,
  no execution), and the chain verifies,
- policy deny / unknown tool / unresolvable binding refuse before
  the executor is ever touched,
- a bound call lives and dies by the ledger: no grant, explicit
  deny, over cap,
- ask parks the call; approval executes with the stored args;
  denial and expiry never execute; a revocation landing while the
  call waits fences it at execution time,
- an executor exception is a 'failed' outcome, never a crash.
"""

import pytest

from app.agentperms import service as ledger_service
from app.toolgateway import service
from app.toolgateway.models import GatewayError, ToolRule

ORG = "org-1"
OTHER_ORG = "org-2"
OWNER = "owner-1"
AGENT = "agent-1"


# -- stateful fake Supabase client ---------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table, mode, payload=None, on_conflict=None):
        self._db = db
        self._table = table
        self._mode = mode
        self._payload = payload
        self._on_conflict = on_conflict
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
        if self._table in self._db.fail_on_insert and self._mode == "insert":
            raise RuntimeError("audit store is down")
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", self._db.next_id(self._table))
            row.setdefault("created_at", self._db.next_ts())
            self._db.tables.setdefault(self._table, []).append(row)
            return _Result([dict(row)])
        if self._mode == "update":
            updated = []
            for row in self._matching():
                row.update(self._payload)
                updated.append(dict(row))
            return _Result(updated)
        if self._mode == "upsert":
            rows = self._db.tables.setdefault(self._table, [])
            keys = (
                self._on_conflict.split(",")
                if self._on_conflict
                else (["id"] if "id" in self._payload else [])
            )
            for row in rows:
                if keys and all(row.get(k) == self._payload.get(k) for k in keys):
                    row.update(self._payload)
                    return _Result([dict(row)])
            row = dict(self._payload)
            row.setdefault("id", self._db.next_id(self._table))
            row.setdefault("created_at", self._db.next_ts())
            rows.append(row)
            return _Result([dict(row)])
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

    def upsert(self, payload, on_conflict=None):
        return _Query(self._db, self._name, "upsert", payload, on_conflict)


class FakeDb:
    def __init__(self):
        self.tables = {}
        self.fail_on_insert = set()
        self._counters = {}
        self._ticks = 0

    def next_id(self, table):
        self._counters[table] = self._counters.get(table, 0) + 1
        return f"{table}-{self._counters[table]}"

    def next_ts(self):
        self._ticks += 1
        return f"2026-10-30T00:00:{self._ticks:02d}Z"

    def table(self, name):
        return _Table(self, name)


@pytest.fixture
def db():
    return FakeDb()


# -- helpers ---------------------------------------------------------------


class Recorder:
    """An executor that records what it was asked to run."""

    def __init__(self, result=None, raises=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}
        self.raises = raises

    def __call__(self, tool, args):
        self.calls.append((tool, dict(args)))
        if self.raises is not None:
            raise self.raises
        return self.result


def audit_kinds(db, org=ORG):
    rows = service.list_calls(db, org, limit=1000)
    return [r["kind"] for r in reversed(rows)]


def grant(db, action_class, **kw):
    return ledger_service.grant_permission(db, ORG, action_class, actor=OWNER, **kw)


# -- allow path --------------------------------------------------------------


class TestAllowPath:
    def test_read_tool_executes_and_audits(self, db):
        ex = Recorder(result={"workflows": []})
        out = service.invoke(db, ORG, "flows.list", {}, actor=AGENT, executor=ex)
        assert out.status == "executed"
        assert out.result == {"workflows": []}
        assert ex.calls == [("flows.list", {})]
        assert audit_kinds(db) == ["requested", "executed"]
        assert service.verify_chain(db, ORG)["ok"] is True

    def test_bound_call_executes_under_a_grant(self, db):
        saved = grant(db, "email.send")
        ex = Recorder(result={"id": "msg-1"})
        out = service.invoke(
            db,
            ORG,
            "notify.send",
            {"channel": "email", "to": "a@b.co", "body": "hi"},
            actor=AGENT,
            executor=ex,
        )
        assert out.status == "executed"
        assert out.action_class == "email.send"
        assert out.channel == "email"
        assert out.grant_id == saved["id"]
        rows = service.list_calls(db, ORG)
        executed = [r for r in rows if r["kind"] == "executed"][0]
        assert executed["grant_id"] == saved["id"]
        # The audit row fingerprints the args; it does not store them.
        assert "a@b.co" not in str(executed)
        assert executed["args_keys"] == ["body", "channel", "to"]


# -- refuse paths --------------------------------------------------------------


class TestRefusals:
    def test_unknown_tool_denied_before_executor(self, db):
        ex = Recorder()
        out = service.invoke(db, ORG, "shell.exec", {}, actor=AGENT, executor=ex)
        assert out.status == "denied" and out.reason == "unknown_tool"
        assert ex.calls == []
        assert audit_kinds(db) == ["requested", "denied"]

    def test_bound_call_without_grant_denied(self, db):
        ex = Recorder()
        out = service.invoke(
            db,
            ORG,
            "notify.send",
            {"channel": "email", "to": "a@b.co"},
            actor=AGENT,
            executor=ex,
        )
        assert out.status == "denied" and out.reason == "no_grant"
        assert ex.calls == []

    def test_bound_call_with_explicit_deny_denied(self, db):
        ledger_service.deny_permission(db, ORG, "sms.send", actor=OWNER)
        ex = Recorder()
        out = service.invoke(
            db,
            ORG,
            "notify.send",
            {"channel": "sms", "to": "+13035550100"},
            actor=AGENT,
            executor=ex,
        )
        assert out.status == "denied" and out.reason == "denied"
        assert ex.calls == []

    def test_unresolvable_binding_denied(self, db):
        ex = Recorder()
        out = service.invoke(
            db,
            ORG,
            "notify.send",
            {"channel": "pigeon", "to": "coop"},
            actor=AGENT,
            executor=ex,
        )
        assert out.status == "denied" and out.reason == "unresolvable_binding"
        assert ex.calls == []

    def test_money_cap_is_the_ledgers_call(self, db):
        grant(db, "payment.charge", cap_cents=500)
        rules = (
            ToolRule(tool="pay.charge", mode="allow", action_class="payment.charge"),
        )
        ex = Recorder()
        over = service.invoke(
            db,
            ORG,
            "pay.charge",
            {},
            actor=AGENT,
            amount_cents=600,
            executor=ex,
            rules=rules,
        )
        assert over.status == "denied" and over.reason == "cap_exceeded"
        under = service.invoke(
            db,
            ORG,
            "pay.charge",
            {},
            actor=AGENT,
            amount_cents=400,
            executor=ex,
            rules=rules,
        )
        assert under.status == "executed"
        assert len(ex.calls) == 1

    def test_audit_store_down_refuses_the_call(self, db):
        db.fail_on_insert.add("agt_tool_calls")
        ex = Recorder()
        out = service.invoke(db, ORG, "flows.list", {}, actor=AGENT, executor=ex)
        assert out.status == "denied" and out.reason == "audit_unavailable"
        assert ex.calls == []

    def test_executor_failure_is_an_outcome_not_a_crash(self, db):
        ex = Recorder(raises=RuntimeError("upstream exploded"))
        out = service.invoke(db, ORG, "flows.list", {}, actor=AGENT, executor=ex)
        assert out.status == "failed" and out.reason == "execution_failed"
        assert audit_kinds(db) == ["requested", "failed"]
        failed = [r for r in service.list_calls(db, ORG) if r["kind"] == "failed"][0]
        assert failed["detail"] == {"error_type": "RuntimeError"}


# -- ask path ------------------------------------------------------------------


class TestAskPath:
    def _park(self, db, ex):
        return service.invoke(
            db,
            ORG,
            "flows.trigger",
            {"workflow_id": "w1", "input": {"x": 1}},
            actor=AGENT,
            executor=ex,
        )

    def test_ask_parks_without_executing(self, db):
        ex = Recorder()
        out = self._park(db, ex)
        assert out.status == "pending_approval"
        assert out.approval_id is not None
        assert ex.calls == []
        assert audit_kinds(db) == ["requested", "approval_requested"]
        pending = service.list_approvals(db, ORG)
        assert [p["id"] for p in pending] == [out.approval_id]

    def test_approval_executes_with_stored_args(self, db):
        ex = Recorder(result={"run_id": "r1"})
        out = self._park(db, ex)
        done = service.decide(
            db, ORG, out.approval_id, actor=OWNER, approve=True, executor=ex
        )
        assert done.status == "executed"
        assert done.approval_id == out.approval_id
        assert ex.calls == [("flows.trigger", {"workflow_id": "w1", "input": {"x": 1}})]
        assert audit_kinds(db) == [
            "requested",
            "approval_requested",
            "approved",
            "executed",
        ]
        assert service.list_approvals(db, ORG) == []
        assert service.verify_chain(db, ORG)["ok"] is True

    def test_denial_never_executes(self, db):
        ex = Recorder()
        out = self._park(db, ex)
        done = service.decide(
            db, ORG, out.approval_id, actor=OWNER, approve=False, executor=ex
        )
        assert done.status == "denied" and done.reason == "approval_denied"
        assert ex.calls == []
        assert audit_kinds(db) == ["requested", "approval_requested", "approval_denied"]

    def test_expired_approval_never_executes(self, db):
        ex = Recorder()
        out = self._park(db, ex)
        row = db.tables["agt_tool_approvals"][0]
        row["expires_at"] = "2020-01-01T00:00:00Z"
        done = service.decide(
            db, ORG, out.approval_id, actor=OWNER, approve=True, executor=ex
        )
        assert done.status == "denied" and done.reason == "approval_expired"
        assert ex.calls == []
        assert row["status"] == "expired"

    def test_decide_twice_is_a_caller_error(self, db):
        ex = Recorder()
        out = self._park(db, ex)
        service.decide(db, ORG, out.approval_id, actor=OWNER, approve=False)
        with pytest.raises(GatewayError):
            service.decide(db, ORG, out.approval_id, actor=OWNER, approve=True)

    def test_decide_missing_approval_is_a_caller_error(self, db):
        with pytest.raises(GatewayError):
            service.decide(db, ORG, "nope", actor=OWNER, approve=True)

    def test_revocation_landing_mid_wait_fences_execution(self, db):
        rules = (
            ToolRule(
                tool="notify.send",
                mode="ask",
                class_by_arg="channel",
                class_map={"email": "email.send"},
            ),
        )
        grant(db, "email.send")
        ex = Recorder()
        out = service.invoke(
            db,
            ORG,
            "notify.send",
            {"channel": "email", "to": "a@b.co"},
            actor=AGENT,
            executor=ex,
            rules=rules,
        )
        assert out.status == "pending_approval"
        # The owner changes their mind about the class entirely
        # while the call waits: execution must fence, not proceed.
        ledger_service.deny_permission(db, ORG, "email.send", actor=OWNER)
        done = service.decide(
            db, ORG, out.approval_id, actor=OWNER, approve=True, executor=ex
        )
        assert done.status == "denied" and done.reason == "denied"
        assert ex.calls == []
        row = db.tables["agt_tool_approvals"][0]
        assert row["status"] == "fenced"


# -- audit chain -----------------------------------------------------------------


class TestAuditChain:
    def test_tampered_row_breaks_verification(self, db):
        ex = Recorder()
        service.invoke(db, ORG, "flows.list", {}, actor=AGENT, executor=ex)
        assert service.verify_chain(db, ORG)["ok"] is True
        db.tables["agt_tool_calls"][0]["actor"] = "mallory"
        verdict = service.verify_chain(db, ORG)
        assert verdict["ok"] is False
        assert verdict["broken_seq"] == 1

    def test_deleted_row_breaks_verification(self, db):
        ex = Recorder()
        service.invoke(db, ORG, "flows.list", {}, actor=AGENT, executor=ex)
        service.invoke(db, ORG, "flows.list", {}, actor=AGENT, executor=ex)
        del db.tables["agt_tool_calls"][1]
        assert service.verify_chain(db, ORG)["ok"] is False

    def test_chains_are_per_org(self, db):
        ex = Recorder()
        service.invoke(db, ORG, "flows.list", {}, actor=AGENT, executor=ex)
        service.invoke(db, OTHER_ORG, "flows.list", {}, actor=AGENT, executor=ex)
        assert service.verify_chain(db, ORG) == {
            "ok": True,
            "rows": 2,
            "broken_seq": None,
        }
        assert service.verify_chain(db, OTHER_ORG)["ok"] is True
        assert service.list_calls(db, OTHER_ORG)[0]["org_id"] == OTHER_ORG

    def test_approvals_are_org_scoped(self, db):
        ex = Recorder()
        out = service.invoke(
            db, ORG, "flows.trigger", {"workflow_id": "w1"}, actor=AGENT, executor=ex
        )
        with pytest.raises(GatewayError):
            service.decide(db, OTHER_ORG, out.approval_id, actor=OWNER, approve=True)


# -- immediate use: the default executor, end to end -------------------------


class TestImmediateUseDefaultExecutor:
    """TW-800's ADD MEANS USE proof, living in the suite: no injected
    executor - the gateway's shipped default executor runs a real
    workflow through the real Flows stack (TW-625) after an owner's
    approval, and the whole thing lands in the audit chain."""

    def _seed_workflow(self, db):
        db.table("flw_workflows").insert(
            {
                "id": "w1",
                "org_id": ORG,
                "name": "gw-demo",
                "status": "active",
                "alert_to": None,
                "paused_reason": None,
                "definition": {
                    "name": "gw-demo",
                    "trigger": {"type": "manual"},
                    "steps": [
                        {"id": "s1", "type": "set", "var": "greeted", "value": True}
                    ],
                },
            }
        ).execute()

    def test_trigger_asks_then_owner_approval_runs_the_workflow(self, db):
        self._seed_workflow(db)
        out = service.invoke(
            db, ORG, "flows.trigger", {"workflow_id": "w1"}, actor=AGENT
        )
        assert out.status == "pending_approval"
        # Nothing ran yet: no run rows exist before the owner decides.
        assert db.tables.get("flw_runs", []) == []

        done = service.decide(db, ORG, out.approval_id, actor=OWNER, approve=True)
        assert done.status == "executed"
        assert done.result is not None and done.result.get("run_id")
        runs = db.tables.get("flw_runs", [])
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert audit_kinds(db) == [
            "requested",
            "approval_requested",
            "approved",
            "executed",
        ]
        assert service.verify_chain(db, ORG)["ok"] is True

    def test_read_tool_runs_through_the_default_executor(self, db):
        self._seed_workflow(db)
        out = service.invoke(db, ORG, "flows.list", {}, actor=AGENT)
        assert out.status == "executed"
        names = [w["name"] for w in out.result["workflows"]]
        assert names == ["gw-demo"]
