"""TW-621 Twistor Flags — LaunchDarkly concept migration (TDD RED).

Maps LaunchDarkly flag concepts (public, documented model) into Twistor
Flags definitions. This is concept mapping, not code copying: no
LaunchDarkly or Unleash code is vendored anywhere in this repo.
"""

import pytest

from app.flags import evaluation, migration, models


def _ld_boolean_flag():
    # Shape mirrors LaunchDarkly's documented flag model: variations,
    # offVariation, fallthrough, rules with clauses. Values here are
    # synthetic fixtures, not copied from any real account.
    return {
        "key": "new-checkout",
        "name": "New checkout",
        "on": True,
        "variations": [{"value": False}, {"value": True}],
        "offVariation": 0,
        "fallthrough": {"variation": 0},
        "rules": [
            {
                "description": "beta users",
                "clauses": [
                    {
                        "attribute": "plan",
                        "op": "in",
                        "values": ["beta"],
                        "negate": False,
                    }
                ],
                "variation": 1,
            }
        ],
    }


class TestBasicMapping:
    def test_key_name_variations_carry_over(self):
        flag = migration.from_launchdarkly(_ld_boolean_flag())
        assert flag.key == "new-checkout"
        assert flag.name == "New checkout"
        assert [v.value for v in flag.variations] == [False, True]

    def test_off_variation_becomes_default(self):
        ld = _ld_boolean_flag()
        ld["offVariation"] = 1
        flag = migration.from_launchdarkly(ld)
        assert flag.default_variation == 1

    def test_ld_off_maps_to_disabled(self):
        ld = _ld_boolean_flag()
        ld["on"] = False
        flag = migration.from_launchdarkly(ld)
        assert flag.enabled is False

    def test_fallthrough_variation_becomes_default_when_no_rules(self):
        ld = _ld_boolean_flag()
        ld["rules"] = []
        ld["fallthrough"] = {"variation": 1}
        flag = migration.from_launchdarkly(ld)
        assert flag.default_variation == 1
        assert flag.targeting == []

    def test_fallthrough_rollout_becomes_percentage_rule(self):
        ld = _ld_boolean_flag()
        ld["rules"] = []
        ld["fallthrough"] = {
            "rollout": {
                "variations": [
                    {"variation": 0, "weight": 90000},
                    {"variation": 1, "weight": 10000},
                ]
            }
        }
        flag = migration.from_launchdarkly(ld)
        assert len(flag.targeting) == 1
        then = flag.targeting[0].then
        assert then.percentage == {0: 90, 1: 10}


class TestClauseMapping:
    def test_in_clause_becomes_in_condition(self):
        flag = migration.from_launchdarkly(_ld_boolean_flag())
        rule = flag.targeting[0]
        assert rule.name == "beta users"
        assert len(rule.conditions) == 1
        cond = rule.conditions[0]
        assert (cond.attribute, cond.op, cond.value) == ("plan", "in", ["beta"])
        assert rule.then.variation == 1

    def test_negated_clause_becomes_not_in(self):
        ld = _ld_boolean_flag()
        ld["rules"][0]["clauses"][0]["negate"] = True
        flag = migration.from_launchdarkly(ld)
        assert flag.targeting[0].conditions[0].op == "not_in"

    def test_ld_operators_map(self):
        cases = {
            "in": "in",
            "notIn": "not_in",
            "equal": "eq",
            "notEqual": "neq",
            "greaterThan": "gt",
            "greaterThanOrEqual": "gte",
            "lessThan": "lt",
            "lessThanOrEqual": "lte",
            "contains": "contains",
            "startsWith": "starts_with",
            "endsWith": "ends_with",
        }
        for ld_op, our_op in cases.items():
            assert migration.map_operator(ld_op) == our_op

    def test_unknown_operator_raises(self):
        with pytest.raises(models.DefinitionError):
            migration.map_operator("semVerGreaterThan")


class TestMigratedFlagEvaluates:
    def test_migrated_flag_serves_rule_then_default(self):
        flag = migration.from_launchdarkly(_ld_boolean_flag())
        assert (
            evaluation.evaluate(
                flag, {"targeting_key": "u1", "attributes": {"plan": "beta"}}
            ).value
            is True
        )
        assert (
            evaluation.evaluate(
                flag, {"targeting_key": "u2", "attributes": {"plan": "free"}}
            ).value
            is False
        )

    def test_migrated_flag_report_lists_unmapped(self):
        ld = _ld_boolean_flag()
        ld["rules"][0]["clauses"].append(
            {
                "attribute": "ver",
                "op": "semVerGreaterThan",
                "values": ["1.0"],
                "negate": False,
            }
        )
        report = migration.migrate_with_report(ld)
        assert report.flag.key == "new-checkout"
        assert len(report.unmapped) == 1
        assert "semVerGreaterThan" in report.unmapped[0]
        # The mappable clause still landed.
        assert len(report.flag.targeting[0].conditions) == 1
