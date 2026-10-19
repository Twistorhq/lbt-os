"""TW-620 Twistor Notify: delivery analytics + silent-failure debugger (RED first)."""

import unittest
from unittest.mock import MagicMock

from app.notify import analytics


def _fake_db(messages, attempts):
    db = MagicMock()

    def _table(name):
        tbl = MagicMock()
        q = MagicMock()
        # chainable query builder returning itself
        q.select.return_value = q
        q.eq.return_value = q
        q.order.return_value = q
        single = MagicMock()
        if name == "ntf_messages":
            q.execute.return_value = MagicMock(data=messages)
            single.execute.return_value = MagicMock(
                data=messages[0] if messages else None
            )
        else:
            q.execute.return_value = MagicMock(data=attempts)
            single.execute.return_value = MagicMock(data=None)
        q.maybe_single.return_value = single
        tbl.select.return_value = q
        tbl.eq.return_value = q
        return tbl

    db.table.side_effect = _table
    return db


class DeliveryRatesTests(unittest.TestCase):
    def test_rates_per_channel(self):
        db = _fake_db(
            [
                {"channel": "email", "status": "sent", "cost_actual_cents": 1},
                {"channel": "email", "status": "sent", "cost_actual_cents": 1},
                {"channel": "email", "status": "failed", "cost_actual_cents": 0},
                {"channel": "sms", "status": "sent", "cost_actual_cents": 2},
            ],
            [],
        )
        out = analytics.delivery_rates(db, "org-1")
        email = out["channels"]["email"]
        self.assertEqual(email["sent"], 2)
        self.assertEqual(email["failed"], 1)
        self.assertAlmostEqual(email["delivery_rate"], 2 / 3, places=4)
        self.assertEqual(email["spend_cents"], 2)
        self.assertEqual(out["channels"]["sms"]["delivery_rate"], 1.0)

    def test_empty_ledger_returns_empty_channels(self):
        db = _fake_db([], [])
        out = analytics.delivery_rates(db, "org-1")
        self.assertEqual(out["channels"], {})


class DebugMessageTests(unittest.TestCase):
    def test_debugger_returns_message_and_attempt_trail(self):
        db = _fake_db(
            [
                {
                    "id": "m-1",
                    "channel": "email",
                    "workflow": "outreach_touch_1",
                    "status": "failed",
                    "recipient_hash": "sha256:abc",
                    "recipient": "user@example.com",
                    "provider_used": "backup",
                    "cost_preview_cents": 1,
                    "cost_actual_cents": 0,
                    "created_at": "2026-10-19",
                }
            ],
            [
                {
                    "provider": "primary",
                    "attempt_no": 1,
                    "status": "failed",
                    "error": "down",
                    "latency_ms": 12,
                    "created_at": "2026-10-19",
                },
                {
                    "provider": "backup",
                    "attempt_no": 2,
                    "status": "failed",
                    "error": "down",
                    "latency_ms": 9,
                    "created_at": "2026-10-19",
                },
            ],
        )
        out = analytics.debug_message(db, "org-1", "m-1")
        self.assertTrue(out["found"])
        self.assertEqual(out["message"]["id"], "m-1")
        self.assertEqual(out["message"]["recipient_hash"], "sha256:abc")
        # raw recipient never leaks through the debugger payload
        self.assertNotIn("recipient", out["message"])
        self.assertEqual(len(out["attempts"]), 2)
        self.assertEqual(out["attempts"][0]["provider"], "primary")

    def test_debugger_not_found(self):
        db = _fake_db([], [])
        out = analytics.debug_message(db, "org-1", "nope")
        self.assertFalse(out["found"])


if __name__ == "__main__":
    unittest.main()
