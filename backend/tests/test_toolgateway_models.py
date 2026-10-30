"""TW-800 Twistor Trades agent tool gateway - policy model behavior.

The policy is plugbear's shape (ordered rules, first match wins,
allow / ask / deny, default deny) composed with the TW-768 class
vocabulary. These tests pin the fail-closed posture: an unbound or
unresolvable call must never drift into a pass.
"""

import pytest

from app.toolgateway import models, service
from app.toolgateway.models import GatewayError, ToolRule


class TestDefaultRules:
    def test_notify_send_binds_email_by_channel(self):
        p = models.resolve_policy("notify.send", {"channel": "email"})
        assert p.mode == "allow"
        assert p.action_class == "email.send"

    def test_notify_send_binds_sms_by_channel(self):
        p = models.resolve_policy("notify.send", {"channel": "sms"})
        assert p.action_class == "sms.send"

    def test_notify_send_unknown_channel_denies_with_bind_error(self):
        p = models.resolve_policy("notify.send", {"channel": "pigeon"})
        assert p.mode == "deny"
        assert p.bind_error is not None

    def test_notify_send_missing_channel_denies(self):
        p = models.resolve_policy("notify.send", {})
        assert p.mode == "deny"
        assert p.bind_error is not None

    def test_flows_trigger_asks_unbound(self):
        p = models.resolve_policy("flows.trigger", {"workflow_id": "w1"})
        assert p.mode == "ask"
        assert p.action_class is None

    def test_flows_reads_allow_unbound(self):
        for tool in ("flows.list", "flows.run_status", "flows.preview"):
            p = models.resolve_policy(tool, {})
            assert p.mode == "allow", tool
            assert p.action_class is None

    def test_unknown_tool_hits_the_deny_default(self):
        p = models.resolve_policy("shell.exec", {})
        assert p.mode == "deny"
        assert p.rule_index is None

    def test_args_fingerprint_is_stable_and_value_free(self):
        h1, keys1 = service.args_fingerprint({"b": 2, "a": 1})
        h2, keys2 = service.args_fingerprint({"a": 1, "b": 2})
        assert h1 == h2
        assert keys1 == keys2 == ["a", "b"]


class TestRuleConstruction:
    def test_unknown_mode_rejected(self):
        with pytest.raises(GatewayError):
            ToolRule(tool="x.y", mode="maybe")

    def test_unknown_fixed_class_rejected(self):
        with pytest.raises(GatewayError):
            ToolRule(tool="x.y", mode="allow", action_class="world.domination")

    def test_both_binding_shapes_rejected(self):
        with pytest.raises(GatewayError):
            ToolRule(
                tool="x.y",
                mode="allow",
                action_class="email.send",
                class_by_arg="channel",
                class_map={"email": "email.send"},
            )

    def test_class_map_without_arg_rejected(self):
        with pytest.raises(GatewayError):
            ToolRule(tool="x.y", mode="allow", class_map={"email": "email.send"})

    def test_unknown_class_in_map_rejected(self):
        with pytest.raises(GatewayError):
            ToolRule(
                tool="x.y",
                mode="ask",
                class_by_arg="channel",
                class_map={"email": "email.blast"},
            )


class TestFirstMatchWins:
    RULES = (
        ToolRule(tool="flows.trigger", mode="ask"),
        ToolRule(tool="flows.*", mode="allow"),
        ToolRule(tool="*", mode="deny"),
    )

    def test_specific_rule_beats_glob(self):
        p = models.resolve_policy("flows.trigger", {}, self.RULES)
        assert p.mode == "ask" and p.rule_index == 0

    def test_glob_catches_the_rest_of_the_family(self):
        p = models.resolve_policy("flows.list", {}, self.RULES)
        assert p.mode == "allow" and p.rule_index == 1

    def test_catch_all_denies_everything_else(self):
        p = models.resolve_policy("anything.else", {}, self.RULES)
        assert p.mode == "deny" and p.rule_index == 2
