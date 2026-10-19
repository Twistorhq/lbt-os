"""TW-625 Twistor Flows: service orchestration + MCP + scheduler (RED first)."""

import datetime
import unittest
from unittest.mock import MagicMock, patch

from app.flows import mcp, models, scheduler, service


def _table_mock(rows=None):
    """A very small supabase-table mock: insert/update/upsert/select/eq."""
    rows = rows or []
    tbl = MagicMock()

    def _exec(data):
        m = MagicMock()
        m.data = data
        ex = MagicMock()
        ex.execute.return_value = m
        return ex

    tbl.insert.side_effect = lambda row: _exec([dict(row, id="wf-1")])
    tbl.update.side_effect = lambda row: _exec([dict(row)])
    tbl.upsert.side_effect = lambda row, on_conflict=None: _exec([dict(row)])
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=list(rows))
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.in_.return_value = chain
    tbl.select.return_value = chain
    tbl.eq.return_value = chain
    return tbl


def _db_with(workflow_row):
    db = MagicMock()
    db.table.side_effect = lambda name: _table_mock(
        [workflow_row] if name == "flw_workflows" else []
    )
    return db


def _wf_row(**kw):
    base = {
        "id": "wf-1",
        "org_id": "org-1",
        "name": "Touch 1",
        "status": "active",
        "alert_to": None,
        "definition": {
            "name": "Touch 1",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "s1", "type": "set", "var": "lead.stage", "value": "engaged"}
            ],
        },
    }
    base.update(kw)
    return base


class ServiceTests(unittest.TestCase):
    def test_create_validates_definition(self):
        db = MagicMock()
        db.table.return_value = _table_mock()
        with self.assertRaises(models.DefinitionError):
            service.create_workflow(db, "org-1", "bad", {"steps": []})

    def test_create_ok(self):
        db = MagicMock()
        db.table.return_value = _table_mock()
        row = service.create_workflow(
            db,
            "org-1",
            "Touch 1",
            {
                "trigger": {"type": "manual"},
                "steps": [{"id": "s1", "type": "set", "var": "x", "value": "1"}],
            },
            alert_to="owner@shop.com",
        )
        self.assertEqual(row["name"], "Touch 1")
        self.assertEqual(row["alert_to"], "owner@shop.com")

    def test_trigger_refuses_paused_workflow(self):
        db = _db_with(_wf_row(status="paused", paused_reason="budget 100%"))
        with self.assertRaises(PermissionError):
            service.trigger(db, "org-1", "wf-1", {})

    def test_trigger_runs_and_completes(self):
        db = _db_with(_wf_row())
        with patch("app.flows.executor.budgets") as bdg:
            bdg.check_and_consume.return_value = ("ok", {})
            result = service.trigger(db, "org-1", "wf-1", {"lead": {"stage": "new"}})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps_executed"], 1)

    def test_preview_prices_before_running(self):
        db = _db_with(_wf_row())
        prev = service.preview(db, "org-1", "wf-1")
        self.assertEqual(prev["steps_upper_bound"], 1)
        self.assertEqual(prev["cost_cents_upper_bound"], 1)

    def test_set_status_rejects_bad_status(self):
        db = MagicMock()
        with self.assertRaises(models.DefinitionError):
            service.set_status(db, "org-1", "wf-1", "exploding")


class McpTests(unittest.TestCase):
    def test_unknown_tool_fails_loud(self):
        db = MagicMock()
        with self.assertRaises(models.DefinitionError):
            mcp.call_tool(db, "org-1", "flows.destroy", {})

    def test_trigger_needs_workflow_id(self):
        db = MagicMock()
        with self.assertRaises(models.DefinitionError):
            mcp.call_tool(db, "org-1", "flows.trigger", {})

    def test_list_delegates(self):
        db = MagicMock()
        db.table.return_value = _table_mock([{"id": "wf-1"}])
        out = mcp.call_tool(db, "org-1", "flows.list", {})
        self.assertIn("workflows", out)

    def test_tools_advertised(self):
        names = [t["name"] for t in mcp.TOOLS]
        self.assertIn("flows.trigger", names)
        self.assertIn("flows.preview", names)


class SchedulerTests(unittest.TestCase):
    def test_cron_due_matches(self):
        now = datetime.datetime(
            2026, 10, 19, 9, 0, tzinfo=datetime.timezone.utc
        )  # Monday
        self.assertTrue(scheduler._cron_due("0 9 * * *", now))
        self.assertFalse(scheduler._cron_due("0 10 * * *", now))
        self.assertFalse(scheduler._cron_due("0 9 * * 99", now))

    def test_tick_fires_due_schedule_once_per_minute(self):
        now = datetime.datetime(2026, 10, 19, 9, 0, tzinfo=datetime.timezone.utc)
        wf = _wf_row(
            definition={
                "name": "Daily",
                "trigger": {"type": "schedule", "cron": "0 9 * * *"},
                "steps": [{"id": "s1", "type": "set", "var": "x", "value": "1"}],
            }
        )
        db = MagicMock()

        def _table(name):
            if name == "flw_workflows":
                return _table_mock([wf])
            if name == "flw_runs":
                return _table_mock([])
            return _table_mock([])

        db.table.side_effect = _table
        with patch("app.flows.scheduler.executor.run_workflow") as rw:
            rw.return_value = {"run_id": "r1", "status": "completed"}
            out = scheduler.tick(db, now)
        self.assertEqual(len(out["fired"]), 1)
        rw.assert_called_once()

    def test_tick_ignores_unvalidatable_cron(self):
        # Definition-time validation rejects ranges (see
        # DefinitionHardeningTests); a legacy row carrying one simply
        # never fires at tick time.
        now = datetime.datetime(2026, 10, 19, 9, 0, tzinfo=datetime.timezone.utc)
        wf = _wf_row(
            definition={
                "name": "Weird",
                "trigger": {"type": "schedule", "cron": "0 9 * * 1-5"},
                "steps": [{"id": "s1", "type": "set", "var": "x", "value": "1"}],
            }
        )
        db = MagicMock()
        db.table.side_effect = lambda n: _table_mock(
            [wf] if n == "flw_workflows" else []
        )
        with patch("app.flows.scheduler.executor.run_workflow") as rw:
            out = scheduler.tick(db, now)
        self.assertEqual(out["fired"], [])
        rw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
