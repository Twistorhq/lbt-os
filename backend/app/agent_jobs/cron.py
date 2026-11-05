"""TW-952 (TW-948-B4): cron semantics for persistent agent jobs.

Ported as a PATTERN from Twistor Core workforce/schedules.py
(TW-151): full five-field syntax (lists, ranges, steps), IANA
timezone matched per job, Vixie-cron day semantics (when both
day-of-month and day-of-week are restricted, EITHER matching is
a match; both 0 and 7 mean Sunday). No Core code is imported;
the semantics are re-implemented here against the lbt-os stack,
plus the next-slot computation the B1 next_fire_at column needs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import AgentJobError

_MAX_SEARCH_DAYS = 366 * 5  # a Feb-29-only schedule still resolves


def _parse_field(component: str, lo: int, hi: int) -> set[int]:
    """Parse one five-field-cron component: *, */n, a-b, a,b, single."""
    values: set[int] = set()
    for part in component.split(","):
        part = part.strip()
        if not part:
            raise AgentJobError(f"invalid cron field component {component!r}")
        try:
            if "/" in part:
                base, step_s = part.split("/", 1)
                step = int(step_s)
                if step <= 0:
                    raise AgentJobError(f"cron step must be positive in {component!r}")
                span = range(lo, hi + 1) if base == "*" else _parse_field(base, lo, hi)
                values.update(v for v in span if (v - lo) % step == 0)
            elif part == "*":
                values.update(range(lo, hi + 1))
            elif "-" in part:
                a_s, b_s = part.split("-", 1)
                values.update(range(int(a_s), int(b_s) + 1))
            else:
                values.add(int(part))
        except ValueError as exc:
            raise AgentJobError(f"invalid cron field component {component!r}") from exc
    bad = [v for v in values if not lo <= v <= hi]
    if bad:
        raise AgentJobError(f"cron value out of range in {component!r}: {bad}")
    if not values:
        raise AgentJobError(f"cron field component {component!r} selects nothing")
    return values


class CronSchedule:
    """A parsed five-field cron expression, matched in a job timezone."""

    def __init__(self, expr: str):
        fields = (expr or "").split()
        if len(fields) != 5:
            raise AgentJobError(f"invalid cron expression {expr!r}: need five fields")
        self.expr = " ".join(fields)
        self.minutes = _parse_field(fields[0], 0, 59)
        self.hours = _parse_field(fields[1], 0, 23)
        self.dom = _parse_field(fields[2], 1, 31)
        self.months = _parse_field(fields[3], 1, 12)
        self.dow = {0 if v == 7 else v for v in _parse_field(fields[4], 0, 7)}
        self._dom_restricted = fields[2].strip() != "*"
        self._dow_restricted = fields[4].strip() != "*"

    def _date_ok(self, local: datetime) -> bool:
        # datetime: Monday=0..Sunday=6; cron: Sunday=0..Saturday=6
        cron_dow = (local.weekday() + 1) % 7
        if self._dom_restricted and self._dow_restricted:
            return local.day in self.dom or cron_dow in self.dow  # Vixie: OR
        return local.day in self.dom and cron_dow in self.dow

    def matches(self, local: datetime) -> bool:
        """True when the expression matches this local minute."""
        return (
            local.minute in self.minutes
            and local.hour in self.hours
            and local.month in self.months
            and self._date_ok(local)
        )

    def next_at_or_after(self, moment: datetime, timezone_name: str) -> datetime:
        """The first matching minute at or after `moment`, as UTC.

        `moment` is an instant (aware, or naive-assumed-UTC per the
        Core clock contract); matching happens in the job timezone
        so "0 9 * * *" fires at 09:00 local regardless of the
        server timezone. The search jumps month, day, then hour
        boundaries when those fields cannot match, so a yearly
        schedule resolves without a minute-by-minute crawl.
        """
        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise AgentJobError(f"unknown timezone {timezone_name!r}") from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        local = moment.astimezone(tz).replace(second=0, microsecond=0)
        deadline = local + timedelta(days=_MAX_SEARCH_DAYS)
        while local <= deadline:
            if local.month not in self.months:
                year = local.year + (1 if local.month == 12 else 0)
                month = 1 if local.month == 12 else local.month + 1
                local = local.replace(year=year, month=month, day=1, hour=0, minute=0)
                continue
            if not self._date_ok(local):
                local = (local + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if local.hour not in self.hours:
                local = (local + timedelta(hours=1)).replace(minute=0)
                continue
            if local.minute not in self.minutes:
                local = local + timedelta(minutes=1)
                continue
            return local.astimezone(timezone.utc)
        raise AgentJobError(
            f"cron expression {self.expr!r} has no fire time within "
            f"{_MAX_SEARCH_DAYS} days"
        )


def validate_timezone(timezone_name: str) -> str:
    """The IANA name, or a loud refusal. Returns the name unchanged."""
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AgentJobError(f"unknown timezone {timezone_name!r}") from exc
    return timezone_name
