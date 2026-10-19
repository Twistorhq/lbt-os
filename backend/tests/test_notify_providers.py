"""TW-620 Twistor Notify: provider interface conformance tests (RED first)."""

import unittest

from app.notify import providers


class ProviderInterfaceTests(unittest.TestCase):
    def test_all_adapters_expose_name_channel_and_cost(self):
        for p in [
            providers.NullProvider(),
            providers.ResendEmailProvider(api_key="test-key"),
            providers.TwilioSmsProvider(
                account_sid="sid", auth_token="tok", from_number="+15550001111"
            ),
        ]:
            self.assertTrue(p.name)
            self.assertIn(p.channel, ("email", "sms", "push"))
            self.assertIsInstance(p.cost_cents_per_unit(), int)
            self.assertGreaterEqual(p.cost_cents_per_unit(), 0)

    def test_null_provider_always_succeeds_without_network(self):
        p = providers.NullProvider()
        result = p.send(
            to="user@example.com",
            subject="hi",
            body="hello",
            metadata={},
        )
        self.assertTrue(result.ok)
        self.assertIsNone(result.error)
        self.assertEqual(result.cost_cents, 0)

    def test_push_stub_refuses_loudly_until_configured(self):
        p = providers.PushProviderStub()
        with self.assertRaises(NotImplementedError):
            p.send(to="device-token", subject=None, body="hi", metadata={})

    def test_resend_provider_refuses_without_api_key(self):
        p = providers.ResendEmailProvider(api_key=None)
        result = p.send(to="user@example.com", subject="hi", body="hello", metadata={})
        self.assertFalse(result.ok)
        self.assertIn("api key", result.error.lower())

    def test_twilio_provider_refuses_without_credentials(self):
        p = providers.TwilioSmsProvider(
            account_sid=None, auth_token=None, from_number=None
        )
        result = p.send(to="+15550001111", subject=None, body="hi", metadata={})
        self.assertFalse(result.ok)
        self.assertIn("credential", result.error.lower())

    def test_result_carries_latency(self):
        p = providers.NullProvider()
        result = p.send(to="a@b.c", subject="s", body="b", metadata={})
        self.assertGreaterEqual(result.latency_ms, 0)


if __name__ == "__main__":
    unittest.main()
