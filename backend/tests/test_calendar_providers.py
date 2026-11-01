"""TW-884: calendar provider port (fail-closed, TW-284 pattern)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.services import calendar_providers as cp

DENVER = ZoneInfo("America/Denver")
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=DENVER)


class InMemoryCalendar(cp.CalendarProvider):
    def __init__(self):
        self.events = []

    def list_events(self, start, end):
        return [e for e in self.events if e["start"] < end and start < e["end"]]

    def create_event(self, *, title, start, end, description, location=None):
        for e in self.events:
            if e["start"] < end and start < e["end"]:
                raise cp.CalendarConflict("overlap")
        event = {
            "id": f"evt-{len(self.events) + 1}",
            "title": title,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
        }
        self.events.append(event)
        return event

    def delete_event(self, event_id):
        before = len(self.events)
        self.events = [e for e in self.events if e["id"] != event_id]
        return len(self.events) < before


def test_factory_fails_closed_without_calendar_id(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", None)
    with pytest.raises(cp.CalendarNotConfigured):
        cp.get_calendar_provider()


def test_google_provider_is_a_fail_closed_seam(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "justynn@twistor.co")
    provider = cp.get_calendar_provider()
    assert isinstance(provider, cp.GoogleCalendarProvider)
    with pytest.raises(cp.CalendarNotConfigured):
        provider.list_events(T0, T0 + timedelta(hours=1))
    with pytest.raises(cp.CalendarNotConfigured):
        provider.create_event(
            title="x", start=T0, end=T0 + timedelta(minutes=20), description="y"
        )
    with pytest.raises(cp.CalendarNotConfigured):
        provider.delete_event("evt-1")


def test_delete_event_removes_and_reports():
    """TW-888: the port's delete backs booking compensation — an
    event created just before an abort must be voidable."""
    cal = InMemoryCalendar()
    event = cal.create_event(
        title="demo", start=T0, end=T0 + timedelta(minutes=20), description="d"
    )
    assert cal.delete_event(event["id"]) is True
    assert cal.events == []
    assert cal.delete_event(event["id"]) is False


def test_inmemory_conflict_detection():
    cal = InMemoryCalendar()
    cal.create_event(
        title="a", start=T0, end=T0 + timedelta(minutes=20), description=""
    )
    with pytest.raises(cp.CalendarConflict):
        cal.create_event(
            title="b",
            start=T0 + timedelta(minutes=10),
            end=T0 + timedelta(minutes=30),
            description="",
        )
    # Adjacent (touching) is not a conflict.
    cal.create_event(
        title="c",
        start=T0 + timedelta(minutes=20),
        end=T0 + timedelta(minutes=40),
        description="",
    )
    assert len(cal.events) == 2
