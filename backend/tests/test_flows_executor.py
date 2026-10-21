"""TW-625 Twistor Flows: execution engine (RED first).

Fixtures named for family: amara_workflow, chidi_run.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.flows import executor, models


def _fake_db():
    db = MagicMock()
    tbl = db.table.return_value
    ins = MagicMock()
    ins.execute.return_value = MagicMock(data=[{"id": "row-1"}])
    tbl.insert.return_value = ins
    upd = MagicMock()
    upd.execute.return_value = MagicMock(data=[])
    tbl.update.return_value = upd
    sel = MagicMock()
    sel.execute.return_value = MagicMock(data=[])
    tbl.select.return_value = sel
    return db


def _amara_workflow(steps, trigger=None, budget=None):
    raw = {
        "name": "amara",
        "trigger": trigger or {"type": "manual"},
        "steps": steps,
    }
    if budget:
        raw["budget"] = budget
    return models.validate_definition(raw)


class ConditionTests(unittest.TestCase):
    def test_equality_true(self):
        self.assertTrue(
            executor.eval_condition(
                {"lead": {"replied": False}}, "{{lead.replied}} == false"
            )
        )

    def test_equality_false(self):
        self.assertFalse(
            executor.eval_condition(
                {"lead": {"replied": True}}, "{{lead.replied}} == false"
            )
        )

    def test_string_equality(self):
        self.assertTrue(executor.eval_condition({"s": "engaged"}, "{{s}} == engaged"))

    def test_greater_than(self):
        self.assertTrue(executor.eval_condition({"n": 5}, "{{n}} > 3"))

    def test_malformed_condition_raises(self):
        with self.assertRaises(executor.ConditionError):
            executor.eval_condition({}, "not a condition at all !!!")


class SetStepTests(unittest.TestCase):
    def test_set_writes_context(self):
        db = _fake_db()
        wf = _amara_workflow(
            [{"id": "s1", "type": "set", "var": "lead.stage", "value": "engaged"}]
        )
        ctx = {}
        status, _ = executor.execute_step(db, "org-1", wf, "run-1", wf.steps[0], ctx)
        self.assertEqual(status, "continue")
        self.assertEqual(ctx["lead"]["stage"], "engaged")


class BranchStepTests(unittest.TestCase):
    def _branch(self):
        return _amara_workflow(
            [
                {
                    "id": "s1",
                    "type": "branch",
                    "condition": "{{lead.replied}} == false",
                    "then": [
                        {"id": "s1a", "type": "set", "var": "sent", "value": "touch2"}
                    ],
                    "else": [
                        {"id": "s1b", "type": "set", "var": "sent", "value": "none"}
                    ],
                }
            ]
        )

    def test_branch_takes_then(self):
        db = _fake_db()
        wf = self._branch()
        ctx = {"lead": {"replied": False}}
        status, _ = executor.execute_step(db, "org-1", wf, "run-1", wf.steps[0], ctx)
        self.assertEqual(status, "continue")
        self.assertEqual(ctx["sent"], "touch2")

    def test_branch_takes_else(self):
        db = _fake_db()
        wf = self._branch()
        ctx = {"lead": {"replied": True}}
        executor.execute_step(db, "org-1", wf, "run-1", wf.steps[0], ctx)
        self.assertEqual(ctx["sent"], "none")


class DelayStepTests(unittest.TestCase):
    def test_delay_suspends_run(self):
        db = _fake_db()
        wf = _amara_workflow([{"id": "s1", "type": "delay", "duration": "3d"}])
        status, info = executor.execute_step(db, "org-1", wf, "run-1", wf.steps[0], {})
        self.assertEqual(status, "suspend")
        self.assertIn("resume_at", info)


class LoopCircuitBreakerTests(unittest.TestCase):
    def test_loop_iterates_with_cap(self):
        db = _fake_db()
        wf = _amara_workflow(
            [
                {
                    "id": "s1",
                    "type": "loop",
                    "over": "{{leads}}",
                    "as": "lead",
                    "max_iterations": 3,
                    "steps": [{"id": "s1a", "type": "set", "var": "n", "value": "1"}],
                }
            ]
        )
        ctx = {"leads": [{"e": "a"}, {"e": "b"}, {"e": "c"}, {"e": "d"}, {"e": "e"}]}
        status, info = executor.execute_step(db, "org-1", wf, "run-1", wf.steps[0], ctx)
        self.assertEqual(status, "continue")
        self.assertEqual(info["iterations"], 3)  # capped, not 5

    def test_loop_error_rate_breaker_pauses(self):
        """Over half the iterations erroring breaks the loop and flags pause."""
        db = _fake_db()
        wf = _amara_workflow(
            [
                {
                    "id": "s1",
                    "type": "loop",
                    "over": "{{leads}}",
                    "as": "lead",
                    "max_iterations": 10,
                    # {{lead.nope}} resolves against no context path -> every
                    # iteration errors at render time, tripping the breaker
                    "steps": [
                        {
                            "id": "s1a",
                            "type": "notify",
                            "channel": "email",
                            "to": "{{lead.nope}}",
                        }
                    ],
                }
            ]
        )
        ctx = {"leads": [{"e": "a"}, {"e": "b"}, {"e": "c"}, {"e": "d"}]}
        status, info = executor.execute_step(db, "org-1", wf, "run-1", wf.steps[0], ctx)
        self.assertEqual(status, "halt")
        self.assertTrue(info.get("circuit_broken"))


class NotifyStepTests(unittest.TestCase):
    def test_notify_step_calls_notify_service(self):
        db = _fake_db()
        wf = _amara_workflow(
            [
                {
                    "id": "s1",
                    "type": "notify",
                    "channel": "email",
                    "to": "{{lead.email}}",
                    "template": "touch1",
                    "variables": {"shop": "{{lead.shop}}"},
                }
            ]
        )
        ctx = {"lead": {"email": "owner@shop.com", "shop": "Acme HVAC"}}
        with patch("app.flows.executor.notify_service") as nsvc:
            nsvc.send.return_value = {"ok": True, "message_id": "m-1"}
            status, info = executor.execute_step(
                db, "org-1", wf, "run-1", wf.steps[0], ctx
            )
        self.assertEqual(status, "continue")
        nsvc.send.assert_called_once()
        kwargs = nsvc.send.call_args.kwargs
        self.assertEqual(kwargs["to"], "owner@shop.com")
        self.assertEqual(kwargs["variables"]["shop"], "Acme HVAC")


class RunBudgetEnforcementTests(unittest.TestCase):
    def test_run_halts_when_step_cap_exceeded(self):
        """The Zapier runaway-bill wedge: per-run step cap halts + pauses."""
        db = _fake_db()
        steps = [
            {"id": f"s{i}", "type": "set", "var": "x", "value": str(i)}
            for i in range(10)
        ]
        wf = _amara_workflow(
            steps,
            budget={
                "max_steps_per_run": 3,
                "max_runs_per_day": 500,
                "max_cost_per_day_cents": 1000,
            },
        )
        with patch("app.flows.executor.budgets") as bdg:
            bdg.check_and_consume.return_value = ("ok", {})
            result = executor.run_workflow(
                db, "org-1", {"id": "wf-1", "definition": wf.model_dump()}, {}
            )
        self.assertEqual(result["status"], "halted")
        self.assertTrue(result["steps_executed"] <= 3)
        self.assertTrue(result.get("budget_paused"))


if __name__ == "__main__":
    unittest.main()
