"""TW-620 Twistor Notify: send orchestration, cost preview, templates (RED first)."""
import unittest
from unittest.mock import MagicMock, patch

from app.notify import providers, routing, service, templates


def _fake_db():
    """Supabase-shaped mock; insert returns the row with a fake id."""
    db = MagicMock()

    def _fake_insert(row):
        r = MagicMock()
        saved = dict(row)
        saved.setdefault("id", "msg-test-1")
        r.data = [saved]
        ex = MagicMock()
        ex.execute.return_value = r
        return ex

    tbl.insert.side_effect = _fake_insert
    sel = MagicMock()
    sel.execute.return_value = MagicMock(data=[])
    tbl.select.return_value = sel
    return db


def _null_config(name="null-mail", channel="email", cost=0):
    return routing.ProviderConfig(
        name=name,
        channel=channel,
        provider=providers.NullProvider(),
        cost_cents=cost,
        priority=100,
        enabled=True,
    )


class CostPreviewTests(unittest.TestCase):
    def test_preview_shows_cost_before_send(self):
        preview = service.preview_cost(
            [_null_config(cost=3)], "email"
        )
        self.assertEqual(preview["provider"], "null-mail")
        self.assertEqual(preview["cost_cents"], 3)
        self.assertEqual(preview["channel"], "email")

    def test_preview_with_no_providers_raises(self):
        with self.assertRaises(routing.NoProviderAvailable):
            service.preview_cost([], "email")


class TemplateTests(unittest.TestCase):
    def test_variable_substitution(self):
        out = templates.render_template(
            "Hi {{name}}, your code is {{code}}.", {"name": "Jo", "code": "123"}
        )
        self.assertEqual(out, "Hi Jo, your code is 123.")

    def test_missing_variable_fails_loud(self):
        with self.assertRaises(templates.TemplateError):
            templates.render_template("Hi {{name}}", {})

    def test_no_variables_passes_through(self):
        self.assertEqual(templates.render_template("plain", {}), "plain")


class SendOrchestrationTests(unittest.TestCase):
    def test_send_records_message_and_attempt(self):
        db = _fake_db()
        with patch.object(
            service.toll_service, "record_toll_event", return_value={"id": "toll-1"}
        ) as toll:
            msg = service.send(
                db,
                "org-1",
                channel="email",
                to="user@example.com",
                subject="Hello {{name}}",
                body="Body {{name}}",
                variables={"name": "Jo"},
                workflow="outreach_touch_1",
                provider_configs=[_null_config()],
            )
        self.assertEqual(msg["status"], "sent")
        self.assertEqual(msg["provider_used"], "null-mail")
        self.assertEqual(msg["cost_preview_cents"], 0)
        # recipient stored, hash logged-safe
        self.assertEqual(msg["recipient"], "user@example.com")
        self.assertTrue(msg["recipient_hash"].startswith("sha256:"))
        toll.assert_called_once()
        self.assertEqual(
            toll.call_args[1]["idempotency_key"], "notify:msg-test-1"
        )

    def test_send_marks_failed_when_all_providers_down(self):
        db = _fake_db()
        cfg = _null_config()
        orig = cfg.provider.send
        cfg.provider.send = lambda **kw: providers.ProviderResult(
            ok=False, error="down", cost_cents=0, latency_ms=1
        )
        with patch.object(
            service.toll_service, "record_toll_event", return_value={"id": "toll-1"}
        ):
            msg = service.send(
                db,
                "org-1",
                channel="email",
                to="user@example.com",
                subject="s",
                body="b",
                provider_configs=[cfg],
            )
        self.assertEqual(msg["status"], "failed")

    def test_toll_emission_failure_does_not_fail_send(self):
        """TW-208: a toll-ledger failure must not fail the notification."""
        db = _fake_db()
        with patch.object(
            service.toll_service,
            "record_toll_event",
            side_effect=RuntimeError("ledger down"),
        ):
            msg = service.send(
                db,
                "org-1",
                channel="email",
                to="user@example.com",
                subject="s",
                body="b",
                provider_configs=[_null_config()],
            )
        self.assertEqual(msg["status"], "sent")
        self.assertIsNone(msg["toll_event_id"])


if __name__ == "__main__":
    unittest.main()
