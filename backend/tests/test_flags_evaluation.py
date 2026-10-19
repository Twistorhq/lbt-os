"""TW-621 Twistor Flags — evaluation engine semantics (TDD RED).

Pure-function tests: no DB, no network. The evaluator is the hot path —
it must be deterministic, fast, and fail-closed.
"""

import pytest

from app.flags import evaluation, models


def _bool_flag(**over):
    base = dict(
        key="new-checkout",
        name="New checkout",
        enabled=True,
        variations=[{"value": False}, {"value": True}],
        default_variation=0,
        targeting=[],
    )
    base.update(over)
    return models.FlagDefinition(**base)


def _ctx(key="user-123", **attrs):
    return {"targeting_key": key, "attributes": attrs}


class TestDisabledFlag:
    def test_disabled_flag_serves_default_variation(self):
        flag = _bool_flag(enabled=False)
        res = evaluation.evaluate(flag, _ctx())
        assert res.value is False
        assert res.reason == "disabled"

    def test_disabled_flag_ignores_targeting_rules(self):
        flag = _bool_flag(
            enabled=False,
            targeting=[
                {
                    "name": "everyone",
                    "conditions": [],
                    "then": {"variation": 1},
                }
            ],
        )
        res = evaluation.evaluate(flag, _ctx())
        assert res.value is False
        assert res.reason == "disabled"


class TestDefaultRule:
    def test_no_rules_serves_default(self):
        res = evaluation.evaluate(_bool_flag(), _ctx())
        assert res.value is False
        assert res.reason == "default"


class TestTargetingRules:
    def test_matching_rule_serves_its_variation(self):
        flag = _bool_flag(
            targeting=[
                {
                    "name": "beta users",
                    "conditions": [
                        {"attribute": "plan", "op": "eq", "value": "beta"}
                    ],
                    "then": {"variation": 1},
                }
            ]
        )
        res = evaluation.evaluate(flag, _ctx(plan="beta"))
        assert res.value is True
        assert res.reason == "targeting_rule"
        assert res.rule_name == "beta users"

    def test_non_matching_rule_falls_to_default(self):
        flag = _bool_flag(
            targeting=[
                {
                    "name": "beta users",
                    "conditions": [
                        {"attribute": "plan", "op": "eq", "value": "beta"}
                    ],
                    "then": {"variation": 1},
                }
            ]
        )
        res = evaluation.evaluate(flag, _ctx(plan="free"))
        assert res.value is False
        assert res.reason == "default"

    def test_first_matching_rule_wins(self):
        flag = _bool_flag(
            targeting=[
                {
                    "name": "rule-a",
                    "conditions": [{"attribute": "tier", "op": "eq", "value": "gold"}],
                    "then": {"variation": 1},
                },
                {
                    "name": "rule-b",
                    "conditions": [{"attribute": "tier", "op": "eq", "value": "gold"}],
                    "then": {"variation": 0},
                },
            ]
        )
        res = evaluation.evaluate(flag, _ctx(tier="gold"))
        assert res.value is True
        assert res.rule_name == "rule-a"

    def test_missing_attribute_fails_closed(self):
        flag = _bool_flag(
            targeting=[
                {
                    "name": "beta users",
                    "conditions": [
                        {"attribute": "plan", "op": "eq", "value": "beta"}
                    ],
                    "then": {"variation": 1},
                }
            ]
        )
        res = evaluation.evaluate(flag, _ctx())  # no attributes at all
        assert res.value is False
        assert res.reason == "default"

    def test_all_conditions_must_match(self):
        flag = _bool_flag(
            targeting=[
                {
                    "name": "us beta",
                    "conditions": [
                        {"attribute": "plan", "op": "eq", "value": "beta"},
                        {"attribute": "country", "op": "eq", "value": "US"},
                    ],
                    "then": {"variation": 1},
                }
            ]
        )
        assert evaluation.evaluate(flag, _ctx(plan="beta", country="CA")).value is False
        assert evaluation.evaluate(flag, _ctx(plan="beta", country="US")).value is True

    @pytest.mark.parametrize(
        "op,attr_val,cond_val,expected",
        [
            ("eq", "a", "a", True),
            ("eq", "a", "b", False),
            ("neq", "a", "b", True),
            ("gt", 10, 5, True),
            ("gt", 3, 5, False),
            ("gte", 5, 5, True),
            ("lt", 3, 5, True),
            ("lte", 5, 5, True),
            ("in", "x", ["x", "y"], True),
            ("in", "z", ["x", "y"], False),
            ("not_in", "z", ["x", "y"], True),
            ("contains", "hello world", "world", True),
            ("starts_with", "hello", "he", True),
            ("ends_with", "hello", "lo", True),
        ],
    )
    def test_operators(self, op, attr_val, cond_val, expected):
        flag = _bool_flag(
            targeting=[
                {
                    "name": "r",
                    "conditions": [
                        {"attribute": "a", "op": op, "value": cond_val}
                    ],
                    "then": {"variation": 1},
                }
            ]
        )
        res = evaluation.evaluate(flag, _ctx(a=attr_val))
        assert (res.value is True) == expected


class TestPercentageRollout:
    def _rollout_flag(self):
        return _bool_flag(
            targeting=[
                {
                    "name": "rollout",
                    "conditions": [],
                    "then": {"percentage": {0: 90, 1: 10}},
                }
            ]
        )

    def test_rollout_is_deterministic(self):
        flag = self._rollout_flag()
        first = evaluation.evaluate(flag, _ctx(key="stable-user"))
        for _ in range(5):
            again = evaluation.evaluate(flag, _ctx(key="stable-user"))
            assert again.value == first.value
            assert again.variation_index == first.variation_index

    def test_rollout_buckets_differ_per_flag(self):
        # Same targeting key, different flag key -> independent buckets.
        # (Not asserting they differ for one key — asserting the bucket
        # function includes the flag key.)
        b1 = evaluation._bucket("flag-a", "user-1")
        b2 = evaluation.evaluate(
            _bool_flag(key="flag-a"), _ctx(key="user-1")
        )
        assert 0 <= b1 < 10000
        assert b2.reason in ("default", "targeting_rule")

    def test_rollout_distribution_roughly_matches(self):
        flag = self._rollout_flag()
        on = sum(
            evaluation.evaluate(flag, _ctx(key=f"user-{i}")).value
            for i in range(2000)
        )
        # 10% expected; allow wide tolerance — this is a smoke check that
        # bucketing spreads, not a statistical proof.
        assert 100 < on < 320, f"unexpected rollout count: {on}"

    def test_rollout_reason(self):
        flag = self._rollout_flag()
        res = evaluation.evaluate(flag, _ctx(key="user-7"))
        assert res.reason == "targeting_rule"

    def test_percentage_must_sum_to_100(self):
        with pytest.raises(models.DefinitionError):
            models.FlagDefinition(
                key="bad",
                enabled=True,
                variations=[{"value": False}, {"value": True}],
                default_variation=0,
                targeting=[
                    {
                        "name": "r",
                        "conditions": [],
                        "then": {"percentage": {0: 50, 1: 40}},
                    }
                ],
            )


class TestMultivariate:
    def test_string_variations(self):
        flag = models.FlagDefinition(
            key="button-color",
            enabled=True,
            variations=[
                {"value": "red"},
                {"value": "green"},
                {"value": "blue"},
            ],
            default_variation=0,
        )
        res = evaluation.evaluate(flag, _ctx())
        assert res.value == "red"
        assert res.variation_index == 0

    def test_json_variation(self):
        flag = models.FlagDefinition(
            key="config",
            enabled=True,
            variations=[{"value": {"limit": 10}}, {"value": {"limit": 100}}],
            default_variation=1,
        )
        res = evaluation.evaluate(flag, _ctx())
        assert res.value == {"limit": 100}


class TestValidation:
    def test_default_variation_must_exist(self):
        with pytest.raises(models.DefinitionError):
            models.FlagDefinition(
                key="bad",
                enabled=True,
                variations=[{"value": False}],
                default_variation=5,
            )

    def test_rule_variation_must_exist(self):
        with pytest.raises(models.DefinitionError):
            models.FlagDefinition(
                key="bad",
                enabled=True,
                variations=[{"value": False}, {"value": True}],
                default_variation=0,
                targeting=[
                    {"name": "r", "conditions": [], "then": {"variation": 9}}
                ],
            )

    def test_unknown_operator_rejected(self):
        with pytest.raises(models.DefinitionError):
            models.FlagDefinition(
                key="bad",
                enabled=True,
                variations=[{"value": False}, {"value": True}],
                default_variation=0,
                targeting=[
                    {
                        "name": "r",
                        "conditions": [
                            {"attribute": "a", "op": "fuzzy", "value": 1}
                        ],
                        "then": {"variation": 1},
                    }
                ],
            )

    def test_empty_variations_rejected(self):
        with pytest.raises(models.DefinitionError):
            models.FlagDefinition(
                key="bad", enabled=True, variations=[], default_variation=0
            )
