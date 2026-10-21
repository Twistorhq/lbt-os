"""TW-621 Twistor Flags — flag definition model + validation.

A flag is a key with an ordered list of variations, a default variation,
and an ordered list of targeting rules. First matching rule wins.
Definitions are plain JSON so the admin UI, the SDK, and the LaunchDarkly
importer all speak one language.

All semantic validation raises DefinitionError RAW (via model_post_init,
never field_validator — pydantic wraps field_validator errors in
ValidationError, which would break the fail-loud contract).

Pricing note (TW-630 wedge): evaluations are UNLIMITED on the flat
subscription — no per-context, per-seat, or per-evaluation meter. The
context meter is the thing LaunchDarkly users hate most; Twistor Flags
does not have one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class DefinitionError(ValueError):
    """A flag definition that cannot evaluate. Fails loud, never silent."""


class _StrictBase(BaseModel):
    """Base that surfaces EVERY construction failure as raw DefinitionError.

    Pydantic v2 wraps everything raised during validation — including a
    DefinitionError from a nested model's model_post_init — in
    ValidationError. __init__ normalizes back: a DefinitionError passes
    through untouched, a ValidationError is converted. The Flags contract
    is fail-loud with a single error type on every construction path.
    """

    model_config = ConfigDict(extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except DefinitionError:
            raise
        except ValidationError as e:
            raise DefinitionError(str(e)) from e


OPERATORS = (
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "ends_with",
)


class Condition(_StrictBase):
    attribute: str
    op: str
    value: Any

    def model_post_init(self, _ctx: Any) -> None:
        if not self.attribute or not self.attribute.strip():
            raise DefinitionError("condition attribute must be non-empty")
        if self.op not in OPERATORS:
            raise DefinitionError(
                f"unknown operator {self.op!r}; want one of {OPERATORS}"
            )
        if self.op in ("in", "not_in") and not isinstance(self.value, list):
            # Unwrapping a single value would silently turn set-membership
            # into substring matching ("beta" in "beta" is True).
            raise DefinitionError(
                f"operator {self.op!r} requires a list value, "
                f"got {type(self.value).__name__}"
            )


class RuleThen(_StrictBase):
    """What a matching rule serves: a fixed variation or a percentage split."""

    model_config = ConfigDict(extra="forbid")

    variation: int | None = None
    percentage: dict[int, int] | None = None

    def model_post_init(self, _ctx: Any) -> None:
        if (self.variation is None) == (self.percentage is None):
            raise DefinitionError(
                "rule 'then': exactly one of variation/percentage required"
            )
        if self.percentage is not None:
            total = sum(self.percentage.values())
            if total != 100:
                raise DefinitionError(f"percentage split must sum to 100, got {total}")
            if any(p < 0 for p in self.percentage.values()):
                raise DefinitionError("percentage split values must be >= 0")


class TargetingRule(_StrictBase):
    name: str
    conditions: list[Condition] = Field(default_factory=list)
    then: RuleThen


class ScheduleChange(_StrictBase):
    """A scheduled flip: at this UTC time, set enabled to this value."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    enabled: bool


class Variation(_StrictBase):
    value: Any
    name: str | None = None


class FlagDefinition(_StrictBase):
    key: str
    name: str | None = None
    description: str | None = None
    enabled: bool = True
    variations: list[Variation]
    default_variation: int = 0
    targeting: list[TargetingRule] = Field(default_factory=list)
    scheduled: list[ScheduleChange] = Field(default_factory=list)

    def model_post_init(self, _ctx: Any) -> None:
        if not self.key or not self.key.strip():
            raise DefinitionError("flag key must be non-empty")
        if any(c.isspace() for c in self.key):
            raise DefinitionError(f"flag key {self.key!r} must not contain whitespace")
        if not self.variations:
            raise DefinitionError("flag must declare at least one variation")
        n = len(self.variations)
        if not 0 <= self.default_variation < n:
            raise DefinitionError(
                f"default_variation {self.default_variation} out of range "
                f"for {n} variation(s)"
            )
        for rule in self.targeting:
            then = rule.then
            if then.variation is not None and not 0 <= then.variation < n:
                raise DefinitionError(
                    f"rule {rule.name!r}: variation {then.variation} out of range"
                )
            if then.percentage is not None:
                for idx in then.percentage:
                    if not 0 <= idx < n:
                        raise DefinitionError(
                            f"rule {rule.name!r}: percentage index {idx} out of range"
                        )


class EvaluationResult(_StrictBase):
    flag_key: str
    value: Any
    variation_index: int
    reason: str  # disabled | default | targeting_rule
    rule_name: str | None = None


class AuditEntry(_StrictBase):
    flag_key: str
    action: str  # create | update | delete | flip | schedule_applied
    actor: str
    summary: str
    at: datetime
