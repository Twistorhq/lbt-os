"""Calendar provider port for TW-884 demo booking (TW-284 pattern).

The outbound agent books workflow-review demos onto Justynn's Google
Calendar. Slice 1 ships the port plus a fail-closed Google seam: until
the Calendar API client is wired at go-live (credentials are Justynn's
manual step), the Google provider raises CalendarNotConfigured on every
operation, exactly like the TW-284 voice providers fail closed without
credentials. Tests inject an in-memory provider; the outbound service
degrades a booking failure to a callback disposition instead of
crashing a live conversation (self-healing rule).
"""

from __future__ import annotations

import abc
from datetime import datetime


class CalendarNotConfigured(RuntimeError):
    """No calendar is configured/wired; booking must fail closed."""


class CalendarConflict(RuntimeError):
    """The requested slot overlaps an existing event."""


class CalendarProvider(abc.ABC):
    """Minimal calendar surface the outbound agent needs."""

    @abc.abstractmethod
    def list_events(self, start: datetime, end: datetime) -> list[dict]:
        """Events overlapping [start, end). Each: id/title/start/end."""

    @abc.abstractmethod
    def create_event(
        self,
        *,
        title: str,
        start: datetime,
        end: datetime,
        description: str,
        location: str | None = None,
    ) -> dict:
        """Create an event; raise CalendarConflict on overlap."""

    @abc.abstractmethod
    def delete_event(self, event_id: str) -> bool:
        """Delete a created event; True when one was removed.

        Exists for TW-888 booking compensation: an event created in
        the sliver before a mid-call revocation/kill-switch abort
        becomes visible must be voidable, so no phantom booking ever
        survives an aborted call."""


class GoogleCalendarProvider(CalendarProvider):
    """Fail-closed seam for Justynn's Google Calendar.

    The calendar id comes from settings.google_calendar_id. The API
    client wiring (service-account / OAuth token from the secure
    handoff) is go-live work; until it lands, every operation raises
    CalendarNotConfigured so no booking is ever faked.
    """

    def __init__(self, calendar_id: str):
        self.calendar_id = calendar_id

    def _not_wired(self):
        raise CalendarNotConfigured(
            "Google Calendar API client is not wired yet (go-live step); "
            "booking fails closed"
        )

    def list_events(self, start: datetime, end: datetime) -> list[dict]:
        self._not_wired()

    def create_event(
        self,
        *,
        title: str,
        start: datetime,
        end: datetime,
        description: str,
        location: str | None = None,
    ) -> dict:
        self._not_wired()

    def delete_event(self, event_id: str) -> bool:
        self._not_wired()


def get_calendar_provider() -> CalendarProvider:
    """Factory. Raises CalendarNotConfigured when no calendar is set."""
    from ..config import settings

    calendar_id = getattr(settings, "google_calendar_id", None)
    if not calendar_id:
        raise CalendarNotConfigured(
            "google_calendar_id is not configured; demo booking fails closed"
        )
    return GoogleCalendarProvider(calendar_id)
