"""TW-952 (TW-948-B4): job definition shapes for the job service.

A job definition is a self-contained prompt plus an ordered
skill list plus exactly one schedule: a five-field cron
expression OR a one-shot instant, never both, never neither.
Validation raises AgentJobError raw (the StrictBase pattern the
neighboring ledgers use), so a malformed definition fails loud
at construction, never inside the ticker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class AgentJobError(ValueError):
    """A job statement that cannot be represented. Fails loud."""


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except AgentJobError:
            raise
        except ValidationError as e:
            raise AgentJobError(str(e)) from e


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


DEFAULT_CATCH_UP_WINDOW_MINUTES = 60
MAX_CATCH_UP_WINDOW_MINUTES = 7 * 24 * 60


class JobSpec(_StrictBase):
    """A new persistent job definition (management surface input)."""

    name: str
    prompt: str
    skill_keys: list[str] = []
    cron_expr: str | None = None
    one_shot_at: datetime | None = None
    timezone: str = "UTC"
    catch_up_window_minutes: int = DEFAULT_CATCH_UP_WINDOW_MINUTES

    def model_post_init(self, _ctx: Any) -> None:
        # Local import: cron needs AgentJobError from this module.
        from .cron import CronSchedule, validate_timezone

        if not self.name.strip():
            raise AgentJobError("job name must be non-empty")
        if not self.prompt.strip():
            raise AgentJobError("job prompt must be non-empty (self-contained)")
        if (self.cron_expr is None) == (self.one_shot_at is None):
            raise AgentJobError(
                "a job needs exactly one schedule: cron_expr or one_shot_at"
            )
        if self.cron_expr is not None:
            CronSchedule(self.cron_expr)  # raises on invalid syntax
        validate_timezone(self.timezone)
        if not 0 <= self.catch_up_window_minutes <= MAX_CATCH_UP_WINDOW_MINUTES:
            raise AgentJobError(
                "catch_up_window_minutes must be between 0 and "
                f"{MAX_CATCH_UP_WINDOW_MINUTES}"
            )
        if self.one_shot_at is not None:
            self.one_shot_at = _as_utc(self.one_shot_at)


class JobEdit(_StrictBase):
    """A partial edit of a job definition. History is never editable."""

    name: str | None = None
    prompt: str | None = None
    skill_keys: list[str] | None = None
    cron_expr: str | None = None
    one_shot_at: datetime | None = None
    timezone: str | None = None
    catch_up_window_minutes: int | None = None

    def model_post_init(self, _ctx: Any) -> None:
        from .cron import CronSchedule, validate_timezone

        if self.name is not None and not self.name.strip():
            raise AgentJobError("job name must be non-empty")
        if self.prompt is not None and not self.prompt.strip():
            raise AgentJobError("job prompt must be non-empty (self-contained)")
        if self.cron_expr is not None and self.one_shot_at is not None:
            raise AgentJobError("an edit sets cron_expr or one_shot_at, not both")
        if self.cron_expr is not None:
            CronSchedule(self.cron_expr)
        if self.timezone is not None:
            validate_timezone(self.timezone)
        if self.catch_up_window_minutes is not None and not (
            0 <= self.catch_up_window_minutes <= MAX_CATCH_UP_WINDOW_MINUTES
        ):
            raise AgentJobError("catch_up_window_minutes out of range")
        if self.one_shot_at is not None:
            self.one_shot_at = _as_utc(self.one_shot_at)
