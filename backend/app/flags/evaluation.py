"""TW-621 Twistor Flags — evaluation engine.

Pure functions: no DB, no network, no clock. The SDK hot path calls
evaluate() with a cached FlagDefinition. Deterministic bucketing via
sha256 so a targeting key always lands in the same bucket.

Fail-closed: a missing context attribute never matches a condition;
an unknown value type never matches a comparison.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .models import Condition, EvaluationResult, FlagDefinition

# Buckets are 0..9999 so percentages map 1:1 to integer ranges.
_BUCKETS = 10000


def _bucket(flag_key: str, targeting_key: str) -> int:
    """Deterministic bucket for a (flag, targeting key) pair."""
    digest = hashlib.sha256(f"{flag_key}:{targeting_key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % _BUCKETS


def _match_condition(cond: Condition, attributes: dict[str, Any]) -> bool:
    if cond.attribute not in attributes:
        return False
    actual = attributes[cond.attribute]
    want = cond.value
    op = cond.op
    try:
        if op == "eq":
            return bool(actual == want)
        if op == "neq":
            return bool(actual != want)
        if op == "in":
            return bool(actual in want)
        if op == "not_in":
            return bool(actual not in want)
        if op == "contains":
            return bool(want in actual)
        if op == "starts_with":
            return (
                isinstance(actual, str)
                and isinstance(want, str)
                and actual.startswith(want)
            )
        if op == "ends_with":
            return (
                isinstance(actual, str)
                and isinstance(want, str)
                and actual.endswith(want)
            )
        if op in ("gt", "gte", "lt", "lte"):
            if isinstance(actual, bool) or isinstance(want, bool):
                return False
            if not isinstance(actual, (int, float)) or not isinstance(
                want, (int, float)
            ):
                return False
            if op == "gt":
                return bool(actual > want)
            if op == "gte":
                return bool(actual >= want)
            if op == "lt":
                return bool(actual < want)
            return bool(actual <= want)
    except TypeError:
        return False
    return False  # unknown op — validated at definition time, never here


def _pick_percentage(flag_key: str, targeting_key: str, split: dict[int, int]) -> int:
    bucket = _bucket(flag_key, targeting_key)
    cumulative = 0
    # Sorted by variation index for a stable, documented layout.
    for idx in sorted(split):
        cumulative += split[idx] * (_BUCKETS // 100)
        if bucket < cumulative:
            return idx
    return sorted(split)[-1]  # bucket == 9999 edge lands on the last slice


def evaluate(flag: FlagDefinition, context: dict[str, Any]) -> EvaluationResult:
    """Evaluate a flag for a context.

    context: {"targeting_key": str, "attributes": {name: value}}.
    First matching targeting rule wins; percentage splits bucket on the
    targeting key. A missing targeting key buckets on the empty string —
    still deterministic, never an error.
    """
    if not flag.enabled:
        idx = flag.default_variation
        return EvaluationResult(
            flag_key=flag.key,
            value=flag.variations[idx].value,
            variation_index=idx,
            reason="disabled",
        )

    targeting_key = str(context.get("targeting_key") or "")
    attributes = context.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}

    for rule in flag.targeting:
        if all(_match_condition(c, attributes) for c in rule.conditions):
            then = rule.then
            if then.variation is not None:
                idx = then.variation
            else:
                assert then.percentage is not None
                idx = _pick_percentage(flag.key, targeting_key, then.percentage)
            return EvaluationResult(
                flag_key=flag.key,
                value=flag.variations[idx].value,
                variation_index=idx,
                reason="targeting_rule",
                rule_name=rule.name,
            )

    idx = flag.default_variation
    return EvaluationResult(
        flag_key=flag.key,
        value=flag.variations[idx].value,
        variation_index=idx,
        reason="default",
    )
