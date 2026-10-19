"""TW-625 Twistor Flows: Zapier-concept importer (RED first)."""

import unittest

from app.flows import migration


class ZapierImportTests(unittest.TestCase):
    def test_email_action_becomes_notify(self):
        zap = {
            "trigger": {"app": "webhook", "event": "catch"},
            "actions": [
                {
                    "app": "email",
                    "action": "send",
                    "to": "owner@shop.com",
                    "subject": "hi",
                    "body": "hey",
                },
            ],
        }
        flow = migration.from_zapier(zap)
        self.assertEqual(flow["trigger"]["type"], "webhook")
        step = flow["steps"][0]
        self.assertEqual(step["type"], "notify")
        self.assertEqual(step["channel"], "email")
        self.assertEqual(step["to"], "owner@shop.com")

    def test_delay_action_becomes_delay(self):
        zap = {
            "trigger": {"app": "schedule", "event": "daily"},
            "actions": [{"app": "delay", "action": "for", "duration": "3d"}],
        }
        flow = migration.from_zapier(zap)
        self.assertEqual(flow["steps"][0]["type"], "delay")

    def test_filter_action_becomes_branch(self):
        zap = {
            "trigger": {"app": "webhook", "event": "catch"},
            "actions": [
                {
                    "app": "filter",
                    "action": "only_continue",
                    "field": "replied",
                    "op": "equals",
                    "value": "false",
                }
            ],
        }
        flow = migration.from_zapier(zap)
        self.assertEqual(flow["steps"][0]["type"], "branch")

    def test_code_action_refused_loudly(self):
        zap = {
            "trigger": {"app": "webhook", "event": "catch"},
            "actions": [{"app": "code", "action": "run_python", "code": "print('hi')"}],
        }
        with self.assertRaises(migration.UnsupportedStepError):
            migration.from_zapier(zap)

    def test_unknown_app_refused_loudly(self):
        zap = {
            "trigger": {"app": "webhook", "event": "catch"},
            "actions": [{"app": "mysterybox", "action": "do"}],
        }
        with self.assertRaises(migration.UnsupportedStepError):
            migration.from_zapier(zap)

    def test_imported_flow_validates(self):
        from app.flows import models

        zap = {
            "trigger": {"app": "webhook", "event": "catch"},
            "actions": [
                {
                    "app": "email",
                    "action": "send",
                    "to": "{{lead.email}}",
                    "subject": "s",
                    "body": "b",
                },
                {"app": "delay", "action": "for", "duration": "3d"},
            ],
        }
        flow = migration.from_zapier(zap)
        flow["name"] = "imported"
        models.validate_definition(flow)  # must not raise


if __name__ == "__main__":
    unittest.main()
