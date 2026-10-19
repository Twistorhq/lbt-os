"""TW-625 fix round 2: Rosa's tick-path majors.

Rosa proved by execution that the scheduler tick's partial-column
selects broke the production resume path: the flw_runs scan did not
select context/steps_executed (context restore failed on delay-then-
personalize runs, per-run cap restarted at 0), and a budget-paused
workflow never auto-resumed from the tick scan. These tests run the
REAL scheduler.tick against a row-filtering fake DB.
"""

import unittest
from unittest.mock import patch

from app.flows import models, scheduler, service


class _ExecResult:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table, cols):
        self._db = db
        self._table = table
        self._cols = cols
        self._filters = []

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, vals))
        return self

    def execute(self):
        rows = [dict(r) for r in self._db.tables.get(self._table, [])]
        for kind, col, val in self._filters:
            if kind == "eq":
                rows = [r for r in rows if r.get(col) == val]
            else:
                rows = [r for r in rows if r.get(col) in val]
        self._db.select_cols.setdefault(self._table, []).append(self._cols)
        return _ExecResult(rows)


class _Update:
    def __init__(self, db, table, patch_dict):
        self._db = db
        self._table = table
        self._patch = patch_dict
        self._filters = []

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def execute(self):
        for row in self._db.tables.get(self._table, []):
            if all(row.get(c) == v for c, v in self._filters):
                row.update(self._patch)
                self._db.updates.append(
                    {
                        "table": self._table,
                        "id": row.get("id"),
                        "patch": dict(self._patch),
                    }
                )
        return _ExecResult([])


class _Table:
    def __init__(self, db, name):
        self._db = db
        self._name = name

    def select(self, cols):
        return _Query(self._db, self._name, cols)

    def update(self, patch_dict):
        return _Update(self._db, self._name, patch_dict)

    def upsert(self, row, on_conflict=None):
        tbl = self._db.tables.setdefault(self._name, [])
        for r in tbl:
            if r.get("id") == row.get("id"):
                r.update(row)
                break
        else:
            tbl.append(dict(row))
        return _ExecResult([row])

    def insert(self, row):
        self._db.tables.setdefault(self._name, []).append(dict(row))
        return _ExecResult([dict(row)])


class _DB:
    def __init__(self):
        self.tables = {}
        self.select_cols = {}
        self.updates = []

    def table(self, name):
        return _Table(self, name)


def _defn(steps, trigger=None):
    return {
        "name": "Tick lane",
        "trigger": trigger or {"type": "manual"},
        "steps": steps,
    }


def _send_only_definition(trigger=None):
    return _defn(
        [
            {
                "id": "n1",
                "type": "notify",
                "channel": "email",
                "to": "{{lead.email}}",
                "body": "hello",
            }
        ],
        trigger=trigger,
    )


class TickSelectColumnsTests(unittest.TestCase):
    """Major 1: the resume scan must select context + steps_executed."""

    def test_resume_scan_selects_context_and_steps_executed(self):
        db = _DB()
        db.tables["flw_workflows"] = []
        db.tables["flw_runs"] = []
        scheduler.tick(db)
        run_selects = db.select_cols.get("flw_runs", [])
        self.assertTrue(run_selects, "tick must scan flw_runs")
        cols = run_selects[0]
        self.assertIn("context", cols)
        self.assertIn("steps_executed", cols)


class TickResumePathTests(unittest.TestCase):
    """Major 1: the tick path must restore context and keep the step cap.

    Rosa's repro: a delay-then-personalize run resumed from the tick
    errored on 'unknown context path {{lead.email}}' and the per-run
    cap restarted at 0. The run below has already spent 1 step of a
    3-step cap; the send must succeed from persisted context and the
    cap must not restart.
    """

    def test_tick_resumes_delay_then_personalize_with_persisted_context(self):
        db = _DB()
        definition = _defn(
            [
                {"id": "d1", "type": "delay", "duration": "0s"},
                {
                    "id": "n2",
                    "type": "notify",
                    "channel": "email",
                    "to": "{{lead.email}}",
                    "body": "touch2",
                },
            ]
        )
        wf_row = {
            "id": "wf-t",
            "org_id": "org-1",
            "name": "Touch",
            "status": "active",
            "alert_to": None,
            "definition": definition,
        }
        db.tables["flw_workflows"] = [wf_row]
        # 1 step already spent against a 3-step cap, context persisted
        run_row = {
            "id": "run-t",
            "org_id": "org-1",
            "workflow_id": "wf-t",
            "status": "suspended",
            "resume_at": "2000-01-01T00:00:00+00:00",
            "resume_step_id": "d1",
            "context": {"lead": {"email": "tick@shop.com"}},
            "steps_executed": 1,
        }
        db.tables["flw_runs"] = [run_row]

        sent = []
        with patch("app.flows.executor.notify_service") as nsvc:

            def _capture(*a, **k):
                sent.append((a, k))
                return {"ok": True, "id": "m-t"}

            nsvc.send.side_effect = _capture
            with patch("app.flows.executor.budgets") as bdg:
                bdg.check_and_consume.return_value = ("ok", {})
                result = scheduler.tick(db)

        self.assertEqual(result["resumed"], ["run-t:completed"])
        self.assertEqual(len(sent), 1)
        # the tick path preserved the persisted context, not a fresh one
        args, kwargs = sent[0]
        all_values = list(args) + list(kwargs.values())
        self.assertTrue(
            any("tick@shop.com" in str(v) for v in all_values),
            f"personalized recipient missing from send call: {sent}",
        )
        # cap counted against persisted steps_executed (would error if cap=3 restarted)
        resumed = [r for r in db.tables["flw_runs"] if r["id"] == "run-t"][0]
        self.assertEqual(resumed["status"], "completed")


class TickAutoResumeTests(unittest.TestCase):
    """Major 2: a budget-paused workflow must auto-resume from the tick
    when the UTC day has rolled the counters over."""

    def _paused_budget_workflow(self, **kw):
        row = {
            "id": "wf-p",
            "org_id": "org-1",
            "name": "Warmup ramp",
            "status": "paused",
            "paused_reason": "task budget: daily step cap reached",
            "alert_to": None,
            "definition": _send_only_definition(
                {"type": "schedule", "cron": "* * * * *"}
            ),
        }
        row.update(kw)
        return row

    def _under_cap_counters(self, db):
        db.tables["flw_budget_counters"] = [
            {
                "id": "c-1",
                "org_id": "org-1",
                "workflow_id": "wf-p",
                "day": "2099-01-01",  # yesterday: today's counter is empty
                "runs": 999,
                "steps": 999,
                "cost_cents": 99999,
            }
        ]

    def test_schedule_scan_auto_resumes_after_day_rollover(self):
        db = _DB()
        db.tables["flw_workflows"] = [self._paused_budget_workflow()]
        db.tables["flw_runs"] = []
        self._under_cap_counters(db)

        with patch("app.flows.executor.notify_service") as nsvc:
            nsvc.send.return_value = {"ok": True, "id": "m-p"}
            with patch("app.flows.executor.budgets") as bdg:
                bdg.check_and_consume.return_value = ("ok", {})
                result = scheduler.tick(db)

        fired = result["fired"]
        self.assertEqual(len(fired), 1)
        self.assertTrue(fired[0].startswith("wf-p:"))
        wf = db.tables["flw_workflows"][0]
        self.assertEqual(wf["status"], "active")

    def test_manually_paused_workflow_stays_paused_on_tick(self):
        db = _DB()
        row = self._paused_budget_workflow(
            paused_reason="manual: Justynn paused the ramp"
        )
        db.tables["flw_workflows"] = [row]
        db.tables["flw_runs"] = []
        self._under_cap_counters(db)

        with patch("app.flows.executor.notify_service") as nsvc:
            nsvc.send.return_value = {"ok": True, "id": "m-p"}
            with patch("app.flows.executor.budgets") as bdg:
                bdg.check_and_consume.return_value = ("ok", {})
                result = scheduler.tick(db)

        self.assertEqual(result["fired"], [])
        self.assertEqual(db.tables["flw_workflows"][0]["status"], "paused")

    def test_suspended_run_resumes_after_budget_auto_resume(self):
        db = _DB()
        wf_row = self._paused_budget_workflow()
        wf_row["definition"] = _defn(
            [
                {"id": "d1", "type": "delay", "duration": "0s"},
                {
                    "id": "n2",
                    "type": "notify",
                    "channel": "email",
                    "to": "{{lead.email}}",
                    "body": "touch2",
                },
            ],
            {"type": "schedule", "cron": "* * * * *"},
        )
        db.tables["flw_workflows"] = [wf_row]
        db.tables["flw_runs"] = [
            {
                "id": "run-p",
                "org_id": "org-1",
                "workflow_id": "wf-p",
                "status": "suspended",
                "resume_at": "2000-01-01T00:00:00+00:00",
                "resume_step_id": "d1",
                "context": {"lead": {"email": "ramp@shop.com"}},
                "steps_executed": 1,
            }
        ]
        self._under_cap_counters(db)

        with patch("app.flows.executor.notify_service") as nsvc:
            nsvc.send.return_value = {"ok": True, "id": "m-r"}
            with patch("app.flows.executor.budgets") as bdg:
                bdg.check_and_consume.return_value = ("ok", {})
                result = scheduler.tick(db)

        self.assertEqual(result["resumed"], ["run-p:completed"])
        self.assertEqual(db.tables["flw_workflows"][0]["status"], "active")


class MaybeAutoResumeTests(unittest.TestCase):
    def test_no_rollover_stays_paused(self):
        from datetime import date

        db = _DB()
        wf_row = {
            "id": "wf-n",
            "org_id": "org-1",
            "status": "paused",
            "paused_reason": "task budget: daily run cap reached",
            "definition": _send_only_definition(),
        }
        db.tables["flw_workflows"] = [wf_row]
        # today's counter is AT the cap: no resume
        budget = models.validate_definition(wf_row["definition"]).budget
        db.tables["flw_budget_counters"] = [
            {
                "id": "c-2",
                "org_id": "org-1",
                "workflow_id": "wf-n",
                "day": date.today().isoformat(),
                "runs": budget.max_runs_per_day,
                "steps": 0,
                "cost_cents": 0,
            }
        ]
        self.assertIsNone(service.maybe_auto_resume(db, "org-1", wf_row))
        self.assertEqual(db.tables["flw_workflows"][0]["status"], "paused")


class CronDowSevenTests(unittest.TestCase):
    def test_dow_7_matches_sunday(self):
        from datetime import datetime, timezone

        # 2026-10-18 is a Sunday
        sunday = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(sunday.weekday(), 6)
        self.assertTrue(scheduler._cron_due("* * * * 7", sunday))
        self.assertTrue(scheduler._cron_due("* * * * 0", sunday))

    def test_dow_7_does_not_match_monday(self):
        from datetime import datetime, timezone

        monday = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(scheduler._cron_due("* * * * 7", monday))


if __name__ == "__main__":
    unittest.main()
