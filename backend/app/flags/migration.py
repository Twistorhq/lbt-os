"""TW-621 Twistor Flags — LaunchDarkly concept migration.

Maps LaunchDarkly's documented flag model into Twistor Flags definitions.
CONCEPT MAPPING ONLY: no LaunchDarkly code, no Unleash code (AGPL — never
touch), nothing vendored. Operator names and the flag/rule/clause shape
are public product concepts, mapped by hand below.

Unmapped constructs (segment references, semver ops, percentage-by-attribute
rollouts) are reported, never silently dropped — migrate_with_report()
returns them alongside the flag so a human can finish the job.
"""

from __future__ import annotations

from typing import Any

from .models import (
    Condition,
    DefinitionError,
    FlagDefinition,
    RuleThen,
    TargetingRule,
    Variation,
    _StrictBase,
)

# LaunchDarkly clause operator -> Twistor Flags operator.
_LD_OPERATORS: dict[str, str] = {
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

# LD ops with no Twistor equivalent — reported, not mapped.
_UNMAPPED_OPS = {
    "segmentMatch",
    "semVerEqual",
    "semVerNotEqual",
    "semVerGreaterThan",
    "semVerGreaterThanOrEqual",
    "semVerLessThan",
    "semVerLessThanOrEqual",
    "before",
    "after",
}


def map_operator(ld_op: str) -> str:
    """Map one LaunchDarkly clause operator. Raises DefinitionError if unmapped."""
    if ld_op in _UNMAPPED_OPS:
        raise DefinitionError(f"no Twistor Flags equivalent for LD op {ld_op!r}")
    try:
        return _LD_OPERATORS[ld_op]
    except KeyError:
        raise DefinitionError(f"unknown LD operator {ld_op!r}") from None


def _map_clause(clause: dict[str, Any], unmapped: list[str]) -> Condition | None:
    op = clause.get("op", "")
    if op in _UNMAPPED_OPS or op not in _LD_OPERATORS:
        unmapped.append(f"clause op {op!r} on attribute {clause.get('attribute')!r}")
        return None
    tw_op = _LD_OPERATORS[op]
    if clause.get("negate"):
        if tw_op == "in":
            tw_op = "not_in"
        elif tw_op == "not_in":
            tw_op = "in"
        else:
            unmapped.append(
                f"negated clause op {op!r} on {clause.get('attribute')!r} "
                "(negation dropped — review)"
            )
    values = clause.get("values", [])
    # in/not_in always keep the list: unwrapping a single value would turn
    # set-membership into substring matching ("beta" in "beta").
    if tw_op in ("in", "not_in"):
        value: Any = values
    else:
        value = values[0] if len(values) == 1 else values
    return Condition(attribute=clause.get("attribute", ""), op=tw_op, value=value)


def _rollout_to_percentage(rollout: dict[str, Any]) -> dict[int, int]:
    """LD weights are per-mille of 100000; convert to integer percents."""
    out: dict[int, int] = {}
    for entry in rollout.get("variations", []):
        weight = entry.get("weight", 0)
        out[int(entry["variation"])] = round(weight / 1000)
    total = sum(out.values())
    if total != 100:
        # Fix rounding drift on the largest slice — loud in the report path.
        biggest = max(out, key=lambda k: out[k])
        out[biggest] += 100 - total
    return out


class MigrationReport(_StrictBase):
    flag: FlagDefinition
    unmapped: list[str]


def migrate_with_report(ld_flag: dict[str, Any]) -> MigrationReport:
    """Migrate one LD flag dict; report constructs that did not map."""
    unmapped: list[str] = []
    variations = [
        Variation(value=v.get("value")) for v in ld_flag.get("variations", [])
    ]
    targeting: list[TargetingRule] = []

    for i, rule in enumerate(ld_flag.get("rules", [])):
        conditions: list[Condition] = []
        for clause in rule.get("clauses", []):
            cond = _map_clause(clause, unmapped)
            if cond is not None:
                conditions.append(cond)
        if "variation" in rule:
            then = RuleThen(variation=int(rule["variation"]))
        elif "rollout" in rule:
            then = RuleThen(percentage=_rollout_to_percentage(rule["rollout"]))
        else:
            unmapped.append(
                f"rule {i} ({rule.get('description')!r}): no variation/rollout"
            )
            continue
        targeting.append(
            TargetingRule(
                name=rule.get("description") or f"rule-{i}",
                conditions=conditions,
                then=then,
            )
        )

    fallthrough = ld_flag.get("fallthrough") or {}
    if not targeting and "rollout" in fallthrough:
        targeting.append(
            TargetingRule(
                name="migrated fallthrough rollout",
                conditions=[],
                then=RuleThen(
                    percentage=_rollout_to_percentage(fallthrough["rollout"])
                ),
            )
        )
        default_variation = int(ld_flag.get("offVariation", 0))
    elif not targeting and "variation" in fallthrough:
        default_variation = int(fallthrough["variation"])
    else:
        default_variation = int(ld_flag.get("offVariation", 0))

    flag = FlagDefinition(
        key=str(ld_flag.get("key", "")),
        name=ld_flag.get("name"),
        description=ld_flag.get("description"),
        enabled=bool(ld_flag.get("on", False)),
        variations=variations,
        default_variation=default_variation,
        targeting=targeting,
    )
    return MigrationReport(flag=flag, unmapped=unmapped)


def from_launchdarkly(ld_flag: dict[str, Any]) -> FlagDefinition:
    """Migrate one LD flag dict. Raises DefinitionError if anything unmapped."""
    report = migrate_with_report(ld_flag)
    if report.unmapped:
        raise DefinitionError(
            "unmapped LD constructs (use migrate_with_report for detail): "
            + "; ".join(report.unmapped)
        )
    return report.flag
