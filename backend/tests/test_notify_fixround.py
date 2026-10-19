"""TW-620 Rosa fix round: prove-it tests for the two majors (RED first)."""
import unittest
from unittest.mock import MagicMock, patch

from app.notify import legacy, providers, routing, service


def _fake_db():
    db = MagicMock()

    def _fake_insert(row):
        saved = dict(row)
        saved.setdefault("id", "msg-test-1")
        r = MagicMock()
        r.data = [saved]
        ex = MagicMock()
        ex.execute.return_value = r
        return ex

    tbl = db.table.return_value
    tbl.insert.side_effect = _fake_insert
    sel = MagicMock()
    sel.execute.return_value = MagicMock(data=[])
    tbl.select.return_value = sel
    return db


class FromAddrTests(unittest.TestCase):
    def test_legacy_send_email_threads_from_addr(self):
        """Major 1: from_addr must reach the provider, not be dropped."""
        captured = {}

        class SpyProvider(providers.Provider):
            name = "spy"
            channel = "email"

            def cost_cents_per_unit(self):
                return 0

            def send(self, *, to, subject, body, metadata):
                captured.update(metadata)
                return providers.ProviderResult(ok=True, cost_cents=0)

        cfg = routing.ProviderConfig(
            name="spy", channel="email", provider=SpyProvider(),
            cost_cents=0, enabled=True,
        )
        with patch.object(
            legacy, "_pinned_config", return_value=cfg
        ), patch.object(
            service.toll_service, "record_toll_event", return_value={"id": "t-1"}
        ):
            legacy.send_email(
                _fake_db(), "org-1", to="a@b.c", subject="s", html="b",
                from_addr="Shop <shop@example.com>",
            )
        self.assertEqual(captured.get("from_addr"), "Shop <shop@example.com>")


class ErrorScrubTests(unittest.TestCase):
    def test_attempt_trail_scrubs_phone_numbers(self):
        """Major 2: Twilio-style errors echoing the number must be scrubbed."""

        class LoudProvider(providers.Provider):
            name = "loud"
            channel = "sms"

            def cost_cents_per_unit(self):
                return 0

            def send(self, *, to, subject, body, metadata):
                return providers.ProviderResult(
                    ok=False,
                    error="Twilio error 21211: invalid To number +15551234567",
                    cost_cents=0,
                )

        cfg = routing.ProviderConfig(
            name="loud", channel="sms", provider=LoudProvider(),
            cost_cents=0, enabled=True,
        )
        _result, attempts = routing.route_send(
            [cfg], "sms", to="+15551234567", subject=None, body="b", metadata={}
        )
        self.assertNotIn("+15551234567", attempts[0].error)
        self.assertNotIn("15551234567", attempts[0].error)
        self.assertIn("[redacted]", attempts[0].error)

    def test_attempt_trail_scrubs_email_addresses(self):
        err = routing._sanitize_error("SMTP 550: mailbox user@example.com not found")
        self.assertNotIn("user@example.com", err)
        self.assertIn("[redacted]", err)


class SystemTemplateSeedTests(unittest.TestCase):
    def test_rate_limit_warning_template_seeded_idempotently(self):
        """Nit 1: the design-promised rate_limit_warning template exists."""
        db = _fake_db()
        # select returns [] -> not present -> insert runs
        service.ensure_system_templates(db, "org-1")
        inserted = db.table.return_value.insert.call_args[0][0]
        self.assertEqual(inserted["name"], "rate_limit_warning")
        # second call with the row present -> no insert
        db2 = MagicMock()
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[{"id": "t-1"}])
        db2.table.return_value.select.return_value = sel
        service.ensure_system_templates(db2, "org-1")
        db2.table.return_value.insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
