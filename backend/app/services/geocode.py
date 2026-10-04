"""Keyless geocoding for the Trade View real backend (TW-301, FOSS-first).

Vendored from twistor-core's territory_intel/geocode.py (TW-277) —
"Prepared by Twistor Holdings LLC", stdlib only, no API keys.
Photon (Komoot) first, Nominatim fallback.

Adds a small in-memory TTL cache so the pins endpoint never hammers a
free geocoder for the same address twice in one process lifetime.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

# Vendored-from attribution: twistor-core/src/twistor_core/territory_intel/geocode.py
USER_AGENT = "TwistorTradeView/1.0 (trade-view pins; contact: Twistor Holdings LLC)"

_PHOTON_URL = "https://photon.komoot.io/api/"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lon: float
    display_name: str
    source: str  # "photon" | "nominatim"


def _get(url: str, timeout: float) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def _from_photon(address: str, timeout: float) -> GeocodeResult | None:
    url = _PHOTON_URL + "?" + urllib.parse.urlencode({"q": address, "limit": 1})
    raw = _get(url, timeout)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        feat = data["features"][0]
        lon, lat = feat["geometry"]["coordinates"][:2]
        props = feat.get("properties", {})
        name = props.get("name") or address
        city = props.get("city") or props.get("locality") or ""
        state = props.get("state") or ""
        display = ", ".join(p for p in (name, city, state) if p)
        return GeocodeResult(
            lat=float(lat), lon=float(lon), display_name=display, source="photon"
        )
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _from_nominatim(address: str, timeout: float) -> GeocodeResult | None:
    url = (
        _NOMINATIM_URL
        + "?"
        + urllib.parse.urlencode({"q": address, "format": "json", "limit": 1})
    )
    raw = _get(url, timeout)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        item = data[0]
        return GeocodeResult(
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            display_name=item.get("display_name", address),
            source="nominatim",
        )
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def geocode(address: str | None, *, timeout: float = 10.0) -> GeocodeResult | None:
    """Geocode an address with zero keys. Photon first, Nominatim fallback."""
    if not address or not address.strip():
        return None
    return _from_photon(address.strip(), timeout) or _from_nominatim(
        address.strip(), timeout
    )


class GeocodeCache:
    """In-memory TTL cache over geocode().

    Keyed by normalized address. A failed lookup caches as a miss for a
    shorter window so one bad address doesn't re-hit the network on every
    pins request, but can still recover later.
    """

    def __init__(
        self,
        ttl_s: float = 86400.0,
        miss_ttl_s: float = 3600.0,
        max_entries: int = 2000,
    ):
        self._ttl = ttl_s
        self._miss_ttl = miss_ttl_s
        self._max_entries = max_entries
        # Insertion-ordered dict used as an LRU: hits move_to_end, inserts
        # evict the least-recently-used entry when full (NIT 1, TW-301).
        self._store: OrderedDict[str, tuple[float, GeocodeResult | None]] = (
            OrderedDict()
        )

    @staticmethod
    def _key(address: str) -> str:
        return " ".join(address.strip().lower().split())

    def _evict_if_full(self) -> None:
        while len(self._store) >= self._max_entries:
            self._store.popitem(last=False)

    def get(
        self, address: str | None, *, timeout: float = 10.0
    ) -> GeocodeResult | None:
        if not address or not address.strip():
            return None
        key = self._key(address)
        now = time.monotonic()
        hit = self._store.get(key)
        if hit is not None:
            expires_at, result = hit
            if now < expires_at:
                self._store.move_to_end(key)
                return result
            del self._store[key]
        result = geocode(address, timeout=timeout)
        ttl = self._ttl if result is not None else self._miss_ttl
        self._evict_if_full()
        self._store[key] = (now + ttl, result)
        return result


# Process-wide cache shared by the tradeview router. Geocoding is
# deterministic per address; a restart simply re-warms from the free APIs.
_cache = GeocodeCache()


def geocode_cached(
    address: str | None, *, timeout: float = 10.0
) -> GeocodeResult | None:
    """Process-cached geocode for request paths. Never raises."""
    try:
        return _cache.get(address, timeout=timeout)
    except Exception:
        return None
