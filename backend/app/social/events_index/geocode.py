"""TW-651: geocoding behind a provider interface (spec 10.2).

Priority order: (1) coordinates the source itself published, stored
as source facts with provenance; (2) the venue cache, keyed by
address fingerprint, so an address is geocoded once in the life of
the platform; (3) Nominatim, behind a rate limiter honoring its
usage policy (one request per second maximum, results cached, the
application identifies itself). A geocoding failure never blocks a
listing: the venue ships without coordinates and a later run
retries. The provider interface is the swap point for a self-hosted
Nominatim or Photon instance when volume outgrows polite public use.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Callable

from .models import VenueFacts

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "TwistorSocial/0.1 (events index; contact holla@twistor.co)"


class RateLimiter:
    """Spaces acquisitions at least min_interval_seconds apart."""

    def __init__(
        self,
        min_interval_seconds: float = 1.1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval_seconds = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def acquire(self) -> None:
        now = self._clock()
        if self._last is not None:
            wait = self.min_interval_seconds - (now - self._last)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last = now


class NominatimProvider:
    name = "nominatim"

    def __init__(
        self,
        http_get: Callable[[str, dict[str, str]], Any],
        limiter: RateLimiter,
    ):
        self._http_get = http_get
        self._limiter = limiter

    def geocode(self, query: str) -> tuple[float, float] | None:
        self._limiter.acquire()
        url = (
            NOMINATIM_SEARCH_URL
            + "?"
            + urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": "1"})
        )
        results = self._http_get(url, {"User-Agent": USER_AGENT})
        if not results:
            return None
        first = results[0]
        try:
            return (float(first["lat"]), float(first["lon"]))
        except (KeyError, TypeError, ValueError):
            return None


def resolve_venue(
    facts: VenueFacts,
    cached: VenueFacts | None,
    provider: Any,
) -> tuple[VenueFacts, str | None]:
    """Resolve coordinates for a venue; returns (facts, provider_name).

    provider_name is None when no coordinates could be resolved; the
    caller stores the venue anyway and a later run retries.
    """
    if facts.latitude is not None and facts.longitude is not None:
        return facts, "source"
    if (
        cached is not None
        and cached.latitude is not None
        and cached.longitude is not None
    ):
        facts.latitude = cached.latitude
        facts.longitude = cached.longitude
        return facts, "venue_cache"
    if provider is None:
        return facts, None
    query_parts = [p for p in (facts.street_address, facts.city) if p]
    if not query_parts and facts.name:
        query_parts = [facts.name]
    if not query_parts:
        return facts, None
    query = ", ".join(query_parts)
    try:
        if hasattr(provider, "geocode"):
            result = provider.geocode(query)
            provider_name = getattr(provider, "name", "provider")
        else:
            result = provider(query)
            provider_name = getattr(provider, "name", "provider")
    except Exception:  # noqa: BLE001 - a geocoder outage never blocks a listing
        return facts, None
    if result is None:
        return facts, None
    facts.latitude, facts.longitude = result
    return facts, provider_name
