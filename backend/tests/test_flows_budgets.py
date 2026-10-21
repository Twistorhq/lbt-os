"""TW-625 Twistor Flows: task budgets — 80% warn, 100% shutoff (RED first)."""

import unittest
from unittest.mock import MagicMock, patch

from app.flows import budgets, models


def _fake_db(counter_rows=None):
    """Supabase-shaped mock. counter_rows: list of today's counter dicts."""
    db = MagicMock()
    tbl = db.table.return_value

    sel = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=counter_rows or [])
    # support .select().eq().eq().execute() chaining
    sel.eq.return_value = chain
    chain.eq.return_value = chain
    tbl.select.return_value = sel

    ins = MagicMock()
    ins.execute.return_value = MagicMock(data=[{"id": "c-1"}])
    tbl.insert.return_value = ins
    tbl.upsert.return_value = ins

    upd = MagicMock()
    upd.execute.return_value = MagicMock(data=[])
    tbl.update.return_value = upd
    return db


def _budget(**kw):
    base = {
        "max_steps_per_run": 100,
        "max_runs_per_day": 10,
        "max_cost_per_day_cents": 100,
    }
    base.update(kw)
    return models.Budget(**base)


class BudgetWarnTests(unittest.TestCase):
    def test_under_80_percent_is_ok(self):
        db = _fake_db([{"runs": 2, "steps": 5, "cost_cents": 10, "warned_80": False}])
        verdict, _ = budgets.check_and_consume(
            db, "org-1", "wf-1", "Touch 1", _budget(), cost_cents=1, steps=1
        )
        self.assertEqual(verdict, "ok")

    def test_crossing_80_percent_warns_once(self):
        db = _fake_db([{"runs": 8, "steps": 5, "cost_cents": 10, "warned_80": False}])
        with patch("app.flows.budgets.notify_service") as nsvc:
            verdict, info = budgets.check_and_consume(
                db,
                "org-1",
                "wf-1",
                "Touch 1",
                _budget(),
                cost_cents=1,
                steps=1,
                alert_to="owner@shop.com",
            )
        self.assertEqual(verdict, "warned")
        nsvc.send.assert_called_once()
        kwargs = nsvc.send.call_args.kwargs
        self.assertEqual(kwargs["priority"], "system")
        self.assertEqual(kwargs["to"], "owner@shop.com")
        self.assertTrue(info["warned"])
        self.assertTrue(info["emailed"])

    def test_warning_without_alert_to_logs_loudly(self):
        db = _fake_db([{"runs": 8, "steps": 5, "cost_cents": 10, "warned_80": False}])
        with patch("app.flows.budgets.notify_service") as nsvc:
            verdict, info = budgets.check_and_consume(
                db,
                "org-1",
                "wf-1",
                "Touch 1",
                _budget(),
                cost_cents=1,
                steps=1,
                alert_to=None,
            )
        self.assertEqual(verdict, "warned")
        nsvc.send.assert_not_called()
        self.assertTrue(info["warned"])
        self.assertFalse(info["emailed"])

    def test_warning_not_repeated(self):
        db = _fake_db([{"runs": 8, "steps": 5, "cost_cents": 10, "warned_80": True}])
        with patch("app.flows.budgets.notify_service") as nsvc:
            verdict, _ = budgets.check_and_consume(
                db, "org-1", "wf-1", "Touch 1", _budget(), cost_cents=1, steps=1
            )
        self.assertEqual(verdict, "ok")
        nsvc.send.assert_not_called()


class BudgetShutoffTests(unittest.TestCase):
    def test_at_100_percent_blocks_and_pauses(self):
        db = _fake_db([{"runs": 10, "steps": 5, "cost_cents": 10, "warned_80": True}])
        verdict, info = budgets.check_and_consume(
            db, "org-1", "wf-1", "Touch 1", _budget(), cost_cents=1, steps=1
        )
        self.assertEqual(verdict, "blocked")
        self.assertTrue(info["workflow_paused"])
        # the pause write went out
        db.table.assert_any_call("flw_workflows")

    def test_cost_cap_blocks(self):
        db = _fake_db([{"runs": 1, "steps": 5, "cost_cents": 99, "warned_80": True}])
        verdict, _ = budgets.check_and_consume(
            db, "org-1", "wf-1", "Touch 1", _budget(), cost_cents=5, steps=1
        )
        self.assertEqual(verdict, "blocked")


if __name__ == "__main__":
    unittest.main()
