"""TW-651: geocoding (spec 10.2).

Coordinates come from, in priority order: the source itself, the
venue cache (an address is geocoded once in the platform's life), or
the rate-limited Nominatim queue. A geocoding failure never blocks a
listing: the card ships without a preview and the job retries later.
"""

import pytest

from app.social.events_index import geocode
from app.social.events_index.models import VenueFacts


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def test_rate_limiter_enforces_min_interval():
    clock = FakeClock()
    limiter = geocode.RateLimiter(
        min_interval_seconds=1.1, clock=clock.now, sleep=clock.sleep
    )
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert clock.sleeps == [1.1, 1.1]


def test_source_coordinates_win_and_need_no_provider():
    facts = VenueFacts(name="Hall", latitude=39.75, longitude=-104.99)
    resolved, provider = geocode.resolve_venue(facts, cached=None, provider=None)
    assert provider == "source"
    assert resolved.latitude == 39.75


def test_venue_cache_beats_provider():
    facts = VenueFacts(name="Hall", street_address="1 Main St", city="Denver")
    cached = VenueFacts(
        name="Hall", street_address="1 Main St", city="Denver",
        latitude=39.7, longitude=-105.0,
    )

    def boom(query):
        raise AssertionError("provider must not be called on a cache hit")

    resolved, provider = geocode.resolve_venue(facts, cached=cached, provider=boom)
    assert provider == "venue_cache"
    assert resolved.latitude == 39.7


def test_provider_result_used_on_cache_miss():
    facts = VenueFacts(name="Hall", street_address="1 Main St", city="Denver")
    calls = []

    class Provider:
        name = "nominatim"

        def geocode(self, query):
            calls.append(query)
            return (39.71, -105.01)

    resolved, provider = geocode.resolve_venue(facts, cached=None, provider=Provider())
    assert provider == "nominatim"
    assert resolved.latitude == pytest.approx(39.71)
    assert "1 Main St" in calls[0]


def test_provider_failure_never_blocks():
    facts = VenueFacts(name="Hall", street_address="1 Main St", city="Denver")

    class Down:
        name = "nominatim"

        def geocode(self, query):
            raise ConnectionError("nominatim unreachable")

    resolved, provider = geocode.resolve_venue(facts, cached=None, provider=Down())
    assert provider is None
    assert resolved.latitude is None
    assert resolved.name == "Hall"


def test_nominatim_provider_parses_first_result():
    seen = {}

    def fake_get(url, headers):
        seen["url"] = url
        seen["headers"] = headers
        return [{"lat": "39.7589", "lon": "-104.9998", "display_name": "Denver"}]

    clock = FakeClock()
    limiter = geocode.RateLimiter(clock=clock.now, sleep=clock.sleep)
    provider = geocode.NominatimProvider(http_get=fake_get, limiter=limiter)
    assert provider.geocode("2901 Inca St, Denver") == (39.7589, -104.9998)
    # Nominatim usage policy: the application must identify itself.
    assert "Twistor" in seen["headers"]["User-Agent"]


def test_nominatim_provider_empty_result_is_none():
    provider = geocode.NominatimProvider(
        http_get=lambda url, headers: [],
        limiter=geocode.RateLimiter(clock=lambda: 0.0, sleep=lambda s: None),
    )
    assert provider.geocode("Nowhere") is None
