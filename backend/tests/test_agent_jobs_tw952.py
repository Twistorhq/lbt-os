"""TW-952 (TW-948-B4): persistent job service on the agent runtime.

Acceptance per build map Section 4 B4, run against a stateful
in-memory Supabase stand-in in the house pattern
(test_persistence_service.py) plus a fake toll sink that mirrors
the B1 record_toll_event seam contract (idempotency key required,
duplicate key returns the existing row, unknown event raises).
The B3 agent_tolls helpers are NOT on this base branch; the fake
sink stands in for the post-B3 vocabulary (agent.task_completed
and friends), which is the documented B3 dependency.
"""

import inspect
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("TWISTOR_PERSISTENCE_HASH_KEY", "tw952-test-key")

from app.agent_jobs import cron as cron_mod  # noqa: E402
from app.agent_jobs import execution, service, ticker as ticker_mod  # noqa: E402
from app.agent_jobs.models import AgentJobError, JobEdit, JobSpec  # noqa: E402

ORG = "org-1"
OTHER_ORG = "org-2"
AGENT = "agent-1"
UTC = timezone.utc


def dt(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# -- fake db (house pattern) ------------------------------------


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


class FakeTollSink:
    """The B1 toll seam contract, with the post-B3 vocabulary."""

    KNOWN = {"agent.task_completed", "agent.email_action"}

    def __init__(self):
        self.rows = {}
        self.calls = 0

    def __call__(self, db, org_id, event_type, quantity=1, idempotency_key=None,
                 source_type=None, source_id=None, amount_cents=None):
        self.calls += 1
        if event_type not in self.KNOWN:
            raise ValueError(f"Unknown toll event type: {event_type}")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
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


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def tolls():
    return FakeTollSink()


def _spec(**kwargs):
    base = dict(
        name="morning-scan",
        prompt="Scan the board and surface new leads.",
        skill_keys=["leads.scan", "leads.rank"],
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


def _tick(db, now, factory=None, tolls=None, control=None, orgs=(ORG,)):
    return ticker_mod.tick(
        db,
        now=now,
        executor_factory=factory or _echo_factory(),
        toll_sink=tolls,
        control=control,
        enabled_orgs=set(orgs),
    )


# -- cron semantics (Core workforce/schedules.py port) ----------


class TestCron:
    def test_full_field_syntax(self):
        sched = cron_mod.CronSchedule("*/15 9-17 1,15 * 1-5")
        # Monday 2026-11-02 09:30 local matches.
        assert sched.matches(datetime(2026, 11, 2, 9, 30))
        assert not sched.matches(datetime(2026, 11, 2, 9, 31))
        assert not sched.matches(datetime(2026, 11, 7, 9, 30))  # Saturday

    def test_vixie_dom_dow_or(self):
        sched = cron_mod.CronSchedule("0 0 13 * 5")
        # Friday the 6th matches via dow; the 13th (Friday) via both;
        # Monday the 13th-style dom match: use 2026-11-13 is Friday,
        # so check a dom-only date: 2026-12-13 is a Sunday.
        assert sched.matches(datetime(2026, 12, 13, 0, 0))
        assert sched.matches(datetime(2026, 11, 6, 0, 0))  # Friday, not 13th
        assert not sched.matches(datetime(2026, 11, 7, 0, 0))

    def test_dow_seven_is_sunday(self):
        sched = cron_mod.CronSchedule("0 9 * * 7")
        assert sched.matches(datetime(2026, 11, 8, 9, 0))  # Sunday
        assert not sched.matches(datetime(2026, 11, 9, 9, 0))

    def test_invalid_expressions_raise(self):
        for bad in ("0 9 * *", "61 9 * * *", "0 25 * * *", "0 9 * * x", ""):
            with pytest.raises(AgentJobError):
                cron_mod.CronSchedule(bad)

    def test_next_at_or_after_crosses_timezone(self):
        sched = cron_mod.CronSchedule("0 9 * * *")
        nxt = sched.next_at_or_after(dt(2026, 11, 5, 15, 0), "America/Denver")
        assert nxt == dt(2026, 11, 5, 16, 0)  # 09:00 MST is 16:00 UTC
        nxt2 = sched.next_at_or_after(dt(2026, 11, 5, 16, 1), "America/Denver")
        assert nxt2 == dt(2026, 11, 6, 16, 0)


# -- definitions and management ---------------------------------


class TestDefinitions:
    def test_create_computes_next_fire_in_job_timezone(self, db):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        assert job["status"] == "active"
        assert job["next_fire_at"] == dt(2026, 11, 5, 16, 0).isoformat()
        assert job["skill_keys"] == ["leads.scan", "leads.rank"]
        assert job["catch_up_window_minutes"] == 60

    def test_create_requires_cron_xor_one_shot(self, db):
        with pytest.raises(AgentJobError):
            _job(db, dt(2026, 11, 5), cron_expr=None, one_shot_at=None)
        with pytest.raises(AgentJobError):
            _job(
                db,
                dt(2026, 11, 5),
                cron_expr="0 9 * * *",
                one_shot_at=dt(2026, 11, 6, 16, 0),
            )

    def test_create_rejects_unknown_timezone(self, db):
        with pytest.raises(AgentJobError):
            _job(db, dt(2026, 11, 5), timezone="Mars/Olympus")

    def test_scoping_other_org_sees_nothing(self, db):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        assert service.get_job(db, OTHER_ORG, AGENT, job["id"]) is None
        assert service.list_jobs(db, OTHER_ORG, AGENT) == []

    def test_remove_archives_and_never_fires(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        service.remove_job(db, ORG, AGENT, job["id"])
        assert service.list_jobs(db, ORG, AGENT) == []
        out = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls)
        assert out["fired"] == []


# -- ticker behavior ---------------------------------------------


class TestTicker:
    def test_fires_at_9_in_own_timezone_regardless_of_server(self, db, tolls):
        _job(db, dt(2026, 11, 5, 15, 0))
        early = _tick(db, dt(2026, 11, 5, 14, 0), tolls=tolls)  # 07:00 Denver
        assert early["fired"] == []
        on_time = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls)  # 09:00 Denver
        assert len(on_time["fired"]) == 1

    def test_overlapping_ticks_fire_once(self, db, tolls):
        _job(db, dt(2026, 11, 5, 15, 0))
        first = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls)
        second = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls)
        third = _tick(db, dt(2026, 11, 5, 16, 1), tolls=tolls)
        assert len(first["fired"]) == 1
        assert second["fired"] == []
        assert third["fired"] == []
        assert len(db.tables["agt_job_fires"]) == 1

    def test_missed_fire_inside_catchup_fires_once_on_recovery(self, db, tolls):
        _job(db, dt(2026, 11, 5, 15, 0))  # slot 16:00, window 60m
        out = _tick(db, dt(2026, 11, 5, 16, 30), tolls=tolls)
        assert len(out["fired"]) == 1
        again = _tick(db, dt(2026, 11, 5, 16, 45), tolls=tolls)
        assert again["fired"] == []

    def test_missed_fire_outside_catchup_does_not_fire(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        out = _tick(db, dt(2026, 11, 5, 19, 30), tolls=tolls)  # 3.5h late
        assert out["fired"] == []
        assert out["missed_skipped"] == 1
        refreshed = service.get_job(db, ORG, AGENT, job["id"])
        assert refreshed["next_fire_at"] == dt(2026, 11, 6, 16, 0).isoformat()

    def test_paused_jobs_never_fire(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        service.pause_job(db, ORG, AGENT, job["id"])
        out = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls)
        assert out["fired"] == []
        assert db.tables.get("agt_job_fires", []) == []

    def test_resume_recomputes_next_fire(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        service.pause_job(db, ORG, AGENT, job["id"])
        resumed = service.resume_job(
            db, ORG, AGENT, job["id"], now=dt(2026, 11, 5, 17, 0)
        )
        assert resumed["status"] == "active"
        assert resumed["next_fire_at"] == dt(2026, 11, 6, 16, 0).isoformat()
        out = _tick(db, dt(2026, 11, 6, 16, 0), tolls=tolls)
        assert len(out["fired"]) == 1

    def test_flag_off_org_never_fires(self, db, tolls):
        _job(db, dt(2026, 11, 5, 15, 0))
        out = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls, orgs=())
        assert out["fired"] == []
        assert out["skipped_flag_off"] == 1

    def test_one_shot_fires_once_at_its_instant(self, db, tolls):
        job = _job(
            db,
            dt(2026, 11, 5, 15, 0),
            name="one-off",
            cron_expr=None,
            one_shot_at=dt(2026, 11, 5, 18, 0),
        )
        assert job["next_fire_at"] == dt(2026, 11, 5, 18, 0).isoformat()
        assert _tick(db, dt(2026, 11, 5, 17, 0), tolls=tolls)["fired"] == []
        assert len(_tick(db, dt(2026, 11, 5, 18, 0), tolls=tolls)["fired"]) == 1
        assert _tick(db, dt(2026, 11, 5, 18, 5), tolls=tolls)["fired"] == []
        done = service.get_job(db, ORG, AGENT, job["id"])
        assert done["next_fire_at"] is None

    def test_fire_keys_namespaced_per_job(self, db, tolls):
        a = _job(db, dt(2026, 11, 5, 15, 0), name="scan-a")
        b = _job(db, dt(2026, 11, 5, 15, 0), name="scan-b")
        _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls)
        keys = sorted(r["fire_key"] for r in db.tables["agt_job_fires"])
        assert len(keys) == 2 and keys[0] != keys[1]
        assert keys[0].startswith(a["id"] + ":")
        assert keys[1].startswith(b["id"] + ":")

    def test_scan_failure_fails_loud(self, db):
        class ExplodingDb(FakeDb):
            def table(self, name):
                raise RuntimeError("db down")

        with pytest.raises(ticker_mod.JobTickerError):
            ticker_mod.tick(
                ExplodingDb(),
                now=dt(2026, 11, 5, 16, 0),
                executor_factory=_echo_factory(),
                enabled_orgs={ORG},
            )


# -- execution: continuity, masking, tolls, cancel ---------------


class TestExecution:
    def test_continuity_dedupe_across_fires(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        candidates = ["lead-1", "lead-2", "lead-3"]

        def factory(ctx):
            fresh = [c for c in candidates if c not in ctx.continuity_surfaced_ids]

            def run(_ctx, fresh=fresh):
                return {"summary": f"found {len(fresh)}", "surfaced_item_ids": fresh}

            return [execution.JobStep(name="scan", handler=run)]

        _tick(db, dt(2026, 11, 5, 16, 0), factory=factory, tolls=tolls)
        _tick(db, dt(2026, 11, 6, 16, 0), factory=factory, tolls=tolls)
        from app.persistence import service as psvc

        cont = psvc.get_continuity(db, ORG, AGENT, job["id"])
        assert cont["surfaced_item_ids"] == ["lead-1", "lead-2", "lead-3"]
        assert cont["run_count"] == 2
        fires = psvc.list_fires(db, ORG, AGENT, job["id"])
        summaries = sorted(f["summary"] for f in fires)
        assert summaries == ["found 1", "found 3"]

    def test_fire_writer_masks_summary_and_error(self, db, tolls):
        _job(db, dt(2026, 11, 5, 15, 0), name="mask-ok")
        ok = _echo_factory(summary="Emailed bob@example.com about the job")
        _tick(db, dt(2026, 11, 5, 16, 0), factory=ok, tolls=tolls)
        fire = db.tables["agt_job_fires"][0]
        assert "bob@example.com" not in fire["summary"]
        assert "[email]" in fire["summary"]

        _job(db, dt(2026, 11, 5, 15, 0), name="mask-fail")

        def boom_factory(ctx):
            def run(_ctx):
                raise RuntimeError("send to bob@example.com failed")

            return [execution.JobStep(name="work", handler=run)]

        _tick(db, dt(2026, 11, 5, 16, 0), factory=boom_factory, tolls=tolls)
        failed = [f for f in db.tables["agt_job_fires"] if f["status"] == "failed"]
        assert len(failed) == 1
        assert "bob@example.com" not in (failed[0]["error"] or "")

    def test_cancel_in_flight_fences_and_tolls_once(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        control = execution.RunControl()
        ran = []

        def factory(ctx):
            def s1(_ctx):
                ran.append("s1")
                return {}

            def s2(_ctx):
                ran.append("s2")
                service.cancel_run(
                    db, ORG, AGENT, job["id"], ctx.fire_id, control=control
                )
                return {}

            def s3(_ctx):
                ran.append("s3")
                return {}

            return [
                execution.JobStep(
                    name="s1", handler=s1, toll_event="agent.task_completed"
                ),
                execution.JobStep(
                    name="s2", handler=s2, toll_event="agent.task_completed"
                ),
                execution.JobStep(
                    name="s3", handler=s3, toll_event="agent.task_completed"
                ),
            ]

        out = _tick(db, dt(2026, 11, 5, 16, 0), factory=factory,
                    tolls=tolls, control=control)
        assert out["fired"][0]["status"] == "cancelled"
        assert ran == ["s1", "s2"]  # nothing further ran after the fence
        fire = db.tables["agt_job_fires"][0]
        assert fire["status"] == "cancelled"
        # Completed steps tolled exactly once, keyed on the fire.
        assert sorted(tolls.rows) == [
            f"{fire['fire_key']}:step:s1",
            f"{fire['fire_key']}:step:s2",
        ]
        # A retry of the same steps against the same toll seam does
        # not double-toll: idempotency keys root on the fire key.
        ctx = execution.build_execution_context(
            org_id=ORG,
            agent_id=AGENT,
            job_id=job["id"],
            fire_id=fire["id"],
            fire_key=fire["fire_key"],
            prompt="x",
            skill_keys=[],
            scheduled_for=dt(2026, 11, 5, 16, 0),
            timezone_name="UTC",
        )
        steps = factory(ctx)
        execution.run_steps(ctx, steps[:2], toll_sink=tolls, db=db)
        assert len(tolls.rows) == 2

    def test_no_job_creates_job_by_construction(self, db, tolls):
        seen = {}

        def factory(ctx):
            seen["ctx"] = ctx

            def run(_ctx):
                try:
                    _ctx.create_job(name="child", prompt="escape")
                except AttributeError:
                    return {"summary": "no capability present"}
                return {"summary": "ESCAPED"}

            return [execution.JobStep(name="work", handler=run)]

        _job(db, dt(2026, 11, 5, 15, 0))
        out = _tick(db, dt(2026, 11, 5, 16, 0), factory=factory, tolls=tolls)
        assert out["fired"][0]["summary"] == "no capability present"
        ctx = seen["ctx"]
        assert not hasattr(ctx, "create_job")
        assert not hasattr(ctx, "job_service")
        assert not hasattr(ctx, "db")
        assert "jobs.manage" not in ctx.capabilities
        # Construction takes no service or db handle at all.
        params = inspect.signature(
            execution.build_execution_context
        ).parameters
        assert "db" not in params and "service" not in params
        assert len(db.tables["agt_jobs"]) == 1  # only the parent job exists

    def test_execution_context_carries_ordered_skills(self, db, tolls):
        seen = {}

        def factory(ctx):
            seen["skills"] = ctx.skill_keys
            return [execution.JobStep(name="w", handler=lambda _c: {})]

        _job(db, dt(2026, 11, 5, 15, 0))
        _tick(db, dt(2026, 11, 5, 16, 0), factory=factory, tolls=tolls)
        assert seen["skills"] == ("leads.scan", "leads.rank")

    def test_trigger_now_fires_immediately(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        outcome = service.trigger_now(
            db,
            ORG,
            AGENT,
            job["id"],
            executor_factory=_echo_factory(summary="manual"),
            toll_sink=tolls,
            now=dt(2026, 11, 5, 15, 30),
            enabled_orgs={ORG},
        )
        assert outcome["status"] == "completed"
        assert outcome["summary"] == "manual"
        # The scheduled slot still fires later, untouched.
        out = _tick(db, dt(2026, 11, 5, 16, 0), tolls=tolls)
        assert len(out["fired"]) == 1

    def test_edit_does_not_rewrite_history(self, db, tolls):
        job = _job(db, dt(2026, 11, 5, 15, 0))
        _tick(
            db,
            dt(2026, 11, 5, 16, 0),
            factory=_echo_factory(surfaced=["lead-1"], summary="first"),
            tolls=tolls,
        )
        fires_before = [dict(r) for r in db.tables["agt_job_fires"]]
        cont_before = [dict(r) for r in db.tables["agt_job_continuity"]]
        edited = service.edit_job(
            db,
            ORG,
            AGENT,
            job["id"],
            JobEdit(prompt="New prompt.", cron_expr="30 8 * * *"),
            now=dt(2026, 11, 5, 17, 0),
        )
        assert edited["prompt"] == "New prompt."
        assert edited["next_fire_at"] == dt(2026, 11, 6, 15, 30).isoformat()
        assert [dict(r) for r in db.tables["agt_job_fires"]] == fires_before
        assert [dict(r) for r in db.tables["agt_job_continuity"]] == cont_before


# -- runtime adapter ----------------------------------------------


class TestRuntimeAdapter:
    def test_adapter_enqueues_agent_runtime_task(self):
        from app.agent_jobs.execution import RuntimeTaskExecutor

        calls = []

        class FakeRuntime:
            def enqueue(self, task):
                calls.append(task)
                return {"task_id": task.id, "status": "queued"}

        adapter = RuntimeTaskExecutor(FakeRuntime())
        ctx = execution.build_execution_context(
            org_id=ORG,
            agent_id=AGENT,
            job_id="job-1",
            fire_id="fire-1",
            fire_key="job-1:slot",
            prompt="Scan the board.",
            skill_keys=["leads.scan"],
            scheduled_for=dt(2026, 11, 5, 16, 0),
            timezone_name="America/Denver",
            continuity_surfaced_ids=["lead-1"],
        )
        result = adapter(ctx)
        assert result["task_id"]
        task = calls[0]
        assert task.org_id == ORG
        assert task.agent_identity_id == AGENT
        assert task.source in ("email", "phone", "flow", "owner")
        assert "Scan the board." in task.content
        assert "leads.scan" in task.content
        assert "lead-1" in task.content
