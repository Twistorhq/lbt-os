"""TW-620 Twistor Notify: least-cost routing + failover tests (RED first)."""
import unittest
from dataclasses import dataclass

from app.notify import providers, routing


def _cfg(name, channel, cost, priority=100, enabled=True, fail=False):
    p = providers.NullProvider()
    p.name = name
    p.channel = channel
    if fail:
        orig = p.send

        def _fail(**kwargs):
            return providers.ProviderResult(
                ok=False, error="provider down", cost_cents=0, latency_ms=1
            )

        p.send = _fail
    return routing.ProviderConfig(
        name=name,
        channel=channel,
        provider=p,
        cost_cents=cost,
        priority=priority,
        enabled=enabled,
    )


class LeastCostRoutingTests(unittest.TestCase):
    def test_cheapest_enabled_provider_first(self):
        configs = [
            _cfg("pricey", "email", 10),
            _cfg("cheap", "email", 1),
            _cfg("mid", "email", 5),
        ]
        ordered = routing.order_providers(configs, "email")
        self.assertEqual([c.name for c in ordered], ["cheap", "mid", "pricey"])

    def test_disabled_providers_excluded(self):
        configs = [_cfg("cheap", "email", 1, enabled=False), _cfg("ok", "email", 5)]
        ordered = routing.order_providers(configs, "email")
        self.assertEqual([c.name for c in ordered], ["ok"])

    def test_channel_filtering(self):
        configs = [_cfg("sms-one", "sms", 1), _cfg("email-one", "email", 1)]
        ordered = routing.order_providers(configs, "email")
        self.assertEqual([c.name for c in ordered], ["email-one"])

    def test_priority_breaks_cost_ties(self):
        configs = [_cfg("slow", "email", 1, priority=200), _cfg("fast", "email", 1, priority=50)]
        ordered = routing.order_providers(configs, "email")
        self.assertEqual(ordered[0].name, "fast")

    def test_no_providers_for_channel_raises(self):
        with self.assertRaises(routing.NoProviderAvailable):
            routing.order_providers([_cfg("sms-one", "sms", 1)], "email")


class FailoverTests(unittest.TestCase):
    def test_failover_walks_to_next_provider(self):
        configs = [_cfg("primary", "email", 1, fail=True), _cfg("backup", "email", 5)]
        result, attempts = routing.route_send(
            configs, "email", to="a@b.c", subject="s", body="b", metadata={}
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[0].ok)
        self.assertEqual(attempts[0].provider, "primary")
        self.assertTrue(attempts[1].ok)
        self.assertEqual(attempts[1].provider, "backup")

    def test_all_providers_down_returns_failure_with_trail(self):
        configs = [
            _cfg("one", "email", 1, fail=True),
            _cfg("two", "email", 5, fail=True),
        ]
        result, attempts = routing.route_send(
            configs, "email", to="a@b.c", subject="s", body="b", metadata={}
        )
        self.assertFalse(result.ok)
        self.assertEqual(len(attempts), 2)
        self.assertIn("provider down", result.error)

    def test_first_success_stops_walk(self):
        configs = [_cfg("primary", "email", 1), _cfg("backup", "email", 5)]
        result, attempts = routing.route_send(
            configs, "email", to="a@b.c", subject="s", body="b", metadata={}
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
