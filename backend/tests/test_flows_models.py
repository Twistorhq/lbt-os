"""TW-625 Twistor Flows: workflow definition validation (RED first)."""

import unittest

from app.flows import models


def _minimal(trigger=None):
    return {
        "name": "Touch 1",
        "trigger": trigger or {"type": "manual"},
        "steps": [
            {"id": "s1", "type": "set", "var": "x", "value": "1"},
        ],
    }


class DefinitionValidationTests(unittest.TestCase):
    def test_minimal_definition_validates(self):
        d = models.validate_definition(_minimal())
        self.assertEqual(d.name, "Touch 1")
        self.assertEqual(d.trigger.type, "manual")

    def test_unknown_trigger_type_rejected(self):
        bad = _minimal({"type": "telepathy"})
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(bad)

    def test_webhook_trigger_requires_key(self):
        bad = _minimal({"type": "webhook"})
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(bad)

    def test_schedule_trigger_requires_cron(self):
        bad = _minimal({"type": "schedule"})
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(bad)

    def test_unknown_step_type_rejected(self):
        bad = _minimal()
        bad["steps"] = [{"id": "s1", "type": "teleport"}]
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(bad)

    def test_duplicate_step_ids_rejected(self):
        bad = _minimal()
        bad["steps"] = [
            {"id": "s1", "type": "set", "var": "x", "value": "1"},
            {"id": "s1", "type": "set", "var": "y", "value": "2"},
        ]
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(bad)

    def test_notify_step_requires_channel_and_to(self):
        bad = _minimal()
        bad["steps"] = [{"id": "s1", "type": "notify", "channel": "email"}]
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(bad)

    def test_branch_requires_condition_and_branches(self):
        bad = _minimal()
        bad["steps"] = [{"id": "s1", "type": "branch"}]
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(bad)

    def test_loop_requires_over_and_steps(self):
        bad = _minimal()
        bad["steps"] = [{"id": "s1", "type": "loop", "as": "lead"}]
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(bad)

    def test_default_budget_applied(self):
        d = models.validate_definition(_minimal())
        self.assertEqual(d.budget.max_steps_per_run, 100)
        self.assertGreater(d.budget.max_runs_per_day, 0)

    def test_custom_budget_kept(self):
        raw = _minimal()
        raw["budget"] = {
            "max_steps_per_run": 10,
            "max_runs_per_day": 5,
            "max_cost_per_day_cents": 50,
        }
        d = models.validate_definition(raw)
        self.assertEqual(d.budget.max_steps_per_run, 10)


if __name__ == "__main__":
    unittest.main()
