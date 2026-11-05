"""TW-953 (TW-948-B5): preflight, durable fire identity,
interruption recovery, exactly-once reconciliation.

Acceptance per build map Section 4 B5 + spec Section 8.5, plus
the inherited TW-952 review scope: Rex F5 (router wiring), F6
(toll reconciliation), F7 tail (durable tick heartbeat), F8
(catch-up collapsed-slot accounting), and the Rex P14 / Rosa nit
(tick-summary double-listing). Runs against the same stateful
in-memory Supabase stand-in as the B4 suite, plus fake seams for
credentials, skills, delivery, ledger, alerts, and tolls. The B3
agent_tolls helpers are NOT on this base; the fake sink mirrors
the B1 record_toll_event seam contract with post-B3 vocabulary.
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("TWISTOR_PERSISTENCE_HASH_KEY", "tw953-test-key")

from app.agent_jobs import (  # noqa: E402  # noqa: E402
    execution,
    heartbeat,
    preflight,
    reconciliation,
    recovery,
    service,
)
from app.agent_jobs import ticker as ticker_mod
from app.agent_jobs.models import AgentJobError, JobSpec  # noqa: E402
from app.persistence import models as pmodels  # noqa: E402
from app.persistence import service as psvc  # noqa: E402

ORG = "org-1"
AGENT = "agent-1"
UTC = timezone.utc


def dt(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# -- fake db (house pattern, extended with update/select hooks) --


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
        self._filters.append((col, value, "eq"))
        return self

    def in_(self, col, values):
        self._filters.append((col, list(values), "in"))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matching(self):
        rows = self._db.tables.get(self._table, [])

        def _ok(row):
            for col, value, op in self._filters:
                if op == "in":
                    if row.get(col) not in value:
                        return False
                elif row.get(col) != value:
                    return False
            return True

        return [r for r in rows if _ok(r)]

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            hook = getattr(self._db, "insert_hook", None)
            if hook is not None:
                hook(self._table, row)
            row.setdefault("id", self._db.next_id(self._table))
            self._db.tables.setdefault(self._table, []).append(row)
            return _Result([dict(row)])
        if self._mode == "update":
            hook = getattr(self._db, "update_hook", None)
            if hook is not None:
                hook(self._table, self._payload)
            updated = []
            for row in self._matching():
                row.update(self._payload)
                updated.append(dict(row))
            return _Result(updated)
        hook = getattr(self._db, "select_hook", None)
        if hook is not None:
            hook(self._table)
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
        self.insert_hook = None
        self.update_hook = None
        self.select_hook = None

    def next_id(self, table):
        self._counters[table] = self._counters.get(table, 0) + 1
        return f"{table}-{self._counters[table]}"

    def table(self, name):
        return _Table(self, name)


class FakeTollSink:
    """The B1 toll seam contract, with the post-B3 vocabulary."""

    KNOWN = {"agent.task_completed", "agent.email_action"}

    def __init__(self, fail_keys=()):
        self.rows = {}
        self.calls = 0
        self.fail_keys = set(fail_keys)

    def __call__(
        self,
        db,
        org_id,
        event_type,
        quantity=1,
        idempotency_key=None,
        source_type=None,
        source_id=None,
        amount_cents=None,
    ):
        self.calls += 1
        if event_type not in self.KNOWN:
            raise ValueError(f"Unknown toll event type: {event_type}")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if idempotency_key in self.fail_keys:
            raise RuntimeError("toll sink down")
        if idempotency_key in self.rows:
            return self.rows[idempotency_key]
        row = {
            "org_id": org_id,
            "event_type": event_type,
            "idempotency_key": idempotency_key,
            "source_type": source_type,
            "source_id": source_id,
        }
        self.rows[idempotency_key] = row
        return row

    def reader(self, db, org_id, fire_id):
        return [r for r in self.rows.values() if r["source_id"] == fire_id]


class FakeAlerter:
    def __init__(self):
        self.calls = []

    def __call__(self, db, org_id, job, reason):
        self.calls.append({"org_id": org_id, "job_id": job["id"], "reason": reason})
        return f"alert-{len(self.calls)}"


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def tolls():
    return FakeTollSink()


@pytest.fixture
def alerter():
    return FakeAlerter()


def _spec(**kwargs):
    base = dict(
        name="morning-scan",
        prompt="Scan the board and surface new leads.",
        skill_keys=["leads.scan"],
        cron_expr="0 9 * * *",
        timezone="America/Denver",
    )
    base.update(kwargs)
    return JobSpec(**base)


def _job(db, now, **kwargs):
    return service.create_job(db, ORG, AGENT, _spec(**kwargs), now=now)


def _echo_factory(surfaced=(), summary="done"):
    def factory(ctx):
        def run(_ctx):
            return {"summary": summary, "surfaced_item_ids": list(surfaced)}

        return [execution.JobStep(name="work", handler=run)]

    return factory


def _tick(db, now, factory=None, tolls=None, **kwargs):
    kwargs.setdefault("enabled_orgs", {ORG})
    return ticker_mod.tick(
        db,
        now=now,
        executor_factory=factory or _echo_factory(),
        toll_sink=tolls,
        **kwargs,
    )


def _good_checker(**overrides):
    seams = dict(
        credential_resolver=lambda ref: {"token": f"tok-{ref}"},
        skill_checker=lambda key: {"ready": True, "licensed": True, "active": True},
        delivery_checker=lambda db, org_id, job: True,
        ledger_checker=lambda db, org_id, agent_id, cls: True,
    )
    seams.update(overrides)
    return preflight.PreflightChecker(**seams)


# -- preflight: definition validation ----------------------------


class TestDefinitionValidation:
    def test_healthy_job_has_no_failures(self, db):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        assert preflight.validate_definition(job) == []

    def test_bad_cron_and_bad_timezone_are_failures(self, db):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        job["cron_expr"] = "not-a-cron"
        job["timezone"] = "Mars/Olympus"
        failures = preflight.validate_definition(job)
        assert len(failures) == 2

    def test_corrupt_next_fire_at_is_a_failure(self, db):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        job["next_fire_at"] = "not-a-date"
        failures = preflight.validate_definition(job)
        assert any("next_fire_at" in f for f in failures)

    def test_empty_prompt_is_a_failure(self, db):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        job["prompt"] = "   "
        assert preflight.validate_definition(job)


# -- preflight: external checks -----------------------------------


class TestPreflightChecks:
    def _rich_job(self, db, **kwargs):
        kwargs.setdefault("credential_refs", ["crm-token"])
        kwargs.setdefault("action_classes", ["email.send"])
        return _job(db, dt(2026, 11, 5, 15, 0), **kwargs)

    def test_healthy_job_passes_all_checks(self, db):
        job = self._rich_job(db)
        result = _good_checker().check(db, ORG, AGENT, job)
        assert result.ok and result.failures == []

    def test_unresolvable_credential_fails(self, db):
        job = self._rich_job(db)
        checker = _good_checker(credential_resolver=lambda ref: None)
        result = checker.check(db, ORG, AGENT, job)
        assert not result.ok
        assert any("credential" in f for f in result.failures)

    def test_credential_ref_without_resolver_fails_closed(self, db):
        job = self._rich_job(db)
        checker = _good_checker(credential_resolver=None)
        result = checker.check(db, ORG, AGENT, job)
        assert not result.ok
        assert any("credential" in f for f in result.failures)

    def test_unlicensed_skill_fails(self, db):
        job = self._rich_job(db)
        checker = _good_checker(
            skill_checker=lambda key: {
                "ready": True,
                "licensed": False,
                "active": True,
            }
        )
        result = checker.check(db, ORG, AGENT, job)
        assert not result.ok
        assert any("skill" in f for f in result.failures)

    def test_skill_without_registry_fails_closed(self, db):
        job = self._rich_job(db)
        checker = _good_checker(skill_checker=None)
        result = checker.check(db, ORG, AGENT, job)
        assert not result.ok
        assert any("skill" in f for f in result.failures)

    def test_delivery_target_not_configured_fails(self, db):
        job = self._rich_job(db)
        checker = _good_checker(delivery_checker=lambda db, org_id, job: False)
        result = checker.check(db, ORG, AGENT, job)
        assert not result.ok
        assert any("delivery" in f for f in result.failures)

    def test_revoked_grant_fails(self, db):
        job = self._rich_job(db)
        checker = _good_checker(ledger_checker=lambda db, org_id, agent_id, cls: False)
        result = checker.check(db, ORG, AGENT, job)
        assert not result.ok
        assert any("email.send" in f for f in result.failures)

    def test_phone_hook_reserved_without_hook(self, db):
        job = self._rich_job(db, action_classes=["call.outbound"])
        result = _good_checker().check(db, ORG, AGENT, job)
        assert result.ok
        assert any("B14" in n for n in result.notes)

    def test_phone_hook_failure_blocks(self, db):
        job = self._rich_job(db, action_classes=["call.outbound"])
        checker = _good_checker(
            phone_hook=lambda db, org_id, agent_id, job: ["list not scrubbed"]
        )
        result = checker.check(db, ORG, AGENT, job)
        assert not result.ok
        assert any("scrubbed" in f for f in result.failures)

    def test_job_spec_rejects_unknown_action_class(self, db):
        with pytest.raises(AgentJobError):
            _spec(action_classes=["email.teleport"])


# -- preflight: blocking behavior in the ticker -------------------


class TestPreflightBlocking:
    def test_failed_preflight_blocks_alerts_once_creates_no_task(
        self, db, tolls, alerter
    ):
        job = _job(
            db,
            dt(2026, 11, 5, 15, 0),
            credential_refs=["crm-token"],
            action_classes=["email.send"],
        )
        ran = []

        def factory(ctx):
            ran.append(ctx.fire_id)
            return [execution.JobStep(name="w", handler=lambda _c: {})]

        checker = _good_checker(credential_resolver=lambda ref: None)
        out = _tick(
            db,
            dt(2026, 11, 5, 16, 0),
            factory=factory,
            tolls=tolls,
            preflight=checker,
            alerter=alerter,
        )
        assert out["fired"] == []
        assert [b["job_id"] for b in out["blocked"]] == [job["id"]]
        assert ran == []  # no task created, no model spend
        assert db.tables.get("agt_job_fires", []) == []
        blocked = service.get_job(db, ORG, AGENT, job["id"])
        assert blocked["status"] == "blocked"
        assert blocked["blocked_alert_id"] == "alert-1"
        assert len(alerter.calls) == 1
        # A second tick sends NO second alert: the blocked job is
        # out of the active scan and block_job is idempotent.
        out2 = _tick(
            db,
            dt(2026, 11, 5, 16, 5),
            factory=factory,
            tolls=tolls,
            preflight=checker,
            alerter=alerter,
        )
        assert out2["blocked"] == []
        assert len(alerter.calls) == 1

    def test_poisoned_cron_quarantined_at_preflight_not_mid_tick(
        self, db, tolls, alerter
    ):
        bad = _job(db, dt(2026, 11, 5, 15, 0), name="poisoned")
        _job(db, dt(2026, 11, 5, 15, 0), name="healthy")
        for row in db.tables["agt_jobs"]:
            if row["id"] == bad["id"]:
                row["cron_expr"] = "not-a-cron"
        out = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls, alerter=alerter)
        assert len(out["fired"]) == 1
        assert [b["job_id"] for b in out["blocked"]] == [bad["id"]]
        assert service.get_job(db, ORG, AGENT, bad["id"])["status"] == "blocked"
        assert len(alerter.calls) == 1

    def test_preflight_scan_blocks_poisoned_job_before_it_is_due(self, db, alerter):
        bad = _job(db, dt(2026, 11, 5, 15, 0), name="poisoned-early")
        ok = _job(db, dt(2026, 11, 5, 15, 0), name="healthy")
        for row in db.tables["agt_jobs"]:
            if row["id"] == bad["id"]:
                row["timezone"] = "Mars/Olympus"
        result = preflight.preflight_scan(db, alerter=alerter)
        assert [b["job_id"] for b in result["blocked"]] == [bad["id"]]
        assert result["ok"] == 1
        assert service.get_job(db, ORG, AGENT, bad["id"])["status"] == "blocked"
        assert service.get_job(db, ORG, AGENT, ok["id"])["status"] == "active"

    def test_trigger_now_preflight_failure_blocks_and_raises(self, db, tolls, alerter):
        job = _job(
            db,
            dt(2026, 11, 5, 15, 0),
            credential_refs=["crm-token"],
        )
        checker = _good_checker(credential_resolver=lambda ref: None)
        with pytest.raises(AgentJobError):
            service.trigger_now(
                db,
                ORG,
                AGENT,
                job["id"],
                executor_factory=_echo_factory(),
                toll_sink=tolls,
                preflight=checker,
                alerter=alerter,
                now=dt(2026, 11, 5, 15, 30),
                enabled_orgs={ORG},
            )
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "blocked"
        assert db.tables.get("agt_job_fires", []) == []
        assert len(alerter.calls) == 1


# -- durable delivery handoff --------------------------------------


class TestDeliveryHandoff:
    def _fire_with_delivery(self, db, tolls, delivery):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        slot = dt(2026, 11, 5, 16, 0)
        return service.fire_job(
            db,
            ORG,
            AGENT,
            job,
            slot,
            executor_factory=_echo_factory(summary="chase sent"),
            toll_sink=tolls,
            delivery=delivery,
        )

    def test_completion_requires_durable_receipt(self, db, tolls):
        outcome = self._fire_with_delivery(db, tolls, lambda ctx, out: "rcpt-9")
        assert outcome["status"] == "completed"
        assert outcome["delivery_receipt"] == "rcpt-9"
        fire = db.tables["agt_job_fires"][0]
        assert fire["status"] == "completed"
        assert fire["delivery_receipt"] == "rcpt-9"

    def test_no_receipt_means_not_completed(self, db, tolls):
        outcome = self._fire_with_delivery(db, tolls, lambda ctx, out: None)
        assert outcome["status"] == "failed"
        fire = db.tables["agt_job_fires"][0]
        assert fire["status"] == "failed"
        assert "receipt" in (fire["error"] or "")

    def test_delivery_failure_fails_the_fire(self, db, tolls):
        def boom(ctx, out):
            raise RuntimeError("notify ledger down")

        outcome = self._fire_with_delivery(db, tolls, boom)
        assert outcome["status"] == "failed"
        fire = db.tables["agt_job_fires"][0]
        assert fire["status"] == "failed"


# -- reaper: stranded running fires ---------------------------------


def _stranded_fire(db, job, *, started_minutes_ago, now):
    slot = dt(2026, 11, 5, 16, 0)
    fire = psvc.record_fire(
        db,
        ORG,
        AGENT,
        job["id"],
        pmodels.FireCreate(
            fire_key=service.mint_fire_key(job["id"], slot),
            scheduled_for=slot.isoformat(),
        ),
    )
    started = now - timedelta(minutes=started_minutes_ago)
    service.update_fire(
        db,
        ORG,
        AGENT,
        fire["id"],
        from_statuses=("pending",),
        status="running",
        started_at=started.isoformat(),
    )
    return fire


class TestReaper:
    def test_stale_running_fire_is_interrupted_and_job_paused_for_review(
        self, db, alerter
    ):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(db, job, started_minutes_ago=120, now=now)
        result = recovery.reap_stranded_fires(
            db, now=now, stale_after=timedelta(minutes=30), alerter=alerter
        )
        assert [r["fire_id"] for r in result["reaped"]] == [fire["id"]]
        row = service.get_fire(db, ORG, AGENT, fire["id"])
        assert row["status"] == "interrupted"
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "review"
        assert len(alerter.calls) == 1

    def test_fresh_running_fire_is_untouched(self, db):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(db, job, started_minutes_ago=5, now=now)
        result = recovery.reap_stranded_fires(
            db, now=now, stale_after=timedelta(minutes=30)
        )
        assert result["reaped"] == []
        assert service.get_fire(db, ORG, AGENT, fire["id"])["status"] == "running"
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "active"

    def test_reaper_is_idempotent(self, db, alerter):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        _stranded_fire(db, job, started_minutes_ago=120, now=now)
        recovery.reap_stranded_fires(
            db, now=now, stale_after=timedelta(minutes=30), alerter=alerter
        )
        second = recovery.reap_stranded_fires(
            db, now=now, stale_after=timedelta(minutes=30), alerter=alerter
        )
        assert second["reaped"] == []
        assert len(alerter.calls) == 1  # no second interruption alert

    def test_interrupted_job_never_auto_resumes_on_tick(self, db, tolls):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        _stranded_fire(db, job, started_minutes_ago=120, now=now)
        recovery.reap_stranded_fires(db, now=now, stale_after=timedelta(minutes=30))
        out = _tick(db, dt(2026, 11, 6, 16, 0), tolls=tolls)
        assert out["fired"] == []
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "review"

    def test_tick_can_reap_before_firing(self, db, tolls):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(db, job, started_minutes_ago=120, now=now)
        out = _tick(db, now, tolls=tolls, reap_stale_after=timedelta(minutes=30))
        assert [r["fire_id"] for r in out["reaped"]] == [fire["id"]]


# -- reconciliation: exactly-once -----------------------------------


class TestReconciliation:
    def _fire_failed_on_toll(self, db):
        """A fire whose step executed but whose toll post threw
        (Rex F6): journal says executed, toll ledger says nothing."""
        job = _job(db, dt(2026, 11, 5, 15, 0))
        slot = dt(2026, 11, 5, 16, 0)
        fire_key = service.mint_fire_key(job["id"], slot)
        sink = FakeTollSink(fail_keys={f"{fire_key}:step:chase"})
        sent = []

        def factory(ctx):
            def chase(_ctx):
                sent.append("chase-1")
                return {"summary": "chased"}

            def later(_ctx):
                sent.append("later")
                return {}

            return [
                execution.JobStep(
                    name="chase", handler=chase, toll_event="agent.email_action"
                ),
                execution.JobStep(name="later", handler=later),
            ]

        outcome = service.fire_job(
            db,
            ORG,
            AGENT,
            job,
            slot,
            executor_factory=factory,
            toll_sink=sink,
        )
        return job, outcome, sink, sent, factory

    def test_executed_but_untolled_is_detected_and_reconciled_once(self, db):
        job, outcome, sink, sent, factory = self._fire_failed_on_toll(db)
        assert outcome["status"] == "failed"
        assert sent == ["chase-1"]  # later step never ran (fail-fast)
        fire = db.tables["agt_job_fires"][0]
        report = reconciliation.reconcile_fire(
            db,
            ORG,
            AGENT,
            fire["id"],
            executor_factory=factory,
            toll_sink=sink,
            toll_reader=sink.reader,
        )
        by_name = {s["name"]: s["classification"] for s in report["steps"]}
        assert by_name == {"chase": "executed_untolled", "later": "not_done"}
        assert sink.rows == {}  # dry run posts nothing
        # Applying against the still-down sink reconciles nothing
        # and loses nothing: the step stays executed_untolled.
        applied = reconciliation.reconcile_fire(
            db,
            ORG,
            AGENT,
            fire["id"],
            executor_factory=factory,
            toll_sink=sink,
            toll_reader=sink.reader,
            apply=True,
        )
        assert applied["reconciled"] == []
        by_name = {s["name"]: s["classification"] for s in applied["steps"]}
        assert by_name["chase"] == "executed_untolled"

    def test_reconcile_apply_posts_toll_exactly_once(self, db):
        job, outcome, sink, sent, factory = self._fire_failed_on_toll(db)
        fire = db.tables["agt_job_fires"][0]
        healthy = FakeTollSink()
        # Share the ledger: the healthy sink writes the rows the
        # reader sees, standing in for the recovered toll service
        # writing the same toll_events table.
        shared_reader = lambda db_, org_id, fire_id: list(  # noqa: E731
            healthy.rows.values()
        )
        first = reconciliation.reconcile_fire(
            db,
            ORG,
            AGENT,
            fire["id"],
            executor_factory=factory,
            toll_sink=healthy,
            toll_reader=shared_reader,
            apply=True,
        )
        assert first["reconciled"] == ["chase"]
        assert len(healthy.rows) == 1
        key = f"{fire['fire_key']}:step:chase"
        assert key in healthy.rows
        second = reconciliation.reconcile_fire(
            db,
            ORG,
            AGENT,
            fire["id"],
            executor_factory=factory,
            toll_sink=healthy,
            toll_reader=shared_reader,
            apply=True,
        )
        by_name = {s["name"]: s["classification"] for s in second["steps"]}
        assert by_name["chase"] == "done"
        assert len(healthy.rows) == 1  # never double-tolled
        assert sent == ["chase-1"]  # the step itself never re-ran

    def test_kill_between_send_and_journal_is_unknown_never_retried(self, db):
        """Acceptance scenario: the worker dies after the external
        send but before the journal write lands. Reconciliation
        must classify the step unknown, surface it, and a resume
        must be refused: the send happened exactly once."""
        job = _job(db, dt(2026, 11, 5, 15, 0))
        slot = dt(2026, 11, 5, 16, 0)
        outbox = []

        def factory(ctx):
            def send(_ctx):
                outbox.append("email-1")
                return {"summary": "sent"}

            return [
                execution.JobStep(
                    name="send",
                    handler=send,
                    toll_event="agent.email_action",
                    idempotent=False,
                )
            ]

        # Wire the journal to the real fire id once it exists by
        # journaling through fire_job's seam with a wrapper that
        # swaps in the DB journal for the actual fire, then dies
        # on the 'executed' write: the send is out, the journal
        # never lands.
        holder = {}

        def journal(phase, step):
            if "inner" not in holder:
                fire_rows = db.tables.get("agt_job_fires", [])
                fid = fire_rows[-1]["id"] if fire_rows else "none"
                fkey = fire_rows[-1]["fire_key"] if fire_rows else "none"
                holder["inner"] = reconciliation.make_db_step_journal(
                    db, ORG, AGENT, fid, fkey
                )
            if phase == "executed":
                raise RuntimeError("worker killed before journal write")
            return holder["inner"](phase, step)

        sink = FakeTollSink()
        outcome = service.fire_job(
            db,
            ORG,
            AGENT,
            job,
            slot,
            executor_factory=factory,
            toll_sink=sink,
            step_journal=journal,
        )
        assert outcome["status"] == "failed"
        assert outbox == ["email-1"]  # the send happened
        fire = db.tables["agt_job_fires"][0]
        report = reconciliation.reconcile_fire(
            db,
            ORG,
            AGENT,
            fire["id"],
            executor_factory=factory,
            toll_sink=sink,
            toll_reader=sink.reader,
            apply=True,
        )
        by_name = {s["name"]: s["classification"] for s in report["steps"]}
        assert by_name["send"] == "unknown"
        assert report["unknown_steps"] == ["send"]
        assert sink.rows == {}  # unknown is never auto-tolled/retried
        assert outbox == ["email-1"]  # and never re-sent

    def test_resume_after_review_refused_on_unknown(self, db, tolls):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(db, job, started_minutes_ago=120, now=now)
        # Journal shows the non-idempotent step started but never
        # journaled executed: its outcome is unknown.
        journal = reconciliation.make_db_step_journal(
            db, ORG, AGENT, fire["id"], fire["fire_key"]
        )
        step = execution.JobStep(
            name="send",
            handler=lambda _c: {},
            toll_event="agent.email_action",
            idempotent=False,
        )
        journal("started", step)
        recovery.reap_stranded_fires(db, now=now, stale_after=timedelta(minutes=30))

        def factory(ctx):
            return [step]

        with pytest.raises(AgentJobError):
            recovery.resume_after_review(
                db,
                ORG,
                AGENT,
                job["id"],
                executor_factory=factory,
                toll_sink=tolls,
                toll_reader=tolls.reader,
                now=dt(2026, 11, 5, 19, 0),
            )
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "review"

    def test_resume_after_review_succeeds_when_all_classified(self, db, tolls):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(db, job, started_minutes_ago=120, now=now)
        journal = reconciliation.make_db_step_journal(
            db, ORG, AGENT, fire["id"], fire["fire_key"]
        )
        step = execution.JobStep(
            name="send",
            handler=lambda _c: {},
            toll_event="agent.email_action",
            idempotent=False,
        )
        journal("started", step)
        journal("executed", step)
        tolls(
            db,
            ORG,
            "agent.email_action",
            idempotency_key=f"{fire['fire_key']}:step:send",
            source_type="agt_job_fire",
            source_id=fire["id"],
        )
        recovery.reap_stranded_fires(db, now=now, stale_after=timedelta(minutes=30))

        def factory(ctx):
            return [step]

        result = recovery.resume_after_review(
            db,
            ORG,
            AGENT,
            job["id"],
            executor_factory=factory,
            toll_sink=tolls,
            toll_reader=tolls.reader,
            now=dt(2026, 11, 5, 19, 0),
        )
        assert result["job"]["status"] == "active"
        assert result["reports"][0]["steps"][0]["classification"] == "done"


# -- durable tick heartbeat (Rex F7 tail) ----------------------------


class TestTickHeartbeat:
    def test_tick_records_durable_heartbeat(self, db, tolls):
        _job(db, dt(2026, 11, 5, 15, 0))
        now = dt(2026, 11, 5, 16, 0)
        _tick(db, now, tolls=tolls)
        health = heartbeat.get_tick_health(db, now=now)
        assert health["last_tick_at"] == now.isoformat()
        assert health["status"] == "ok"
        assert health["stale"] is False
        assert health["fired_count"] == 1

    def test_scan_failure_still_records_heartbeat(self, db):
        def hook(table):
            if table == "agt_jobs":
                raise RuntimeError("db down")

        db.select_hook = hook
        with pytest.raises(ticker_mod.JobTickerError):
            _tick(db, dt(2026, 11, 5, 16, 0))
        health = heartbeat.get_tick_health(db, now=dt(2026, 11, 5, 16, 0))
        assert health["status"] == "scan_failed"
        assert health["last_tick_at"] is not None

    def test_dead_scheduler_is_visible_and_alerts_once(self, db):
        _job(db, dt(2026, 11, 5, 15, 0))
        _tick(db, dt(2026, 11, 5, 16, 0), tolls=FakeTollSink())
        alerts = []
        later = dt(2026, 11, 5, 19, 0)
        health = heartbeat.check_scheduler_health(
            db,
            now=later,
            stale_after=timedelta(minutes=15),
            health_alerter=lambda h: alerts.append(h) or "alert-1",
        )
        assert health["stale"] is True
        assert health["alerted"] is True
        assert len(alerts) == 1
        again = heartbeat.check_scheduler_health(
            db,
            now=later,
            stale_after=timedelta(minutes=15),
            health_alerter=lambda h: alerts.append(h) or "alert-2",
        )
        assert again["alerted"] is False  # same stale episode, no re-alert
        assert len(alerts) == 1


# -- catch-up collapsed-slot accounting (Rex F8) ----------------------


class TestCatchUpAccounting:
    def test_collapsed_slots_are_counted_on_catch_up_fire(self, db, tolls):
        # Daily 09:00 Denver job; ticker down five days; the slot
        # is inside the 7-day catch-up window, so it fires ONCE
        # and the five collapsed daily slots are counted.
        _job(
            db,
            dt(2026, 11, 5, 15, 0),
            catch_up_window_minutes=7 * 24 * 60,
        )
        out = _tick(db, dt(2026, 11, 10, 16, 0), tolls=tolls)
        assert len(out["fired"]) == 1
        assert out["missed_collapsed"] == 5

    def test_skipped_slots_are_all_counted(self, db, tolls):
        # Slot three days stale, window 60 minutes: nothing fires
        # and every skipped daily slot is counted, not just one.
        _job(db, dt(2026, 11, 5, 15, 0))
        out = _tick(db, dt(2026, 11, 8, 16, 0), tolls=tolls)
        assert out["fired"] == []
        assert out["missed_skipped"] == 4


# -- tick-summary accuracy (Rex P14 / Rosa nit) -----------------------


class TestTickSummaryAccuracy:
    def test_advance_failure_after_fire_is_not_double_listed(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))

        def hook(table, payload):
            if table == "agt_jobs" and "next_fire_at" in payload:
                raise RuntimeError("advance write down")

        db.update_hook = hook
        out = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls)
        assert [f["job_id"] for f in out["fired"]] == [job["id"]]
        assert out["failed"] == []
        assert out["errors"] == []
        assert [a["job_id"] for a in out["advance_failed"]] == [job["id"]]
        assert len(db.tables["agt_job_fires"]) == 1


# -- router wiring (Rex F5) -------------------------------------------


class TestRouterWiring:
    def _fake_request(self):
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    def test_shared_run_control_is_one_instance(self):
        from app.agent_jobs import router as router_mod

        request = self._fake_request()
        first = router_mod._shared_control(request)
        second = router_mod._shared_control(request)
        assert first is second

    def test_make_steps_carries_toll_event(self):
        from app.agent_jobs import router as router_mod

        request = self._fake_request()

        class FakeRuntime:
            def enqueue(self, task):
                return {"task_id": task.id}

        request.app.state.agent_runtime_factory = lambda db, org_id: FakeRuntime()
        factory = router_mod._executor_factory(request, FakeDb(), ORG)
        ctx = execution.build_execution_context(
            org_id=ORG,
            agent_id=AGENT,
            job_id="job-1",
            fire_id="fire-1",
            fire_key="job-1:slot",
            prompt="x",
            skill_keys=[],
            scheduled_for=dt(2026, 11, 5, 16, 0),
            timezone_name="UTC",
        )
        steps = factory(ctx)
        assert steps[0].toll_event == "agent.task_completed"

    def test_tick_endpoint_tolls_through_shared_control(self, db, monkeypatch):
        from app.agent_jobs import router as router_mod
        from app.services import toll_service

        # Simulate the post-B3 base: the agent toll vocabulary
        # exists in the rate table (B3 merge-order dependency).
        monkeypatch.setitem(toll_service.TOLL_RATES, "agent.task_completed", 25)
        monkeypatch.setattr(
            router_mod, "settings", SimpleNamespace(agent_jobs_tick_key="s3cret")
        )
        monkeypatch.setattr(service, "jobs_enabled", lambda org, e=None: True)
        # The tick endpoint uses wall-clock now, so the job is
        # created relative to it: an every-minute job whose slot
        # is two minutes old, inside the catch-up window.
        real_now = datetime.now(UTC)
        service.create_job(
            db,
            ORG,
            AGENT,
            _spec(cron_expr="* * * * *", timezone="UTC"),
            now=real_now - timedelta(minutes=2),
        )
        request = self._fake_request()

        class FakeRuntime:
            def enqueue(self, task):
                return {"task_id": task.id}

        request.app.state.agent_runtime_factory = lambda db_, org_id: FakeRuntime()
        # Preflight seams for the HTTP path: a skill registry that
        # reports ready+licensed, and a configured Notify provider
        # row so the default delivery check passes.
        request.app.state.agent_skill_checker = lambda key: {
            "ready": True,
            "licensed": True,
            "active": True,
        }
        db.table("ntf_providers").insert(
            {
                "org_id": ORG,
                "provider_name": "resend",
                "channel": "email",
                "is_enabled": True,
                "config": {},
            }
        ).execute()
        summary = router_mod.tick_jobs(request, x_tick_key="s3cret", db=db)
        assert len(summary["fired"]) == 1
        toll_rows = db.tables.get("toll_events", [])
        assert len(toll_rows) == 1
        fire = db.tables["agt_job_fires"][0]
        assert toll_rows[0]["idempotency_key"] == f"{fire['fire_key']}:step:run_task"
        assert toll_rows[0]["source_id"] == fire["id"]
        # The tick also left a durable heartbeat behind.
        health = heartbeat.get_tick_health(db)
        assert health["status"] == "ok"

    def test_cancel_endpoint_shares_the_run_control(self, db):
        from app.agent_jobs import router as router_mod

        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(
            db, job, started_minutes_ago=0, now=dt(2026, 11, 5, 16, 0)
        )
        request = self._fake_request()
        result = router_mod.cancel_run(
            job["id"],
            router_mod.CancelIn(fire_id=fire["id"]),
            agent_id=AGENT,
            request=request,
            org_id=ORG,
            db=db,
        )
        assert result["status"] == "cancelled"
        control = router_mod._shared_control(request)
        assert control.is_fenced(fire["id"])


# -- status vocabulary + migration shape -------------------------------


class TestRexRetestFixRound:
    """Rex TW-953 retest FAIL (P1/P3/P4/P8/P9): the gates around
    the crash-recovery core. Each test here failed on tip 78a6e08
    before the fix round."""

    # F1: the null pipe is not a configured delivery target.
    def _provider_row(self, db, name, *, enabled=True, channel="email"):
        db.table("ntf_providers").insert(
            {
                "org_id": ORG,
                "provider_name": name,
                "channel": channel,
                "is_enabled": enabled,
                "config": {},
            }
        ).execute()

    def test_null_provider_alone_is_not_configured_delivery(self, db):
        self._provider_row(db, "null")
        assert preflight.notify_delivery_configured(db, ORG) is False

    def test_null_enabled_with_only_real_provider_disabled_is_not_configured(self, db):
        self._provider_row(db, "null", enabled=True)
        self._provider_row(db, "resend", enabled=False)
        assert preflight.notify_delivery_configured(db, ORG) is False

    def test_unknown_provider_row_is_not_configured_delivery(self, db):
        self._provider_row(db, "bogus-provider")
        assert preflight.notify_delivery_configured(db, ORG) is False

    def test_real_enabled_provider_is_configured_delivery(self, db):
        self._provider_row(db, "resend", enabled=True)
        assert preflight.notify_delivery_configured(db, ORG) is True

    def test_disabled_real_provider_alone_is_not_configured(self, db):
        self._provider_row(db, "twilio_sms", enabled=False, channel="sms")
        assert preflight.notify_delivery_configured(db, ORG) is False

    # F2: a job in review resumes ONLY through resume_after_review.
    def _review_job(self, db):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        for row in db.tables["agt_jobs"]:
            if row["id"] == job["id"]:
                row["status"] = "review"
                row["review_reason"] = "fire interrupted mid-run"
                row["review_fire_id"] = "fire-x"
        return service.get_job(db, ORG, AGENT, job["id"])

    def test_resume_job_refuses_a_review_job(self, db):
        job = self._review_job(db)
        with pytest.raises(AgentJobError):
            service.resume_job(db, ORG, AGENT, job["id"])
        after = service.get_job(db, ORG, AGENT, job["id"])
        assert after["status"] == "review"
        assert after["review_reason"] == "fire interrupted mid-run"
        assert after["review_fire_id"] == "fire-x"

    def test_trigger_now_refuses_a_review_job(self, db, tolls):
        job = self._review_job(db)
        with pytest.raises(AgentJobError):
            service.trigger_now(
                db,
                ORG,
                AGENT,
                job["id"],
                executor_factory=_echo_factory(),
                toll_sink=tolls,
                now=dt(2026, 11, 5, 15, 30),
                enabled_orgs={ORG},
            )
        assert db.tables.get("agt_job_fires", []) == []
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "review"

    # F3: resume_after_review holds while tolls are still unposted.
    def test_resume_after_review_refused_while_executed_untolled(self, db):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(db, job, started_minutes_ago=120, now=now)
        journal = reconciliation.make_db_step_journal(
            db, ORG, AGENT, fire["id"], fire["fire_key"]
        )
        step = execution.JobStep(
            name="chase",
            handler=lambda _c: {},
            toll_event="agent.email_action",
            idempotent=False,
        )
        journal("started", step)
        journal("executed", step)
        recovery.reap_stranded_fires(db, now=now, stale_after=timedelta(minutes=30))
        key = f"{fire['fire_key']}:step:chase"
        down_sink = FakeTollSink(fail_keys={key})

        def factory(ctx):
            return [step]

        with pytest.raises(AgentJobError) as excinfo:
            recovery.resume_after_review(
                db,
                ORG,
                AGENT,
                job["id"],
                executor_factory=factory,
                toll_sink=down_sink,
                toll_reader=down_sink.reader,
                now=dt(2026, 11, 5, 19, 0),
            )
        assert "chase" in str(excinfo.value)
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "review"
        assert down_sink.rows == {}  # nothing tolled, nothing lost

    # F4: journal steps dropped from the plan still classify.
    def _journal_only_setup(self, db):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(db, job, started_minutes_ago=120, now=now)
        journal = reconciliation.make_db_step_journal(
            db, ORG, AGENT, fire["id"], fire["fire_key"]
        )
        return job, fire, journal

    def test_reconcile_classifies_journal_only_started_step_unknown(self, db, tolls):
        job, fire, journal = self._journal_only_setup(db)
        ghost = execution.JobStep(
            name="ghost",
            handler=lambda _c: {},
            toll_event="agent.email_action",
            idempotent=False,
        )
        journal("started", ghost)

        def factory(ctx):
            return [execution.JobStep(name="work", handler=lambda _c: {})]

        report = reconciliation.reconcile_fire(
            db,
            ORG,
            AGENT,
            fire["id"],
            executor_factory=factory,
            toll_sink=tolls,
            toll_reader=tolls.reader,
        )
        by_name = {s["name"]: s["classification"] for s in report["steps"]}
        assert by_name["ghost"] == "unknown"
        assert report["unknown_steps"] == ["ghost"]
        assert by_name["work"] == "not_done"

    def test_reconcile_classifies_journal_only_executed_step_untolled(self, db, tolls):
        job, fire, journal = self._journal_only_setup(db)
        ghost = execution.JobStep(
            name="ghost_exec",
            handler=lambda _c: {},
            toll_event="agent.email_action",
            idempotent=False,
        )
        journal("started", ghost)
        journal("executed", ghost)

        def factory(ctx):
            return [execution.JobStep(name="work", handler=lambda _c: {})]

        report = reconciliation.reconcile_fire(
            db,
            ORG,
            AGENT,
            fire["id"],
            executor_factory=factory,
            toll_sink=tolls,
            toll_reader=tolls.reader,
        )
        by_name = {s["name"]: s["classification"] for s in report["steps"]}
        assert by_name["ghost_exec"] == "executed_untolled"
        assert report["executed_untolled"] == ["ghost_exec"]

    def test_resume_after_review_refused_on_journal_only_unknown(self, db, tolls):
        now = dt(2026, 11, 5, 18, 0)
        job, fire, journal = self._journal_only_setup(db)
        ghost = execution.JobStep(
            name="ghost",
            handler=lambda _c: {},
            toll_event="agent.email_action",
            idempotent=False,
        )
        journal("started", ghost)
        recovery.reap_stranded_fires(db, now=now, stale_after=timedelta(minutes=30))

        def factory(ctx):
            return [execution.JobStep(name="work", handler=lambda _c: {})]

        with pytest.raises(AgentJobError) as excinfo:
            recovery.resume_after_review(
                db,
                ORG,
                AGENT,
                job["id"],
                executor_factory=factory,
                toll_sink=tolls,
                toll_reader=tolls.reader,
                now=dt(2026, 11, 5, 19, 0),
            )
        assert "ghost" in str(excinfo.value)
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "review"

    # F5: the production run_task step is non-idempotent.
    def test_router_run_task_step_is_non_idempotent(self):
        from app.agent_jobs import router as router_mod

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        class FakeRuntime:
            def enqueue(self, task):
                return {"task_id": task.id}

        request.app.state.agent_runtime_factory = lambda db, org_id: FakeRuntime()
        factory = router_mod._executor_factory(request, FakeDb(), ORG)
        ctx = execution.build_execution_context(
            org_id=ORG,
            agent_id=AGENT,
            job_id="job-1",
            fire_id="fire-1",
            fire_key="job-1:slot",
            prompt="x",
            skill_keys=[],
            scheduled_for=dt(2026, 11, 5, 16, 0),
            timezone_name="UTC",
        )
        steps = factory(ctx)
        assert steps[0].name == "run_task"
        assert steps[0].idempotent is False


class TestRosaGateFixRound:
    """Rosa TW-953 gate REQUEST CHANGES on 78a6e08: MF1 (never-
    ticked scheduler double-alerts), MF3 (preflight_scan and
    check_scheduler_health had no production caller). Each test
    here failed on 78a6e08 before this fix round."""

    # MF1: the episode marker must persist even when no
    # heartbeat row exists yet (Rosa's probe: 2 checks, 2 alerts).
    def test_never_ticked_scheduler_alerts_once_per_episode(self, db):
        alerts = []
        alerter = lambda health: alerts.append(health) or "alert-1"  # noqa: E731
        now = dt(2026, 11, 5, 16, 0)
        first = heartbeat.check_scheduler_health(
            db, now=now, stale_after=timedelta(minutes=15), health_alerter=alerter
        )
        second = heartbeat.check_scheduler_health(
            db,
            now=now + timedelta(minutes=5),
            stale_after=timedelta(minutes=15),
            health_alerter=alerter,
        )
        assert first["alerted"] is True
        assert second["alerted"] is False
        assert len(alerts) == 1
        # The marker persisted on a row the alert itself created.
        rows = db.tables.get("agt_job_tick_heartbeats", [])
        assert len(rows) == 1
        assert rows[0]["health_alerted_for"] == "never"

    # MF3a: the tick wires preflight_scan, so a poisoned job is
    # quarantined even when it is not yet due (Rosa's probe left
    # it 'active').
    def test_tick_quarantines_not_yet_due_poisoned_job(self, db, tolls, alerter):
        bad = _job(db, dt(2026, 11, 5, 15, 0), name="poisoned-future")
        for row in db.tables["agt_jobs"]:
            if row["id"] == bad["id"]:
                row["cron_expr"] = "not-a-cron"
        # Tick runs an hour before the job's 09:00 Denver slot.
        out = _tick(db, dt(2026, 11, 5, 15, 30), tolls=tolls, alerter=alerter)
        assert out["fired"] == []
        assert service.get_job(db, ORG, AGENT, bad["id"])["status"] == "blocked"
        assert [b["job_id"] for b in out["blocked"]] == [bad["id"]]
        assert len(alerter.calls) == 1

    # MF3b: the tick wires the scheduler health check: a stale
    # heartbeat alerts through the tick path, once per episode.
    def test_tick_runs_scheduler_health_check_and_alerts_once(self, db, tolls):
        _job(db, dt(2026, 11, 5, 15, 0))
        alerts = []
        health_alerter = lambda health: alerts.append(health) or "ha-1"  # noqa: E731
        out1 = _tick(
            db, dt(2026, 11, 5, 16, 0), tolls=tolls, health_alerter=health_alerter
        )
        assert out1["scheduler_health"]["alerted"] is True
        assert len(alerts) == 1
        # Ten minutes later the heartbeat is fresh: no new alert.
        out2 = _tick(
            db, dt(2026, 11, 5, 16, 10), tolls=tolls, health_alerter=health_alerter
        )
        assert out2["scheduler_health"]["alerted"] is False
        assert len(alerts) == 1

    # Rosa N2 (closed by the Rex F3 fix): resume_after_review
    # with NO toll sink must refuse over executed-but-untolled
    # steps, not resume past them.
    def test_resume_after_review_without_sink_refuses_untolled(self, db):
        now = dt(2026, 11, 5, 18, 0)
        job = _job(db, dt(2026, 11, 5, 15, 0))
        fire = _stranded_fire(db, job, started_minutes_ago=120, now=now)
        journal = reconciliation.make_db_step_journal(
            db, ORG, AGENT, fire["id"], fire["fire_key"]
        )
        step = execution.JobStep(
            name="chase",
            handler=lambda _c: {},
            toll_event="agent.email_action",
            idempotent=False,
        )
        journal("started", step)
        journal("executed", step)
        recovery.reap_stranded_fires(db, now=now, stale_after=timedelta(minutes=30))

        def factory(ctx):
            return [step]

        with pytest.raises(AgentJobError) as excinfo:
            recovery.resume_after_review(
                db,
                ORG,
                AGENT,
                job["id"],
                executor_factory=factory,
                toll_sink=None,
                toll_reader=FakeTollSink().reader,
                now=dt(2026, 11, 5, 19, 0),
            )
        assert "chase" in str(excinfo.value)
        assert service.get_job(db, ORG, AGENT, job["id"])["status"] == "review"


class TestVocabularyAndMigration:
    def test_interrupted_and_review_statuses_exist(self):
        assert "interrupted" in pmodels.FIRE_STATUSES
        assert "review" in pmodels.JOB_STATUSES

    def test_migration_is_additive_files_only(self):
        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "supabase",
            "migration_agent_jobs_tw953.sql",
        )
        sql = open(path).read()
        assert "Prepared by Twistor Holdings LLC" in sql
        assert "NEVER applied by this ticket" in sql
        assert "agt_job_fire_steps" in sql
        assert "agt_job_tick_heartbeats" in sql
        assert "DROP TABLE" not in sql
        assert "interrupted" in sql
