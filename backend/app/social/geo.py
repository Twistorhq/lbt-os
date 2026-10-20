"""TW-650 Twistor Social — geo helpers (pure functions).

Doctrine (spec Section 7.1): the device evaluates the geofence and
sends a verdict; raw coordinates never cross the wire and no
coordinate column exists on rsn_checkins. This module validates
arrival verdict payloads fail-closed and computes reveal windows.

Reveal windows (spec Section 8 + TW-648 design delta): venue events
reveal inside a 48-hour window before start; home events inside the
3-hour home window — opens 3 hours before start, closes at event
end. A home address is the most sensitive location class in the
product, so it gets the tightest window in the system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

EARTH_RADIUS_M = 6_371_000.0

REVEAL_WINDOWS = {
    "venue": timedelta(hours=48),
    "home": timedelta(hours=3),
}

ACCURACY_BUCKETS = ("coarse", "fine")
_ARRIVAL_KEYS = {"within_geofence", "accuracy_bucket"}


class GeoError(Exception):
    """Fail-loud geo validation error. The router maps these to 4xx."""


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters. Used server-side only for
    venue/series geometry (e.g. district pins), never on member
    check-ins: no member coordinate is ever available to this code."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def is_within_radius(distance_m: float, radius_m: float) -> bool:
    return distance_m <= radius_m


@dataclass(frozen=True)
class RevealWindow:
    opens_at: datetime
    closes_at: datetime
    is_open: bool


def reveal_window(
    venue_type: str,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> RevealWindow:
    """The reveal window for an event. Unknown venue types fail
    closed: there is no default window for an unrecognized class."""
    lead = REVEAL_WINDOWS.get(venue_type)
    if lead is None:
        raise GeoError(f"unknown venue_type: {venue_type!r}")
    opens_at = starts_at - lead
    return RevealWindow(
        opens_at=opens_at,
        closes_at=ends_at,
        is_open=opens_at <= now <= ends_at,
    )


@dataclass(frozen=True)
class ArrivalVerdict:
    within_geofence: bool
    accuracy_bucket: str


def validate_arrival_payload(payload: dict) -> ArrivalVerdict:
    """Validate a client-submitted arrival verdict, fail-closed.

    The payload must contain exactly the verdict and an accuracy
    bucket. Any extra key (a coordinate, a device id, anything) is
    refused: the wire contract is verdict-only, and a payload that
    carries more than the verdict is not a verdict we accept.
    """
    keys = set(payload)
    if keys != _ARRIVAL_KEYS:
        raise GeoError(
            f"arrival payload keys must be exactly {sorted(_ARRIVAL_KEYS)}, "
            f"got {sorted(keys)}"
        )
    if not isinstance(payload["within_geofence"], bool):
        raise GeoError("within_geofence must be a boolean")
    if payload["accuracy_bucket"] not in ACCURACY_BUCKETS:
        raise GeoError(
            f"accuracy_bucket must be one of {ACCURACY_BUCKETS}, "
            f"got {payload['accuracy_bucket']!r}"
        )
    return ArrivalVerdict(
        within_geofence=payload["within_geofence"],
        accuracy_bucket=payload["accuracy_bucket"],
    )
