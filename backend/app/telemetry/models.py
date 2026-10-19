"""TW-622 Twistor Telemetry — Pydantic models.

Fail-loud validation on every construction path. Events carry a hard cap
on property count so one runaway SDK cannot poison the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_PROPERTIES = 100
MAX_BATCH = 1000
MAX_FUNNEL_STEPS = 8

INSIGHT_KINDS = ("trend", "funnel", "retention", "breakdown")
BUCKETS = ("hour", "day", "week")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_json_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _check_jsonable(v: Any, path: str = "$") -> None:
    if isinstance(v, dict):
        for k, sub in v.items():
            if not isinstance(k, str):
                raise ValueError(f"property key at {path} must be a string")
            _check_jsonable(sub, f"{path}.{k}")
    elif isinstance(v, list):
        for i, sub in enumerate(v):
            _check_jsonable(sub, f"{path}[{i}]")
    elif not _is_json_scalar(v):
        raise ValueError(f"property value at {path} is not JSON-serializable")


class TelemetryEvent(BaseModel):
    event: str = Field(min_length=1, max_length=128)
    distinct_id: str = Field(min_length=1, max_length=256)
    properties: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_utcnow)

    @field_validator("event")
    @classmethod
    def _strip_event(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("event name must not be blank")
        return v

    @field_validator("occurred_at")
    @classmethod
    def _coerce_utc(cls, v: datetime) -> datetime:
        # Naive datetimes are assumed UTC (documented) — never server-local,
        # so buckets don't shift with the server's timezone. Fail-loud on
        # anything else happens in the query layer via _as_utc.
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("properties")
    @classmethod
    def _check_properties(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > MAX_PROPERTIES:
            raise ValueError(f"max {MAX_PROPERTIES} properties per event")
        _check_jsonable(v)
        return v


class CaptureBatch(BaseModel):
    events: list[TelemetryEvent] = Field(min_length=1, max_length=MAX_BATCH)


class TrendQuery(BaseModel):
    event: str = Field(min_length=1, max_length=128)
    start: datetime
    end: datetime
    bucket: Literal["hour", "day", "week"] = "day"
    breakdown_property: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _start_before_end(self) -> "TrendQuery":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class FunnelStep(BaseModel):
    event: str = Field(min_length=1, max_length=128)


class FunnelDefinition(BaseModel):
    steps: list[FunnelStep] = Field(min_length=2, max_length=MAX_FUNNEL_STEPS)
    window_hours: int = Field(default=72, ge=1, le=720)


class FunnelQuery(BaseModel):
    funnel: FunnelDefinition
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _start_before_end(self) -> "FunnelQuery":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class RetentionQuery(BaseModel):
    cohort_event: str = Field(min_length=1, max_length=128)
    return_event: str = Field(min_length=1, max_length=128)
    days: int = Field(ge=1, le=90)
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _start_before_end(self) -> "RetentionQuery":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class BreakdownQuery(BaseModel):
    event: str = Field(min_length=1, max_length=128)
    property: str = Field(min_length=1, max_length=128)
    start: datetime
    end: datetime
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def _start_before_end(self) -> "BreakdownQuery":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class InsightDefinition(BaseModel):
    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: Literal["trend", "funnel", "retention", "breakdown"]
    definition: dict[str, Any]

    @field_validator("definition")
    @classmethod
    def _check_definition(cls, v: dict[str, Any]) -> dict[str, Any]:
        _check_jsonable(v)
        return v
